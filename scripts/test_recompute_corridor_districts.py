#!/usr/bin/env python3
"""Stress tests for recompute_corridor_districts.py.

Pure stdlib. Exercises the geometry engine (including the cases plain vertex
sampling gets wrong), the Esri-JSON->GeoJSON conversion, every fail-loud path,
and an end-to-end run against the real corridor file + synthetic districts.

Run:  python scripts/test_recompute_corridor_districts.py
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

import recompute_corridor_districts as R

FAIL = 0
ROOT = Path(__file__).resolve().parent.parent


def check(cond, msg):
    global FAIL
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAIL += 1


def square(x0, y0, x1, y1):
    """A closed CCW-ish square ring as a GeoJSON Polygon."""
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def line(*pts):
    return {"type": "LineString", "coordinates": [list(p) for p in pts]}


# ---------- point-in-polygon ----------
def test_pip():
    sq = square(0, 0, 10, 10)
    check(R.point_in_geom((5, 5), sq), "pip: center inside")
    check(not R.point_in_geom((15, 5), sq), "pip: point outside")
    check(not R.point_in_geom((-1, -1), sq), "pip: point below-left outside")
    # polygon with a hole
    holed = {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}
    check(not R.point_in_geom((5, 5), holed), "pip: point in hole is OUTSIDE")
    check(R.point_in_geom((1, 1), holed), "pip: point in ring but not hole is inside")
    # multipolygon
    mp = {"type": "MultiPolygon", "coordinates": [
        square(0, 0, 5, 5)["coordinates"], square(20, 20, 25, 25)["coordinates"]]}
    check(R.point_in_geom((22, 22), mp), "pip: point in 2nd multipolygon part")
    check(not R.point_in_geom((10, 10), mp), "pip: point between multipolygon parts outside")


# ---------- segment intersection ----------
def test_seg():
    check(R.segments_cross((0, 0), (10, 10), (0, 10), (10, 0)), "seg: clean X crossing")
    check(not R.segments_cross((0, 0), (1, 1), (5, 5), (6, 6)), "seg: collinear disjoint no cross")
    check(not R.segments_cross((0, 0), (1, 0), (0, 1), (1, 1)), "seg: parallel no cross")
    check(R.segments_cross((0, 0), (10, 0), (5, 0), (5, 5)), "seg: T-junction touch counts")
    check(R.segments_cross((0, 0), (5, 5), (5, 5), (10, 0)), "seg: shared endpoint counts")
    check(not R.segments_cross((0, 0), (2, 0), (3, 0), (5, 0)), "seg: collinear gap no cross")


# ---------- corridor overlap: the cases vertex-only sampling breaks on ----------
def test_overlap():
    dist = square(0, 0, 10, 10)
    check(R.corridor_overlaps(line((2, 2), (8, 8)), dist), "overlap: line fully inside")
    check(not R.corridor_overlaps(line((20, 20), (30, 30)), dist), "overlap: line fully outside")
    # KEY CASE: both endpoints OUTSIDE, but the segment passes straight through.
    # A vertex-only test would return False here; segment-crossing must catch it.
    check(R.corridor_overlaps(line((-5, 5), (15, 5)), dist),
          "overlap: pass-through with both vertices outside (vertex-sampling would MISS)")
    # clips a corner only
    check(R.corridor_overlaps(line((8, -2), (12, 2)), dist), "overlap: corner clip")
    # runs alongside just outside -> no
    check(not R.corridor_overlaps(line((-1, -5), (-1, 15)), dist), "overlap: parallel just outside")
    # multipolygon district, corridor only in the far part
    mp = {"type": "MultiPolygon", "coordinates": [
        square(0, 0, 5, 5)["coordinates"], square(20, 20, 25, 25)["coordinates"]]}
    check(R.corridor_overlaps(line((21, 21), (24, 24)), mp), "overlap: corridor in 2nd MP part")
    # MultiLineString where only one part touches
    mls = {"type": "MultiLineString", "coordinates": [[[50, 50], [60, 60]], [[2, 2], [3, 3]]]}
    check(R.corridor_overlaps(mls, dist), "overlap: one MultiLineString part inside")


# ---------- Esri JSON conversion ----------
def test_esri():
    # clockwise outer ring (negative shoelace) per Esri convention
    esri = {"rings": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]}
    g = R.esri_to_geojson_geometry(esri)
    check(g["type"] == "MultiPolygon", "esri: converts to MultiPolygon")
    check(R.point_in_geom((5, 5), g), "esri: converted polygon contains interior point")
    # outer + hole (hole is opposite winding)
    esri2 = {"rings": [
        [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]],      # cw outer
        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]}          # ccw hole
    g2 = R.esri_to_geojson_geometry(esri2)
    check(not R.point_in_geom((5, 5), g2), "esri: hole respected (center excluded)")
    check(R.point_in_geom((1, 1), g2), "esri: interior outside hole included")
    payload = {"features": [{"attributes": {"DISTRICT": 3}, "geometry": esri}]}
    feats = R.features_from_payload(payload)
    check(feats[0]["properties"]["DISTRICT"] == 3 and feats[0]["geometry"]["type"] == "MultiPolygon",
          "esri: features_from_payload maps attributes + geometry")


# ---------- fail-loud paths ----------
def _load(**kw):
    """load_source wrapper that returns ('ok', map) or ('abort', msg)."""
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            m = R.load_source(**kw)
        return ("ok", m)
    except R.DataError as e:
        return ("abort", str(e))


def test_failloud():
    good_geom = square(0, 0, 10, 10)

    def fc(*feats):
        return {"type": "FeatureCollection", "features": list(feats)}

    def feat(dist, geom=good_geom):
        return {"type": "Feature", "properties": {"DISTRICT": dist}, "geometry": geom}

    with tempfile.TemporaryDirectory() as td:
        # ArcGIS error body
        p = Path(td) / "err.json"
        p.write_text(json.dumps({"error": {"code": 499, "message": "Token Required"}}))
        st, msg = _load(url=None, file=str(p), field="DISTRICT",
                        guesses=R.CITY_FIELD_GUESSES, valid_range=R.CITY_RANGE, label="city")
        check(st == "abort" and "ArcGIS error" in msg, "failloud: token/error body aborts")

        # zero features
        p.write_text(json.dumps(fc()))
        st, msg = _load(url=None, file=str(p), field="DISTRICT",
                        guesses=R.CITY_FIELD_GUESSES, valid_range=R.CITY_RANGE, label="city")
        check(st == "abort" and "ZERO features" in msg, "failloud: zero features aborts")

        # out-of-range value (e.g. accidentally an OBJECTID)
        p.write_text(json.dumps(fc(feat(4213))))
        st, msg = _load(url=None, file=str(p), field="DISTRICT",
                        guesses=R.CITY_FIELD_GUESSES, valid_range=R.CITY_RANGE, label="city")
        check(st == "abort" and "not a valid" in msg, "failloud: out-of-range district aborts")

        # missing field entirely
        p.write_text(json.dumps(fc({"type": "Feature", "properties": {"NAME": "x"},
                                    "geometry": good_geom})))
        st, msg = _load(url=None, file=str(p), field=None,
                        guesses=R.CITY_FIELD_GUESSES, valid_range=R.CITY_RANGE, label="city")
        check(st == "abort" and "could not find a district field" in msg,
              "failloud: missing field aborts with field list")

        # incomplete layer: county with only 3 of 5 districts
        p.write_text(json.dumps(fc(feat(1), feat(2), feat(3))))
        st, msg = _load(url=None, file=str(p), field="DISTRICT",
                        guesses=R.COUNTY_FIELD_GUESSES, valid_range=R.COUNTY_RANGE, label="county")
        check(st == "abort" and "missing district" in msg, "failloud: incomplete layer aborts")

        # happy path + field auto-detect + multipart merge
        p.write_text(json.dumps(fc(feat(1), feat(2), feat(3), feat(4),
                                   feat(5, square(0, 0, 1, 1)), feat(5, square(8, 8, 9, 9)))))
        st, m = _load(url=None, file=str(p), field=None,
                      guesses=R.COUNTY_FIELD_GUESSES, valid_range=R.COUNTY_RANGE, label="county")
        check(st == "ok" and set(m) == {"1", "2", "3", "4", "5"}, "happy: auto-detect + all districts")
        check(m["5"]["type"] == "MultiPolygon" and len(m["5"]["coordinates"]) == 2,
              "happy: district split across features is merged")


# ---------- integration against the REAL corridor file ----------
def test_integration():
    cpath = ROOT / "data" / "escambia_corridors.geojson"
    if not cpath.exists():
        check(False, f"integration: {cpath} missing")
        return
    corridors = json.loads(cpath.read_text())
    feats = corridors["features"]
    # bounding box of all corridors
    xs, ys = [], []
    for f in feats:
        for p in R.line_segments(f["geometry"]):
            for (x, y) in p:
                xs.append(x)
                ys.append(y)
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    pad = 0.05
    # county layer that blankets everything -> every corridor must hit district 1
    big = square(minx - pad, miny - pad, maxx + pad, maxy + pad)
    county_all = {"1": big, "2": square(999, 999, 1000, 1000),  # far-away dummies to fill range
                  "3": square(999, 999, 1000, 1000), "4": square(999, 999, 1000, 1000),
                  "5": square(999, 999, 1000, 1000)}
    # a vertical split at the horizontal midpoint -> east/west halves
    midx = (minx + maxx) / 2
    west = square(minx - pad, miny - pad, midx, maxy + pad)
    east = square(midx, miny - pad, maxx + pad, maxy + pad)
    city_all = {"1": west, "2": east, "3": square(999, 999, 1000, 1000),
                "4": square(999, 999, 1000, 1000), "5": square(999, 999, 1000, 1000),
                "6": square(999, 999, 1000, 1000), "7": square(999, 999, 1000, 1000)}

    block = R.compute_overlaps(feats, county_all, city_all)
    check(len(block) == len(feats), f"integration: every corridor produced a result ({len(block)})")
    check(all(v["county"] == ["1"] for v in block.values()),
          "integration: blanket county layer assigns district 1 to all")
    # each corridor must be in west, east, or (if it straddles) both -- never neither
    ok = all(("1" in v["city"] or "2" in v["city"]) for v in block.values())
    check(ok, "integration: every corridor lands in a city half (no orphans from split)")
    # at least one corridor should straddle the midline (both halves) given real data
    straddlers = [cid for cid, v in block.items() if set(v["city"]) >= {"1", "2"}]
    print(f"  INFO: {len(straddlers)} corridors straddle the east/west split line")


# ---------- end-to-end main() with dry-run ----------
def test_main_dryrun():
    cpath = ROOT / "data" / "escambia_corridors.geojson"
    if not cpath.exists():
        check(False, "e2e: corridors file missing")
        return
    with tempfile.TemporaryDirectory() as td:
        # minimal reps file + county/city geojson files spanning the corridor bbox
        corridors = json.loads(cpath.read_text())
        xs = [x for f in corridors["features"] for seg in R.line_segments(f["geometry"]) for (x, _y) in seg]
        ys = [y for f in corridors["features"] for seg in R.line_segments(f["geometry"]) for (_x, y) in seg]
        pad = 0.05
        big = square(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        far = square(999, 999, 1000, 1000)

        def fc_for(rng):
            fs = [{"type": "Feature", "properties": {"DISTRICT": d},
                   "geometry": big if d == 1 else far} for d in rng]
            return {"type": "FeatureCollection", "features": fs}

        cty = Path(td) / "county.geojson"
        cty.write_text(json.dumps(fc_for(range(1, 6))))
        city = Path(td) / "city.geojson"
        city.write_text(json.dumps(fc_for(range(1, 8))))
        reps = Path(td) / "reps.json"
        reps.write_text(json.dumps({"_meta": {}, "corridor_districts": {"C001": {"county": ["9"], "city": []}}}))

        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = R.main(["--county-file", str(cty), "--city-file", str(city),
                         "--reps", str(reps), "--dry-run"])
        out = buf.getvalue()
        check(rc == 0, "e2e: dry-run returns 0")
        check("nothing written" in out, "e2e: dry-run does not write")
        check(json.loads(reps.read_text())["corridor_districts"]["C001"]["county"] == ["9"],
              "e2e: dry-run left the file unchanged")

        # real write
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = R.main(["--county-file", str(cty), "--city-file", str(city), "--reps", str(reps)])
        check(rc == 0, "e2e: write run returns 0")
        written = json.loads(reps.read_text())
        check(written["corridor_districts"]["C001"]["county"] == ["1"],
              "e2e: write updated the block to district 1")


def main():
    for t in (test_pip, test_seg, test_overlap, test_esri, test_failloud,
              test_integration, test_main_dryrun):
        print(f"\n== {t.__name__} ==")
        t()
    print("\n" + ("ALL TESTS PASSED" if FAIL == 0 else f"{FAIL} FAILURES"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
