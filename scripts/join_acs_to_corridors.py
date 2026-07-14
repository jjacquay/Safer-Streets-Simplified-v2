#!/usr/bin/env python3
"""Attach ACS equity context to each corridor via a tract-level spatial join.

Inputs:
  - data/acs_escambia_tracts.json            (from fetch_acs_escambia.py)
  - census tract polygons: data/private/escambia_tracts.geojson if present,
    else fetched from the Census TIGERweb REST API (network; runs in CI).

Method (pure Python, no geo dependencies):
  For each corridor we sample the vertices of its line geometry, assign each
  sample point to the census tract polygon that contains it (ray-casting
  point-in-polygon, holes respected), then aggregate the tracts' ACS indicators
  weighted by how many of the corridor's points fall in each tract (a proxy for
  the share of corridor length in that tract). Rates are length-weighted;
  median household income is a population-weighted mean of tract medians
  (an approximation — medians are not strictly averageable).

Output: adds `equity` to each feature's properties in
data/escambia_corridors.geojson. Only ADDS a field — never touches curated text.
Soft-skips (exit 0) if the ACS file is missing so the wider refresh won't fail.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORRIDORS = ROOT / "data" / "escambia_corridors.geojson"
ACS = ROOT / "data" / "acs_escambia_tracts.json"
TRACTS_LOCAL = ROOT / "data" / "private" / "escambia_tracts.geojson"

# Overridable; verify on first CI run. TIGERweb "Tracts_Blocks" current tracts layer.
TRACTS_URL = os.environ.get(
    "TRACTS_GEOJSON_URL",
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/Tracts_Blocks/"
    "MapServer/4/query?where=" + urllib.parse.quote("STATE='12' AND COUNTY='033'") +
    "&outFields=GEOID&returnGeometry=true&outSR=4326&f=geojson",
)


# ---------- geometry helpers (pure Python) ----------
def point_in_ring(pt, ring):
    """Ray-casting: is pt (lon,lat) inside the ring (list of [lon,lat])?"""
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi):
            inside = not inside
        j = i
    return inside


def point_in_polygon(pt, polygon):
    """polygon = [outer_ring, hole1, hole2, ...]. Inside outer and not in any hole."""
    if not polygon:
        return False
    if not point_in_ring(pt, polygon[0]):
        return False
    return not any(point_in_ring(pt, hole) for hole in polygon[1:])


def point_in_feature(pt, geom):
    t = geom.get("type")
    if t == "Polygon":
        return point_in_polygon(pt, geom["coordinates"])
    if t == "MultiPolygon":
        return any(point_in_polygon(pt, poly) for poly in geom["coordinates"])
    return False


def corridor_points(geom):
    """All vertices of a (Multi)LineString as (lon,lat) sample points."""
    if not geom:
        return []
    t = geom.get("type")
    if t == "LineString":
        return [(c[0], c[1]) for c in geom["coordinates"]]
    if t == "MultiLineString":
        return [(c[0], c[1]) for seg in geom["coordinates"] for c in seg]
    return []


# ---------- join ----------
def build_tract_lookup(acs):
    return {t["geoid"]: t for t in acs.get("tracts", [])}


def geoid_of(feature):
    p = feature.get("properties", {})
    return str(p.get("GEOID") or p.get("geoid") or p.get("GEOID20") or "")


def weighted_equity(counts, tract_lookup):
    """counts: {geoid: n_points}. Returns aggregated equity dict or None."""
    total = sum(counts.values())
    if not total:
        return None
    poverty = zveh = poc = 0.0
    pw = pw_pop = 0.0  # population-weighted income accumulator
    used = []
    wsum_pov = wsum_zveh = wsum_poc = 0.0
    for geoid, n in counts.items():
        t = tract_lookup.get(geoid)
        if not t:
            continue
        used.append({"geoid": geoid, "name": t.get("name"), "points": n})
        w = n
        if t.get("poverty_rate") is not None:
            poverty += t["poverty_rate"] * w; wsum_pov += w
        if t.get("zero_vehicle_hh_share") is not None:
            zveh += t["zero_vehicle_hh_share"] * w; wsum_zveh += w
        if t.get("people_of_color_share") is not None:
            poc += t["people_of_color_share"] * w; wsum_poc += w
        inc = t.get("median_household_income")
        pop = t.get("population") or 0
        if inc is not None and pop:
            pw += inc * pop; pw_pop += pop

    def r(v, w):
        return round(v / w, 4) if w else None

    return {
        "tract_count": len(used),
        "tracts": sorted(used, key=lambda x: -x["points"]),
        "poverty_rate": r(poverty, wsum_pov),
        "zero_vehicle_hh_share": r(zveh, wsum_zveh),
        "people_of_color_share": r(poc, wsum_poc),
        "median_household_income": round(pw / pw_pop) if pw_pop else None,
        "method": "length-weighted tract average (ACS 5-year); income is population-weighted mean of tract medians",
    }


def load_tracts():
    if TRACTS_LOCAL.exists():
        return json.loads(TRACTS_LOCAL.read_text())
    print(f"Fetching tract geometry from {TRACTS_URL[:80]}...", file=sys.stderr)
    req = urllib.request.Request(TRACTS_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def main():
    if not ACS.exists():
        print("data/acs_escambia_tracts.json missing — run fetch_acs_escambia.py first; skipping.",
              file=sys.stderr)
        return 0
    acs = json.loads(ACS.read_text())
    tract_lookup = build_tract_lookup(acs)
    tracts_gj = load_tracts()
    tract_feats = tracts_gj.get("features", [])

    corridors = json.loads(CORRIDORS.read_text())
    joined = 0
    for feat in corridors["features"]:
        pts = corridor_points(feat.get("geometry"))
        counts = {}
        for pt in pts:
            for tf in tract_feats:
                if point_in_feature(pt, tf["geometry"]):
                    g = geoid_of(tf)
                    counts[g] = counts.get(g, 0) + 1
                    break
        eq = weighted_equity(counts, tract_lookup)
        if eq:
            feat["properties"]["equity"] = eq
            joined += 1

    CORRIDORS.write_text(json.dumps(corridors, ensure_ascii=False, indent=2) + "\n")
    print(f"Attached equity context to {joined}/{len(corridors['features'])} corridors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
