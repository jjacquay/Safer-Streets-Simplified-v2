#!/usr/bin/env python3
"""Fetch road centerlines from OSM Overpass API for each corridor.

For each corridor in corridors_master.json, query Overpass for highway ways
matching any of the `name_keys` within its bounding box. Merge the resulting
ways into one or more LineStrings per corridor.

Writes data/private/osm_centerlines.geojson (intermediate; not committed).
"""
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "scripts" / "corridors_master.json"
OUT = ROOT / "data" / "private" / "osm_centerlines.geojson"

OVERPASS = "https://overpass-api.de/api/interpreter"


def build_query(name_keys, bbox):
    """Build an Overpass QL query for highway ways matching any name_key in bbox.

    bbox is [west, south, east, north]; Overpass expects (south, west, north, east).
    """
    w, s, e, n = bbox
    s_bbox = f"({s},{w},{n},{e})"
    # Match name OR ref (US 90, SR 291, etc) case-insensitively
    name_regex = "|".join([k.replace(" ", "[ ]?") for k in name_keys])
    # Build union of way queries
    parts = [
        f'way["highway"]["name"~"{name_regex}",i]{s_bbox};',
        f'way["highway"]["ref"~"{name_regex}",i]{s_bbox};',
    ]
    q = f"""
[out:json][timeout:60];
(
  {''.join(parts)}
);
out geom;
""".strip()
    return q


def fetch_overpass(query, max_retries=3):
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                OVERPASS,
                data=data,
                headers={"User-Agent": "StreetSmartEscambia/1.0 (research)"},
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            last_err = exc
            print(f"  overpass attempt {attempt+1} failed: {exc}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"overpass failed: {last_err}")


def ways_to_linestrings(elements):
    """Each way -> LineString feature with [lon, lat] coordinates."""
    feats = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        coords = [[p["lon"], p["lat"]] for p in geom]
        tags = el.get("tags", {}) or {}
        feats.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "osm_id": el.get("id"),
                "name": tags.get("name"),
                "ref": tags.get("ref"),
                "highway": tags.get("highway"),
            },
        })
    return feats


def main():
    with open(MASTER) as f:
        master = json.load(f)

    OUT.parent.mkdir(parents=True, exist_ok=True)

    out_features = []
    for corridor in master["corridors"]:
        cid = corridor["id"]
        cname = corridor["name"]
        osm = corridor.get("osm") or {}
        keys = osm.get("name_keys") or []
        bbox = osm.get("bbox")
        if not keys or not bbox:
            print(f"[{cid}] {cname}: no osm config, skipping")
            continue

        q = build_query(keys, bbox)
        print(f"[{cid}] {cname}: fetching ({len(keys)} keys)...")
        try:
            data = fetch_overpass(q)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            continue

        ways = ways_to_linestrings(data.get("elements", []))
        print(f"  -> {len(ways)} ways")
        for w in ways:
            w["properties"]["corridor_id"] = cid
            w["properties"]["corridor_name"] = cname
            out_features.append(w)
        # Be polite to Overpass
        time.sleep(2)

    fc = {"type": "FeatureCollection", "features": out_features}
    with open(OUT, "w") as f:
        json.dump(fc, f)
    print(f"\nWrote {len(out_features)} ways to {OUT}")


if __name__ == "__main__":
    main()
