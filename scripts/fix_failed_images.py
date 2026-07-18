#!/usr/bin/env python3
"""Retry the 7 treatments whose images failed by fetching the source page via
Wayback Machine, extracting the hero image, and downloading via id_ marker.
"""
import gzip
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREATMENTS = ROOT / 'data' / 'treatments.json'
ASSETS = ROOT / 'assets' / 'treatments'
ASSETS.mkdir(parents=True, exist_ok=True)

UA = 'Mozilla/5.0 (compatible; StreetSmartBot/1.0)'

# Slugs to retry, with manual preferred image patterns from inspection
FAILED = [
    'pedestrian-safety-islands',
    'design-speed',
    'neighborhood-main-street',
    'conventional-crosswalks',
    'transit-streets',
    'downtown-1-way-street',
    'fdot-context-based-solutions',
]

# Skip slugs already covered (image_local present and file exists)
SKIP_IF_HAVE_LOCAL = True


def http_get(url, binary=False, timeout=25):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, identity',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ct = r.headers.get('Content-Type', '')
        enc = (r.headers.get('Content-Encoding') or '').lower()
    if enc == 'gzip' or (data[:2] == b'\x1f\x8b'):
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    return (data, ct) if binary else (data.decode('utf-8', errors='replace'), ct)


def wayback_snapshot(url):
    api = f'https://archive.org/wayback/available?url={url}'
    body, _ = http_get(api)
    obj = json.loads(body)
    snap = obj.get('archived_snapshots', {}).get('closest', {})
    if snap.get('available'):
        # snap['url'] looks like http://web.archive.org/web/20260327081437/https://...
        return snap['timestamp'], snap['url']
    return None, None


def find_hero_image(html, slug):
    # Prefer og:image meta
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1)
    # Otherwise find first big content image
    candidates = re.findall(r'(https?://[^"\']*wp-content/uploads/[^"\']+\.(?:jpg|jpeg|png))', html, re.I)
    # Filter out tiny / favicon / logo images
    for c in candidates:
        low = c.lower()
        if any(k in low for k in ['favicon', 'logo', 'sprite', '-150x', '-300x', 'cropped-']):
            continue
        return c
    return candidates[0] if candidates else None


def wayback_raw(url):
    """Convert any URL to a Wayback 'id_' raw asset fetch."""
    # If url is already wayback, strip its prefix to get original
    m = re.match(r'^https?://web\.archive\.org/web/\d+(?:im_|id_)?/(.+)$', url)
    if m:
        original = m.group(1)
    else:
        original = url
    return f'https://web.archive.org/web/2024id_/{original}'


def http_get_retry(url, binary=False, tries=3, sleep=1.5):
    last = None
    for i in range(tries):
        try:
            return http_get(url, binary=binary)
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    raise last


def fetch_image_for(slug, page_url, current_img_url):
    print(f'\n=== {slug}')
    # Strategy 1: try the existing image_url through Wayback id_
    candidates = [current_img_url]

    # Strategy 2: fetch the source page directly (id_ to bypass wayback toolbar)
    try:
        ts, snap_url = wayback_snapshot(page_url)
        if snap_url:
            # Convert snap_url to id_ variant to get clean HTML
            raw_snap = re.sub(r'/web/(\d+)/', r'/web/\1id_/', snap_url)
            html, _ = http_get(raw_snap)
            hero = find_hero_image(html, slug)
            if hero:
                print(f'  found hero in wayback page: {hero[:90]}')
                candidates.insert(0, hero)
    except Exception as e:
        print(f'  wayback page fetch failed: {e}')

    # Try candidates
    for cand in candidates:
        if not cand:
            continue
        ext = '.jpg'
        low = cand.lower()
        if '.png' in low:
            ext = '.png'
        out = ASSETS / f'{slug}{ext}'
        fetch_url = wayback_raw(cand)
        try:
            data, ct = http_get_retry(fetch_url, binary=True)
            if len(data) < 2000:
                print(f'  too small from {fetch_url[:90]}: {len(data)} bytes')
                continue
            if 'image' not in ct.lower() and not data[:8].startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF', b'RIFF')):
                print(f'  not an image (ct={ct})')
                continue
            out.write_bytes(data)
            print(f'  -> saved {out.relative_to(ROOT)} ({len(data)} bytes)')
            return out.relative_to(ROOT).as_posix(), cand
        except Exception as e:
            print(f'  fetch failed {fetch_url[:90]}: {e}')
            continue
    return None, None


def main():
    data = json.loads(TREATMENTS.read_text())
    updates = 0
    for slug in FAILED:
        t = data.get(slug)
        if not t:
            print(f'!! {slug} not in treatments.json')
            continue
        # Skip if already have a working local image
        existing = t.get('image_local')
        if SKIP_IF_HAVE_LOCAL and existing and (ROOT / existing).exists() and (ROOT / existing).stat().st_size > 2000:
            print(f'\n=== {slug}  (already have {existing}, skipping)')
            continue
        page_url = t.get('url')
        cur_img = t.get('image_url')
        local, used = fetch_image_for(slug, page_url, cur_img)
        if local:
            t['image_local'] = local
            if used and used != cur_img:
                t['image_url'] = used
            updates += 1
        time.sleep(0.3)
    if updates:
        TREATMENTS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        print(f'\nUpdated treatments.json with {updates} new image_local paths')
    else:
        print('\nNo updates')


if __name__ == '__main__':
    sys.exit(main() or 0)
