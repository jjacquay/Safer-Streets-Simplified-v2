#!/usr/bin/env python3
"""Recompute corridor -> district overlaps from CURRENT district boundaries.

Replaces the `corridor_districts` block in data/representatives.json. The block
that ships today was built from a 2015-era city layer, but both the Escambia
County Commission and the Pensacola City Council redrew their lines after the
2020 census, so those overlaps can be wrong. This pulls the *current* district
polygons and recomputes which districts each corridor actually runs through.

Sources (CONFIRM the exact layer + field before trusting output):
  - County commission districts: Escambia County ArcGIS
  - City council districts:       City of Pensacola ArcGIS
Provide each source as EITHER a live ArcGIS REST query URL (--county-url /
--city-url) OR a pre-downloaded GeoJSON file (--county-file / --city-file) --
some networks block the GIS hosts, so the file path keeps the script usable.
Geometry must be WGS84 lon/lat (EPSG:4326); for a live URL request `outSR=4326`.

A corridor "overlaps" a district if any of its vertices fall inside the district
polygon OR any of its segments cross a polygon edge -- so a street that merely
passes through a district between two far-apart vertices is still caught, which
plain vertex sampling would miss.

Fails LOUDLY (non-zero exit) on: HTTP / ArcGIS error bodies, zero features, a
missing or unparseable district-number field, out-of-range district numbers, or
any corridor that lands in zero districts of a source that clearly covers it.
A silent bad pull must never overwrite good data -- prefer --dry-run first.

Usage:
  # live pull (URLs shown are PLACEHOLDERS -- confirm the real layer numbers)
  python scripts/recompute_corridor_districts.py \
      --county-url "https://gismaps.myescambia.com/arcgis/rest/services/.../MapServer/29/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson" \
      --city-url   "https://maps.cityofpensacola.com/.../FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson" \
      --county-field COMMDIST --city-field DISTRICT --dry-run

  # from files you downloaded elsewhere
  python scripts/recompute_corridor_districts.py \
      --county-file data/private/county_districts.geojson \
      --city-file   data/private/city_districts.geojson --dry-run
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORRIDORS = ROOT / "data" / "escambia_corridors.geojson"
REPS = ROOT / "data" / "representatives.json"

# Sanity bounds on district numbers so a wrong field (e.g. OBJECTID) fails loudly
# instead of silently producing "district 4213". Widen only with real evidence.
COUNTY_RANGE = range(1, 6)   # Escambia County Commission: districts 1-5
CITY_RANGE = range(1, 8)     # Pensacola City Council: districts 1-7

# Field-name guesses tried (in order) when --*-field is not given. The chosen
# field must parse to an in-range integer for EVERY feature or it is rejected.
COUNTY_FIELD_GUESSES = ["COMMDIST", "COMMISSION", "COMM_DIST", "DISTRICT",
                        "District", "district", "DIST", "DISTRICT_N", "DISTRICTNU"]
CITY_FIELD_GUESSES = ["DISTRICT", "District", "district", "COUNCIL",
                      "COUNCILDIS", "CouncilDis", "COUNCIL_DI", "DIST", "WARD"]


class DataError(Exception):
    """Raised for any condition that must abort the run (caught in main)."""


# ---------- geometry helpers (pure Python, lon/lat) ----------
def point_in_ring(pt, ring):
    """Ray-casting: is pt (lon,lat) inside ring (list of [lon,lat])?"""
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
    """polygon = [outer_ring, hole1, ...]. Inside outer and not in any hole."""
    if not polygon or not point_in_ring(pt, polygon[0]):
        return False
    return not any(point_in_ring(pt, hole) for hole in polygon[1:])


def point_in_geom(pt, geom):
    t = geom.get("type")
    if t == "Polygon":
        return point_in_polygon(pt, geom["coordinates"])
    if t == "MultiPolygon":
        return any(point_in_polygon(pt, poly) for poly in geom["coordinates"])
    return False


def _orient(a, b, c, eps=1e-12):
    """Sign of the cross product (b-a) x (c-a): 1 = ccw, -1 = cw, 0 = collinear."""
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if v > eps:
        return 1
    if v < -eps:
        return -1
    return 0


def _on_seg(a, b, p):
    """p is collinear with a-b: is it within the a-b bounding box?"""
    return (min(a[0], b[0]) - 1e-12 <= p[0] <= max(a[0], b[0]) + 1e-12 and
            min(a[1], b[1]) - 1e-12 <= p[1] <= max(a[1], b[1]) + 1e-12)


def segments_cross(p1, p2, p3, p4):
    """Do segment p1-p2 and segment p3-p4 intersect (incl. touching)?"""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        # strict straddle on both sides (guards against the d==0 collinear cases,
        # which are handled explicitly below)
        if d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
            return True
    if d1 == 0 and _on_seg(p3, p4, p1):
        return True
    if d2 == 0 and _on_seg(p3, p4, p2):
        return True
    if d3 == 0 and _on_seg(p1, p2, p3):
        return True
    if d4 == 0 and _on_seg(p1, p2, p4):
        return True
    return False


def rings_of(geom):
    """All rings (outer + holes) of a Polygon/MultiPolygon as lists of [lon,lat]."""
    t = geom.get("type")
    if t == "Polygon":
        return list(geom["coordinates"])
    if t == "MultiPolygon":
        return [ring for poly in geom["coordinates"] for ring in poly]
    return []


def bbox_of(geom):
    xs, ys = [], []
    for ring in rings_of(geom):
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_disjoint(b1, b2):
    if not b1 or not b2:
        return False
    return b1[2] < b2[0] or b2[2] < b1[0] or b1[3] < b2[1] or b2[3] < b1[1]


def line_segments(geom):
    """(p1,p2) segment pairs across a (Multi)LineString."""
    t = geom.get("type")
    segs = []
    if t == "LineString":
        coords = [geom["coordinates"]]
    elif t == "MultiLineString":
        coords = geom["coordinates"]
    else:
        return segs
    for line in coords:
        for i in range(len(line) - 1):
            segs.append(((line[i][0], line[i][1]), (line[i + 1][0], line[i + 1][1])))
    return segs


def corridor_overlaps(line_geom, dist_geom, dist_bbox=None, line_bbox=None):
    """True if the corridor line touches the district polygon (vertex-in or crossing)."""
    if dist_bbox is None:
        dist_bbox = bbox_of(dist_geom)
    if line_bbox is None:
        line_bbox = bbox_of({"type": "MultiPolygon",
                             "coordinates": [[[list(p) for seg in line_segments(line_geom)
                                               for p in seg]]]}) if line_segments(line_geom) else None
    if _bbox_disjoint(dist_bbox, line_bbox):
        return False
    segs = line_segments(line_geom)
    # 1) any vertex inside the filled polygon
    for (a, _b) in segs:
        if point_in_geom(a, dist_geom):
            return True
    if segs and point_in_geom(segs[-1][1], dist_geom):
        return True
    # 2) any corridor segment crosses any ring edge (catches pass-throughs / clips)
    for ring in rings_of(dist_geom):
        for i in range(len(ring) - 1):
            e1 = (ring[i][0], ring[i][1])
            e2 = (ring[i + 1][0], ring[i + 1][1])
            for (a, b) in segs:
                if segments_cross(a, b, e1, e2):
                    return True
    return False


# ---------- ArcGIS / GeoJSON loading ----------
def _signed_area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return s / 2.0


def esri_to_geojson_geometry(esri):
    """Convert an Esri-JSON polygon ('rings') to a GeoJSON MultiPolygon.

    Esri convention: outer rings are clockwise (negative shoelace area here),
    holes are counterclockwise (positive). Each hole is attached to the outer
    ring that contains its first vertex.
    """
    rings = esri.get("rings")
    if not rings:
        raise DataError("Esri geometry has no 'rings'")
    outers, holes = [], []
    for ring in rings:
        (outers if _signed_area(ring) < 0 else holes).append(ring)
    if not outers:  # degenerate: treat everything as outer
        outers, holes = rings, []
    polys = [[o] for o in outers]
    for hole in holes:
        placed = False
        for poly in polys:
            if point_in_ring(hole[0], poly[0]):
                poly.append(hole)
                placed = True
                break
        if not placed:
            polys.append([hole])  # orphan hole -> its own polygon, better than dropping it
    return {"type": "MultiPolygon", "coordinates": polys}


def features_from_payload(payload):
    """Normalize a REST payload to a list of {'properties','geometry'(GeoJSON)}."""
    if not isinstance(payload, dict):
        raise DataError("response was not a JSON object")
    if "error" in payload:  # ArcGIS returns HTTP 200 with an error body (e.g. token required)
        raise DataError("ArcGIS error: " + json.dumps(payload["error"])[:400])
    if payload.get("type") == "FeatureCollection":
        feats = payload.get("features") or []
        return [{"properties": f.get("properties") or {}, "geometry": f.get("geometry")}
                for f in feats]
    # Esri JSON (f=json): {"features":[{"attributes":..., "geometry":{"rings":...}}]}
    if "features" in payload:
        out = []
        for f in payload["features"]:
            geom = f.get("geometry") or {}
            out.append({"properties": f.get("attributes") or {},
                        "geometry": esri_to_geojson_geometry(geom) if "rings" in geom else geom})
        return out
    raise DataError("unrecognized payload shape (no 'features' / FeatureCollection)")


def fetch_url(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "safer-streets-districts/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise DataError(f"response was not JSON ({e}); first 200 chars: {raw[:200]!r}")


def load_source(url, file, field, guesses, valid_range, label):
    """Return {district_str: merged_geom_dict} for one government source."""
    if file:
        payload = json.loads(Path(file).read_text(encoding="utf-8"))
        origin = f"file:{file}"
    elif url:
        payload = fetch_url(url)
        origin = url
    else:
        raise DataError(f"{label}: provide --{label}-url or --{label}-file")

    feats = features_from_payload(payload)
    if not feats:
        raise DataError(f"{label}: source returned ZERO features ({origin})")

    props0 = feats[0]["properties"]
    chosen = field
    if not chosen:
        for g in guesses:
            if g in props0 and _all_parse(feats, g, valid_range):
                chosen = g
                break
    if not chosen or chosen not in props0:
        raise DataError(
            f"{label}: could not find a district field. "
            f"Available fields: {sorted(props0.keys())}. "
            f"Pass --{label}-field with the correct one.")

    out = {}
    for f in feats:
        raw_val = f["properties"].get(chosen)
        num = _parse_district(raw_val)
        if num is None or num not in valid_range:
            raise DataError(
                f"{label}: field {chosen!r} value {raw_val!r} -> {num} is not a valid "
                f"district in {valid_range.start}..{valid_range.stop - 1}. "
                f"Wrong field, or the layer needs a token / different range.")
        geom = f["geometry"]
        if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
            raise DataError(f"{label}: district {num} has non-polygon geometry {geom and geom.get('type')}")
        key = str(num)
        if key in out:  # a district split across multiple features -> merge parts
            out[key] = _merge_polys(out[key], geom)
        else:
            out[key] = geom

    missing = [str(d) for d in valid_range if str(d) not in out]
    if missing:
        raise DataError(f"{label}: layer is missing district(s) {missing} "
                        f"(got {sorted(out, key=int)}). Incomplete layer -- do not use.")
    print(f"  {label}: {len(out)} districts via field {chosen!r} ({origin[:70]})")
    return out


def _parse_district(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    m = re.search(r"\d+", str(val))
    return int(m.group()) if m else None


def _all_parse(feats, field, valid_range):
    for f in feats:
        n = _parse_district(f["properties"].get(field))
        if n is None or n not in valid_range:
            return False
    return True


def _merge_polys(g1, g2):
    def parts(g):
        return [g["coordinates"]] if g["type"] == "Polygon" else list(g["coordinates"])
    return {"type": "MultiPolygon", "coordinates": parts(g1) + parts(g2)}


# ---------- core computation ----------
def compute_overlaps(corridor_feats, county_map, city_map):
    result = {}
    county_bboxes = {d: bbox_of(g) for d, g in county_map.items()}
    city_bboxes = {d: bbox_of(g) for d, g in city_map.items()}
    for f in corridor_feats:
        cid = f["properties"].get("id")
        geom = f.get("geometry")
        if not cid or not geom:
            continue
        lbb = bbox_of({"type": "MultiPolygon",
                       "coordinates": [[[list(p) for seg in line_segments(geom) for p in seg]]]}) \
            if line_segments(geom) else None
        counties = sorted((d for d, g in county_map.items()
                           if corridor_overlaps(geom, g, county_bboxes[d], lbb)), key=int)
        cities = sorted((d for d, g in city_map.items()
                         if corridor_overlaps(geom, g, city_bboxes[d], lbb)), key=int)
        result[cid] = {"county": counties, "city": cities}
    return result


def diff_blocks(old, new):
    """Human-readable diff of two corridor_districts blocks."""
    lines = []
    for cid in sorted(new):
        o = old.get(cid, {})
        n = new[cid]
        for gov in ("county", "city"):
            os_, ns_ = set(o.get(gov, [])), set(n.get(gov, []))
            if os_ != ns_:
                added = sorted(ns_ - os_, key=int)
                removed = sorted(os_ - ns_, key=int)
                parts = []
                if added:
                    parts.append("+" + ",".join(added))
                if removed:
                    parts.append("-" + ",".join(removed))
                lines.append(f"  {cid} {gov:6} {' '.join(parts)}   "
                             f"(was {sorted(os_, key=int)} -> now {sorted(ns_, key=int)})")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recompute corridor->district overlaps.")
    ap.add_argument("--county-url")
    ap.add_argument("--county-file")
    ap.add_argument("--county-field")
    ap.add_argument("--city-url")
    ap.add_argument("--city-file")
    ap.add_argument("--city-field")
    ap.add_argument("--corridors", default=str(CORRIDORS))
    ap.add_argument("--reps", default=str(REPS))
    ap.add_argument("--out", default=None, help="defaults to --reps (in-place)")
    ap.add_argument("--dry-run", action="store_true", help="compute + diff, do not write")
    args = ap.parse_args(argv)

    try:
        corridors = json.loads(Path(args.corridors).read_text(encoding="utf-8"))
        corridor_feats = corridors.get("features", [])
        if not corridor_feats:
            raise DataError(f"no corridor features in {args.corridors}")

        reps = json.loads(Path(args.reps).read_text(encoding="utf-8"))

        print("Loading district boundaries...")
        county_map = load_source(args.county_url, args.county_file, args.county_field,
                                 COUNTY_FIELD_GUESSES, COUNTY_RANGE, "county")
        city_map = load_source(args.city_url, args.city_file, args.city_field,
                               CITY_FIELD_GUESSES, CITY_RANGE, "city")

        print(f"Computing overlaps for {len(corridor_feats)} corridors...")
        new_block = compute_overlaps(corridor_feats, county_map, city_map)

        # Every corridor must land in >=1 county district (the county blankets the
        # whole area). City is legitimately empty for corridors outside Pensacola.
        orphans = [cid for cid, v in new_block.items() if not v["county"]]
        if orphans:
            raise DataError(f"{len(orphans)} corridor(s) matched ZERO county districts: "
                            f"{orphans}. Check the county layer / projection (need lon/lat).")

        old_block = reps.get("corridor_districts", {})
        changes = diff_blocks(old_block, new_block)
        print(f"\n{len(changes)} corridor/government assignments changed vs current data:")
        print("\n".join(changes) if changes else "  (none -- output matches existing block)")

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        reps["corridor_districts"] = new_block
        meta = reps.setdefault("_meta", {})
        src = meta.setdefault("sources", {})
        if args.county_url or args.county_file:
            src["county_districts_geo"] = args.county_url or f"file:{args.county_file}"
        if args.city_url or args.city_file:
            src["city_districts_geo"] = args.city_url or f"file:{args.city_file}"
        meta["corridor_districts_recomputed"] = "by scripts/recompute_corridor_districts.py"

        out_path = Path(args.out or args.reps)
        out_path.write_text(json.dumps(reps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nWrote {out_path}")
        return 0

    except DataError as e:
        print(f"\nABORT: {e}", file=sys.stderr)
        return 2
    except (OSError, urllib.error.URLError) as e:
        print(f"\nABORT (network/IO): {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
