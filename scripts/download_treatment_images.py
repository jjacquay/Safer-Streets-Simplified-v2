"""
Download all treatment images to assets/treatments/ using a layered fallback:
  1. Direct fetch with a browser User-Agent + Referer
  2. Wayback Machine 'id_' raw-asset endpoint (proven to bypass FHWA CDN block)

Updates each treatment's `image_local` field. Skips treatments that already
have a working local image.

Usage: python3 scripts/download_treatment_images.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, quote

import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ASSETS = ROOT / "assets" / "treatments"
ASSETS.mkdir(parents=True, exist_ok=True)
LEGACY_PSC = ROOT / "assets" / "psc"  # the v4 download location, used for FHWA-10

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch(url, referer=None, timeout=30):
    """Return (status, content_type, body_bytes) or raise."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        **({"Referer": referer} if referer else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def ext_from(url, content_type):
    p = urlparse(url).path.lower()
    for e in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"):
        if p.endswith(e):
            return e
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "gif" in content_type:
        return ".gif"
    if "svg" in content_type:
        return ".svg"
    return ".bin"


def try_direct(url, referer):
    try:
        status, ct, body = fetch(url, referer=referer, timeout=15)
        if status == 200 and ("image" in ct or len(body) > 5_000) and not body[:200].lower().startswith(b"<!doctype"):
            return body, ct
    except Exception as e:
        pass
    return None, None


def try_wayback(url):
    """Use Wayback Machine 'id_' marker to get the raw asset, bypassing CDN blocks."""
    # Use a recent snapshot year; '*' would query the API, but 2024id_ is the proven pattern.
    wb = f"https://web.archive.org/web/2024id_/{url}"
    try:
        status, ct, body = fetch(wb, timeout=30)
        if status == 200 and ("image" in ct or len(body) > 5_000) and not body[:200].lower().startswith(b"<!doctype"):
            return body, ct
    except Exception as e:
        pass
    # Try 2023 fallback
    wb = f"https://web.archive.org/web/2023id_/{url}"
    try:
        status, ct, body = fetch(wb, timeout=30)
        if status == 200 and ("image" in ct or len(body) > 5_000) and not body[:200].lower().startswith(b"<!doctype"):
            return body, ct
    except Exception as e:
        pass
    return None, None


def download_image(image_url, referer, slug):
    """Try direct, then Wayback. Return repo-relative path or None."""
    if not image_url or not image_url.startswith("http"):
        return None

    body, ct = try_direct(image_url, referer)
    if not body:
        body, ct = try_wayback(image_url)
    if not body:
        return None

    ext = ext_from(image_url, ct or "")
    fname = f"{slug}{ext}"
    out = ASSETS / fname
    out.write_bytes(body)
    rel = f"assets/treatments/{fname}"
    return rel


def main():
    cat_path = DATA / "treatments.json"
    cat = json.loads(cat_path.read_text())

    # Inherit legacy v4 FHWA images already on disk (assets/psc/*).
    for slug, t in cat.items():
        if t.get("image_local"):
            # Already set, copy file into the new location if needed
            legacy = ROOT / t["image_local"]
            if legacy.exists():
                new_path = ASSETS / legacy.name
                if not new_path.exists():
                    new_path.write_bytes(legacy.read_bytes())
                t["image_local"] = f"assets/treatments/{legacy.name}"
                continue

    # Pass through each treatment and download missing ones.
    todo = [(slug, t) for slug, t in cat.items() if not t.get("image_local")]
    print(f"{len(todo)} treatments need image download")

    for slug, t in todo:
        url = t.get("image_url", "")
        referer = t.get("url", "")
        print(f"  {slug}  <-  {url[:80]}")
        rel = download_image(url, referer, slug)
        if rel:
            t["image_local"] = rel
            print(f"    -> saved {rel}")
        else:
            print(f"    !! FAILED — will fall back to remote image_url at runtime")
        time.sleep(0.3)

    cat_path.write_text(json.dumps(cat, indent=2))
    print(f"\nUpdated {cat_path}")

    # Report final status
    missing = [s for s, t in cat.items() if not t.get("image_local")]
    print(f"With local image: {len(cat) - len(missing)}")
    print(f"Missing local:    {len(missing)}  ({', '.join(missing[:10])})")


if __name__ == "__main__":
    main()
