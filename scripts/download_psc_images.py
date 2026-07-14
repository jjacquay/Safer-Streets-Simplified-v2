"""
Download the FHWA Proven Safety Countermeasure hero images via the
Internet Archive (Wayback Machine) because the live FHWA CDN blocks
direct hotlinking from data-center IPs.

Re-writes psc_content.json with local /assets/psc/<slug>.<ext> paths.
"""
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "data" / "psc_content.json"
ASSETS = ROOT / "assets" / "psc"
ASSETS.mkdir(parents=True, exist_ok=True)


def fetch(url, dest):
    # Use Wayback's id_ marker which serves the raw archived asset
    # (without it, Wayback returns its HTML landing page).
    way = f"https://web.archive.org/web/2024id_/{url}"
    req = urllib.request.Request(way, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            ct = r.headers.get("Content-Type", "")
    except Exception as e:
        return False, f"fetch failed: {e}"
    if len(data) < 500:
        return False, f"too small ({len(data)} bytes), likely 404"
    if "html" in ct.lower() and "image" not in ct.lower():
        return False, f"got HTML not image (ct={ct})"
    dest.write_bytes(data)
    return True, f"{len(data)} bytes ct={ct}"


def main():
    content = json.loads(CONTENT.read_text())
    n_ok = 0
    for slug, c in content.items():
        url = c.get("image_url", "")
        if not url:
            print(f"  {slug}: no image_url, skip")
            continue
        # Extract extension
        path = urllib.parse.urlparse(url).path
        ext = os.path.splitext(path)[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        dest = ASSETS / f"{slug}{ext}"
        ok, msg = fetch(url, dest)
        if ok:
            n_ok += 1
            c["image_local"] = f"assets/psc/{slug}{ext}"
            c["image_source"] = url  # keep original for attribution
            print(f"  OK  {slug}: {msg}")
        else:
            print(f"  FAIL {slug}: {msg}")

    CONTENT.write_text(json.dumps(content, indent=2))
    print(f"\nDownloaded {n_ok}/{len(content)} images to {ASSETS}")
    if n_ok < len(content):
        sys.exit(1)


if __name__ == "__main__":
    main()
