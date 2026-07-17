#!/usr/bin/env python3
"""Discover, download, and apply county/city district boundary layers.

The corridor→district recompute tool (recompute_corridor_districts.py) needs two
polygon layers — Escambia County Commission districts (5) and Pensacola City
Council districts (7) — that nobody has a confirmed endpoint for yet. This script
finds them, fail-loud:

  1. Searches the public ArcGIS Online index for matching Feature/Map services.
  2. Walks the ArcGIS REST services directories of candidate government hosts.
  3. Validates every candidate layer hard: polygon-typed, name matches the
     target, returns EXACTLY the expected feature count (5 county / 7 city),
     covers each district exactly once, and passes the district-field
     auto-detection reused from the recompute module.
  4. Prefers layers hosted on official government domains over community copies;
     any remaining ambiguity ABORTS with a full candidate report — it never
     silently picks.
  5. Downloads the chosen layers (WGS84 GeoJSON) and drives the recompute
     (dry-run diff first, then write in full mode).

Designed to run in CI (GitHub runners have open internet; the dev sandbox does
not). --report-only mode: discover + validate + dry-run in a temp dir, write a
Markdown report, touch nothing under data/, and always exit 0 — the report is
the product. Full mode exits non-zero unless both layers were selected and the
recompute succeeded.

Known endpoints can bypass discovery entirely:
  python3 scripts/fetch_district_boundaries.py \
    --county-url "https://…/MapServer/3" --city-url "https://…/FeatureServer/0"
(the layer URL, without /query — the query string is appended here).
"""

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recompute_corridor_districts as R

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "district_boundaries"

# Single injection point for all network access, so tests can stub it out.
FETCH = R.fetch_url

AGOL_SEARCH = "https://www.arcgis.com/sharing/rest/search"

# Hosts named in repo docs as the likely GIS homes. Walked in order; individual
# failures are logged and skipped, never fatal.
CANDIDATE_HOSTS = [
    "https://gismaps.myescambia.com/arcgis/rest/services",
    "https://gis.myescambia.com/arcgis/rest/services",
    "https://maps.cityofpensacola.com/arcgis/rest/services",
    "https://gis.cityofpensacola.com/arcgis/rest/services",
]

# A layer only counts as "official" if it is served from a government domain.
OFFICIAL_DOMAINS = ("myescambia.com", "cityofpensacola.com", "escambiavotes.gov")

TARGETS = {
    "county": {
        "label": "Escambia County Commission districts",
        "name_re": re.compile(r"(comm(ission)?|bcc)[ _-]*district", re.I),
        "expected": 5,
        "field_guesses": R.COUNTY_FIELD_GUESSES,
        "valid_range": R.COUNTY_RANGE,
        "agol_queries": [
            'escambia county commission districts',
            'escambia BCC districts',
        ],
    },
    "city": {
        "label": "Pensacola City Council districts",
        "name_re": re.compile(r"council[ _-]*district", re.I),
        "expected": 7,
        "field_guesses": R.CITY_FIELD_GUESSES,
        "valid_range": R.CITY_RANGE,
        "agol_queries": [
            'pensacola city council districts',
        ],
    },
}

MAX_SERVICES_PER_HOST = 150


def _fetch(url, timeout=30):
    return FETCH(url, timeout=timeout)


def is_official(url):
    host = urllib.parse.urlparse(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def walk_services_directory(services_root, log):
    """Yield service URLs (…/Name/MapServer|FeatureServer) under an ArcGIS
    services root, recursing one level of folders. Failures log + stop that
    branch."""
    seen = 0

    def _list(url, folder=""):
        nonlocal seen
        try:
            payload = _fetch(url + "?f=json")
        except Exception as e:  # noqa: BLE001 — any host failure just ends this branch
            log.append(f"  services dir unreachable: {url} ({type(e).__name__}: {e})")
            return
        for svc in payload.get("services", []):
            if seen >= MAX_SERVICES_PER_HOST:
                return
            stype = svc.get("type")
            if stype not in ("MapServer", "FeatureServer"):
                continue
            name = svc.get("name", "")
            # Service names are root-relative ("Folder/Service").
            yield_url = services_root + "/" + name + "/" + stype
            seen += 1
            yield yield_url
        if not folder:
            for sub in payload.get("folders", []):
                yield from _list(services_root + "/" + sub, folder=sub)

    yield from _list(services_root)


def layers_of_service(svc_url, log):
    """[{id, name, url}] for the sublayers of a Map/Feature service."""
    try:
        payload = _fetch(svc_url + "?f=json")
    except Exception as e:  # noqa: BLE001
        log.append(f"  service unreadable: {svc_url} ({type(e).__name__})")
        return []
    out = []
    for lyr in payload.get("layers", []) or []:
        if lyr.get("id") is None:
            continue
        out.append({
            "id": lyr["id"],
            "name": lyr.get("name", ""),
            "url": f"{svc_url}/{lyr['id']}",
        })
    return out


def agol_search(query, log):
    """Service URLs from the public ArcGIS Online item search."""
    q = f'({query}) AND (type:"Feature Service" OR type:"Map Service")'
    url = AGOL_SEARCH + "?" + urllib.parse.urlencode({"f": "json", "num": 50, "q": q})
    try:
        payload = _fetch(url)
    except Exception as e:  # noqa: BLE001
        log.append(f"  AGOL search failed for {query!r} ({type(e).__name__}: {e})")
        return []
    urls = []
    for item in payload.get("results", []) or []:
        u = (item.get("url") or "").rstrip("/")
        if u and re.search(r"/(MapServer|FeatureServer)(/\d+)?$", u):
            urls.append(u)
    return urls


def normalize_layer_url(url):
    """Accept both bare layer URLs (…/MapServer/3) and full query URLs
    (…/MapServer/3/query?where=…) — the recompute README documents the latter
    form, so users will paste either."""
    url = (url or "").split("?", 1)[0].rstrip("/")
    if url.endswith("/query"):
        url = url[: -len("/query")]
    return url


def query_layer_features(layer_url):
    """Download a layer's features; GeoJSON first, Esri JSON fallback."""
    params = "where=1%3D1&outFields=*&returnGeometry=true&outSR=4326"
    last_err = None
    for fmt in ("geojson", "json"):
        try:
            payload = _fetch(f"{layer_url}/query?{params}&f={fmt}", timeout=60)
            return R.features_from_payload(payload)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise R.DataError(f"query failed for {layer_url}: {last_err}")


def detect_district_field(feats, target):
    """The first field guess where every feature parses to an in-range district
    AND the distinct values cover the full district set, or None. Feature count
    may exceed the district count — districts are often split into multiple
    polygons, and the recompute tool merges them by district value."""
    if not feats:
        return None
    props0 = feats[0].get("properties") or {}
    want = set(target["valid_range"])
    for guess in target["field_guesses"]:
        if guess in props0 and R._all_parse(feats, guess, target["valid_range"]):
            values = {R._parse_district(f["properties"].get(guess)) for f in feats}
            if values == want:
                return guess
    return None


def validate_candidate(layer_url, target):
    """Return {url, field, feats} if the layer covers exactly the target's
    district set, else raise DataError with the reason."""
    feats = query_layer_features(layer_url)
    # Sanity bound: a "districts" layer with dozens of rows is something else
    # (precincts, parcels) even if a field happens to parse.
    if not feats or len(feats) > 8 * target["expected"]:
        raise R.DataError(f"{len(feats)} features (expected ~{target['expected']}, "
                          f"allowing split-polygon districts)")
    field = detect_district_field(feats, target)
    if not field:
        keys = sorted((feats[0].get("properties") or {}).keys())[:12]
        raise R.DataError(
            f"{len(feats)} features but no field covers districts "
            f"{sorted(set(target['valid_range']))} with every feature in range "
            f"(fields include: {', '.join(keys)})")
    return {"url": layer_url, "field": field, "feats": feats}


def _vertex_count(geom):
    def count(x):
        if isinstance(x, (list, tuple)):
            if x and isinstance(x[0], (int, float)):
                return 1
            return sum(count(y) for y in x)
        return 0
    return count((geom or {}).get("coordinates") or [])


def candidate_signature(cand):
    """Cheap dataset fingerprint: same districts + geometry bulk = same layer
    republished under another service name (common on municipal GIS servers)."""
    districts = tuple(sorted(R._parse_district(f["properties"].get(cand["field"]))
                             for f in cand["feats"]))
    verts = sum(_vertex_count(f.get("geometry")) for f in cand["feats"])
    return (districts, len(cand["feats"]), verts)


def host_layer_candidates(log):
    """Walk every candidate host ONCE and return all sublayers as
    [{name, url, host}] — shared across targets so dead-host timeouts are paid
    a single time."""
    layers = []
    for host in CANDIDATE_HOSTS:
        log.append(f"walking {host}")
        for svc_url in walk_services_directory(host, log):
            for lyr in layers_of_service(svc_url, log):
                layers.append({"name": lyr["name"], "url": lyr["url"], "host": host})
    return layers


def discover(target_key, host_layers, log):
    """All validated candidates for one target, deduped, with provenance."""
    target = TARGETS[target_key]
    candidate_urls = {}  # url -> provenance

    for lyr in host_layers:
        if target["name_re"].search(lyr["name"]):
            candidate_urls.setdefault(lyr["url"], f"host-walk:{lyr['host']}")

    for q in target["agol_queries"]:
        log.append(f"AGOL search: {q!r}")
        for u in agol_search(q, log):
            if re.search(r"/\d+$", u):
                candidate_urls.setdefault(u, f"agol:{q}")
            else:
                for lyr in layers_of_service(u, log):
                    if target["name_re"].search(lyr["name"]):
                        candidate_urls.setdefault(lyr["url"], f"agol:{q}")

    validated, rejected = [], []
    for url, provenance in candidate_urls.items():
        try:
            cand = validate_candidate(url, target)
            cand["provenance"] = provenance
            cand["official"] = is_official(url)
            validated.append(cand)
        except Exception as e:  # noqa: BLE001
            rejected.append((url, provenance, f"{type(e).__name__}: {e}"))
    return validated, rejected


def choose(validated):
    """Exactly one candidate, or (None, reason). Official domains outrank
    community copies. Multiple equally-ranked candidates with the SAME data
    fingerprint are copies of one dataset republished under several services
    (common on municipal GIS servers) — pick one deterministically. Candidates
    with DIFFERING data are a hard stop, never a guess."""
    if not validated:
        return None, "no candidate layer passed validation"
    official = [c for c in validated if c["official"]]
    pool = official or validated
    if len(pool) > 1:
        if len({candidate_signature(c) for c in pool}) == 1:
            # Deterministic pick among identical copies, avoiding services with
            # "test"/"temp" in the URL — same data, but those URLs are the ones
            # that vanish, and the chosen URL is recorded as provenance.
            chosen = sorted(pool, key=lambda c: (bool(re.search(r"test|temp", c["url"], re.I)), c["url"]))[0]
            chosen["note"] = f"{len(pool)} identical copies found; chose deterministically"
            return chosen, None
        return None, (f"{len(pool)} equally-ranked candidates with DIFFERING data — "
                      "refusing to guess: " + ", ".join(c["url"] for c in pool))
    return pool[0], None


def run_recompute(county_file, city_file, write):
    """Drive the existing recompute tool; returns (exit_code, captured_output)."""
    argv = ["--county-file", str(county_file), "--city-file", str(city_file)]
    if not write:
        argv.append("--dry-run")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = R.main(argv)
    return rc, buf.getvalue()


def render_report(results, log, recompute_out):
    lines = [
        "# District boundary discovery report",
        "",
        "_Generated by scripts/fetch_district_boundaries.py — candidates are",
        "validated hard (every feature parses to an in-range district and the",
        "full district set is covered; split-polygon districts allowed) but the",
        "chosen layers still deserve a human look before the corridor_districts",
        "they produce ships to residents._",
        "",
    ]
    for key, res in results.items():
        t = TARGETS[key]
        lines.append(f"## {t['label']}  (`{key}`, expect {t['expected']} districts)")
        lines.append("")
        chosen, reason = res["chosen"], res["reason"]
        if chosen:
            lines.append(f"- **Selected:** `{chosen['url']}`")
            lines.append(f"  - district field `{chosen['field']}`, {len(chosen['feats'])} features, "
                         f"official domain: {chosen['official']}, via {chosen['provenance']}")
            if chosen.get("note"):
                lines.append(f"  - {chosen['note']}")
        else:
            lines.append(f"- **No layer selected:** {reason}")
        if res["validated"] and (not chosen or len(res["validated"]) > 1):
            lines.append("- Validated candidates:")
            for c in res["validated"]:
                lines.append(f"  - `{c['url']}` (official={c['official']}, via {c['provenance']})")
        if res["rejected"]:
            lines.append(f"- Rejected candidates ({len(res['rejected'])}):")
            for url, provenance, why in res["rejected"][:20]:
                lines.append(f"  - `{url}` — {why} (via {provenance})")
        lines.append("")
    if recompute_out is not None:
        lines.append("## Recompute output")
        lines.append("")
        lines.append("```")
        lines.append(recompute_out.rstrip())
        lines.append("```")
        lines.append("")
    lines.append("<details><summary>Discovery log</summary>")
    lines.append("")
    lines.extend("    " + entry for entry in log)
    lines.append("")
    lines.append("</details>")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--county-url", help="known county layer URL (…/MapServer/<n>); skips discovery")
    ap.add_argument("--city-url", help="known city layer URL; skips discovery")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--report", default=None, help="write the Markdown report here")
    ap.add_argument("--report-only", action="store_true",
                    help="discover + validate + dry-run in a temp dir; write nothing under data/; always exit 0")
    args = ap.parse_args(argv)

    log = []
    results = {}
    overrides = (("county", args.county_url), ("city", args.city_url))
    # One shared host walk for every target that still needs discovery.
    host_layers = host_layer_candidates(log) if any(not o for _, o in overrides) else []
    for key, override in overrides:
        target = TARGETS[key]
        if override:
            override = normalize_layer_url(override)
            log.append(f"{key}: using provided URL {override} (discovery skipped)")
            try:
                cand = validate_candidate(override, target)
                cand["provenance"] = "cli-override"
                cand["official"] = is_official(override)
                results[key] = {"chosen": cand, "reason": None,
                                "validated": [cand], "rejected": []}
            except Exception as e:  # noqa: BLE001
                results[key] = {"chosen": None,
                                "reason": f"provided URL failed validation: {e}",
                                "validated": [], "rejected": [(override, "cli-override", str(e))]}
        else:
            validated, rejected = discover(key, host_layers, log)
            chosen, reason = choose(validated)
            results[key] = {"chosen": chosen, "reason": reason,
                            "validated": validated, "rejected": rejected}

    both = all(results[k]["chosen"] for k in TARGETS)
    recompute_out = None
    rc = 0
    if both:
        out_dir = Path(tempfile.mkdtemp(prefix="districts_")) if args.report_only else Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for key, fname in (("county", "county_commission.geojson"), ("city", "city_council.geojson")):
            chosen = results[key]["chosen"]
            fc = {"type": "FeatureCollection",
                  "_source": {"url": chosen["url"], "district_field": chosen["field"]},
                  "features": [{"type": "Feature",
                                "properties": f["properties"],
                                "geometry": f["geometry"]} for f in chosen["feats"]]}
            path = out_dir / fname
            path.write_text(json.dumps(fc), encoding="utf-8")
            files[key] = path
        try:
            rc, recompute_out = run_recompute(files["county"], files["city"], write=False)
            if rc == 0 and not args.report_only:
                rc, out2 = run_recompute(files["county"], files["city"], write=True)
                recompute_out += "\n--- write pass ---\n" + out2
        except Exception as e:  # noqa: BLE001
            recompute_out = f"recompute raised {type(e).__name__}: {e}"
            rc = 2
    else:
        rc = 2

    report = render_report(results, log, recompute_out)
    print(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")

    if args.report_only:
        return 0  # the report IS the deliverable; a red probe run helps nobody
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
