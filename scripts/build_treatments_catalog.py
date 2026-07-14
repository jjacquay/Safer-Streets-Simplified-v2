"""
Merge FHWA + NACTO + FDOT + CNU treatment content into a single
treatments.json catalog, then download all images via the Wayback Machine
(FHWA blocks data-center IPs; NACTO/CNU/FDOT may or may not — use Wayback as
a uniform fallback).

Output schema (per treatment, keyed by slug):
{
  "slug": str,
  "source": "FHWA" | "NACTO" | "FDOT" | "CNU",
  "source_full": "Federal Highway Administration — Proven Safety Countermeasure" | ...,
  "title": str,
  "tagline": str,
  "narrative": str,
  "crash_reduction": str,            # "" if none quoted
  "factors": [str],                   # ped, bike, intersection, lane_departure, speeding, impaired, night
  "image_url": str,                   # canonical remote URL
  "image_local": str,                 # repo-relative path (assets/treatments/<slug>.<ext>)
  "image_alt": str,
  "image_is_icon": bool,              # True for the FHWA speed-limit icon
  "url": str,                          # source page
}
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

# --- Locate browse-results files ---
BROWSE_DIR = Path("/home/user/workspace/wide")
FHWA_INITIAL = "browse_results_mqxxxxxx.json"  # original 10 PSCs — already in psc_content.json
EXTERNAL_RESULTS = BROWSE_DIR / "browse_results_mqy998b8.json"   # NACTO/FDOT/CNU (12)
FHWA_REMAINING = BROWSE_DIR / "browse_results_mqy6zgiu.json"     # 8 successful FHWA pages
FHWA_RETRY = BROWSE_DIR / "browse_results_mqy9e5wk.json"         # road-diets retry (1)
EXISTING_PSC = DATA / "psc_content.json"                         # 10 PSCs already cleaned

# FHWA PSCs whose image is a category icon, not a real photo.
PSC_ICON_SLUGS = {"appropriate-speed-limits-all-road-users", "speed-safety-cameras"}

# Source-of-truth: which schema columns we expect from wide_browse.
SOURCE_TITLE_FIELD = "PSC title"
EXTERNAL_TITLE_FIELD = "Treatment or page title as shown on the page"
COL_TAGLINE = ("One-sentence problem this PSC addresses (from page hero or 'Why' section)",
               "One-sentence summary of the problem this treatment addresses or what it does (from page intro/hero)")
COL_NARRATIVE = (
    "2-3 sentence plain-English description of how the countermeasure works and what crash types it reduces. Verbatim from the FHWA page or tightly paraphrased.",
    "2-4 sentence plain-English description of how the treatment works, what crash types or safety/place issues it addresses, and key design notes. Verbatim or tightly paraphrased from the page.",
)
COL_REDUCTION = "Crash-reduction statistic if quoted on the page (e.g. '83% reduction in fatal/injury crashes at intersections')"
COL_REDUCTION_ALT = "Crash-reduction statistic if quoted on the page (e.g. '47% pedestrian crashes'). Empty string if no stat is given."
COL_IMG = ("Absolute URL of the largest representative photo on the page (hero or first photograph showing the countermeasure in real-world use). Must be a direct image URL ending in .jpg/.jpeg/.png/.webp.",
           "Absolute URL of the largest representative image on the page (hero image, photograph, or diagram showing the treatment). Must be a direct image URL ending in .jpg/.jpeg/.png/.webp/.gif. Skip logos and navigation icons.")
COL_ALT = ("Alt text or caption of that image, if available on the page",
           "Alt text or caption of that image, if available")
COL_URL = "Source URL"
COL_FACTORS = "Which crash factors this treatment addresses. Choose from: ped, bike, intersection, lane_departure, speeding, impaired, night. Multiple OK."


def strip_md(s):
    """Strip markdown link wrappers like [text](url) → text."""
    if not s:
        return ""
    # Remove [text](url) → text
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Trim weird trailing attribution patterns like "82% ... Federal Highway Administration"
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    return s


def pick(row, *keys):
    """Fuzzy lookup by trying multiple candidate column names."""
    if isinstance(keys[0], (list, tuple)):
        keys = keys[0]
    for k in keys:
        if k in row and row[k]:
            return row[k]
    return ""


def url_to_slug(url):
    """Turn a source URL into a deterministic slug."""
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if not parts:
        return p.netloc.replace(".", "-")
    # Take the last meaningful segment
    last = parts[-1].lower()
    # Strip file extensions
    last = re.sub(r"\.(shtm|html|htm|aspx|php)$", "", last)
    return re.sub(r"[^a-z0-9-]+", "-", last).strip("-")


def detect_source(url):
    h = urlparse(url).netloc.lower()
    if "highways.dot.gov" in h or "fhwa" in h:
        return "FHWA", "Federal Highway Administration — Proven Safety Countermeasure"
    if "nacto.org" in h:
        return "NACTO", "National Association of City Transportation Officials"
    if "fdot.gov" in h:
        return "FDOT", "Florida Department of Transportation — Context-Based Solutions"
    if "cnu.org" in h:
        return "CNU", "Congress for the New Urbanism"
    return "OTHER", h


def load_rows(path):
    if not path.exists():
        print(f"WARN: missing {path}")
        return []
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        return data.get("results", data.get("rows", []))
    return data


# Canonical slugs for pages where the URL's last segment is generic.
URL_SLUG_OVERRIDES = {
    "https://www.fdot.gov/roadway/context-based-solutions/default.shtm": "fdot-context-based-solutions",
}


def normalize_row(row):
    """Take a wide_browse row and produce a canonical treatment dict.

    We always prefer 'entity' (the input URL passed to wide_browse) over the
    extracted 'Source URL' field because some pages return markdown-formatted
    link text that defeats URL parsing.
    """
    src_url = strip_md(row.get("entity", ""))
    if not src_url:
        src_url = strip_md(pick(row, COL_URL, "url"))
    title = strip_md(pick(row, SOURCE_TITLE_FIELD, EXTERNAL_TITLE_FIELD, "title"))
    tagline = strip_md(pick(row, *COL_TAGLINE, "tagline"))
    narrative = strip_md(pick(row, *COL_NARRATIVE, "narrative"))
    crash_reduction = strip_md(pick(row, COL_REDUCTION, COL_REDUCTION_ALT, "crash_reduction"))
    image_url = strip_md(pick(row, *COL_IMG, "image_url"))
    image_alt = strip_md(pick(row, *COL_ALT, "image_alt"))
    factors = pick(row, COL_FACTORS, "factors") or []
    if isinstance(factors, str):
        factors = [f.strip() for f in factors.split(",") if f.strip()]

    source, source_full = detect_source(src_url)
    slug = URL_SLUG_OVERRIDES.get(src_url, url_to_slug(src_url))
    return {
        "slug": slug,
        "source": source,
        "source_full": source_full,
        "title": title,
        "tagline": tagline,
        "narrative": narrative,
        "crash_reduction": crash_reduction,
        "factors": factors,
        "image_url": image_url,
        "image_local": "",  # filled by downloader
        "image_alt": image_alt,
        "image_is_icon": slug in PSC_ICON_SLUGS,
        "url": src_url,
    }


# Infer factors for FHWA PSCs (the FHWA wide_browse didn't include factors).
FHWA_INFERRED_FACTORS = {
    "rectangular-rapid-flashing-beacons-rrfb": ["ped"],
    "longitudinal-rumble-strips-and-stripes-two-lane-roads": ["lane_departure"],
    "dedicated-left-and-right-turn-lanes-intersections": ["intersection"],
    "leading-pedestrian-interval": ["ped", "intersection"],
    "lighting": ["night", "impaired"],
    "backplates-retroreflective-borders": ["intersection", "night"],
    "bicycle-lanes": ["bike"],
    "roundabouts": ["intersection", "speeding"],
    "systemic-application-multiple-low-cost-countermeasures-stop": ["intersection"],
    "appropriate-speed-limits-all-road-users": ["speeding", "ped"],
    "speed-safety-cameras": ["speeding"],
    "variable-speed-limits": ["speeding"],
    "crosswalk-visibility-enhancements": ["ped"],
    "medians-and-pedestrian-refuge-islands-urban-and-suburban-areas": ["ped"],
    "pedestrian-hybrid-beacons": ["ped"],
    "road-diets-roadway-reconfiguration": ["speeding", "ped", "lane_departure"],
    "walkways": ["ped"],
    "corridor-access-management": ["intersection"],
    "road-safety-audit": ["intersection", "ped", "bike"],
}


def main():
    catalog = {}

    # --- 1. Pre-existing FHWA content (already cleaned + downloaded) ---
    if EXISTING_PSC.exists():
        existing = json.loads(EXISTING_PSC.read_text())
        for slug, c in existing.items():
            # existing schema is a bit different — convert
            catalog[slug] = {
                "slug": slug,
                "source": "FHWA",
                "source_full": "Federal Highway Administration — Proven Safety Countermeasure",
                "title": c.get("title", ""),
                "tagline": c.get("tagline", ""),
                "narrative": c.get("narrative", ""),
                "crash_reduction": c.get("crash_reduction", ""),
                "factors": FHWA_INFERRED_FACTORS.get(slug, []),
                "image_url": c.get("image_url", ""),
                "image_local": c.get("image_local", ""),
                "image_alt": c.get("image_alt", ""),
                "image_is_icon": slug in PSC_ICON_SLUGS,
                "url": c.get("url", ""),
            }
        print(f"  Loaded {len(existing)} from existing psc_content.json")

    # --- 2. The 8 successful FHWA fetches from the second batch ---
    for row in load_rows(FHWA_REMAINING):
        t = normalize_row(row)
        if not t["slug"]:
            continue
        if not t["factors"]:
            t["factors"] = FHWA_INFERRED_FACTORS.get(t["slug"], [])
        if t["slug"] in catalog:
            continue  # don't clobber the cleaned existing copy
        catalog[t["slug"]] = t
        print(f"  + FHWA: {t['slug']}")

    # --- 3. FHWA road-diets retry ---
    for row in load_rows(FHWA_RETRY):
        t = normalize_row(row)
        if not t["slug"]:
            continue
        if not t["factors"]:
            t["factors"] = FHWA_INFERRED_FACTORS.get(t["slug"], [])
        if t["slug"] not in catalog:
            catalog[t["slug"]] = t
            print(f"  + FHWA-retry: {t['slug']}")

    # --- 4. NACTO/FDOT/CNU external content ---
    for row in load_rows(EXTERNAL_RESULTS):
        t = normalize_row(row)
        if not t["slug"]:
            continue
        # CNU pages share slug naming with FHWA in rare cases — disambiguate by prefix
        if t["source"] in ("NACTO", "FDOT", "CNU") and t["slug"] in catalog:
            t["slug"] = f"{t['source'].lower()}-{t['slug']}"
        catalog[t["slug"]] = t
        print(f"  + {t['source']}: {t['slug']}")

    out = DATA / "treatments.json"
    out.write_text(json.dumps(catalog, indent=2))
    print(f"\nWrote {len(catalog)} treatments to {out}")

    # Summary by source
    from collections import Counter
    by_source = Counter(c["source"] for c in catalog.values())
    print(f"By source: {dict(by_source)}")


if __name__ == "__main__":
    main()
