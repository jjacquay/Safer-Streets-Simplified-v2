#!/usr/bin/env python3
"""Offline tests for fetch_district_boundaries.py.

All network access goes through the module-level FETCH hook, which these tests
replace with a dict-backed fake — no real requests. The end-to-end case drives
main() in --report-only mode with --county-url/--city-url overrides against
synthetic district polygons and the real corridor file.

Run:  python3 scripts/test_fetch_district_boundaries.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_district_boundaries as F

FAILURES = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def fake_fetch(responses):
    """FETCH stand-in: dict of url -> payload; unknown URLs raise."""
    def _fetch(url, timeout=30):
        if url not in responses:
            raise OSError(f"fake: no response for {url}")
        return responses[url]
    return _fetch


def square(x0, y0, x1, y1):
    return {"type": "Polygon",
            "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def feat(props, geom):
    return {"type": "Feature", "properties": props, "geometry": geom}


QUERY = "/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson"


def county_fc(count=5, field="COMMDIST", distinct=True):
    # District 1 blankets Escambia so the recompute orphan check passes;
    # the rest are far-away dummies that keep the range/count valid.
    big = square(-88.5, 29.5, -86.0, 31.8)
    feats = []
    for i in range(1, count + 1):
        district = i if distinct else 1
        geom = big if i == 1 else square(900 + i, 900, 901 + i, 901)
        feats.append(feat({field: district, "NAME": f"D{district}"}, geom))
    return fc(feats)


def city_fc(split=False):
    big = square(-88.5, 29.5, -86.0, 31.8)
    feats = [feat({"DISTRICT": i}, big if i == 1 else square(900 + i, 950, 901 + i, 951))
             for i in range(1, 8)]
    if split:
        # Districts split into multiple polygons — 9 features covering 1..7,
        # like the real maps.cityofpensacola.com layers.
        feats.append(feat({"DISTRICT": 2}, square(970, 950, 971, 951)))
        feats.append(feat({"DISTRICT": 5}, square(975, 950, 976, 951)))
    return fc(feats)


def test_is_official():
    check(F.is_official("https://gismaps.myescambia.com/arcgis/rest/services/X/MapServer/0") is True,
          "official: county host")
    check(F.is_official("https://services9.arcgis.com/abc/arcgis/rest/services/X/FeatureServer/0") is False,
          "official: AGOL-hosted copy is not official")


def test_name_matching():
    county_re = F.TARGETS["county"]["name_re"]
    city_re = F.TARGETS["city"]["name_re"]
    check(bool(county_re.search("Commission Districts")), "match: county 'Commission Districts'")
    check(bool(county_re.search("BCC_Districts")), "match: county 'BCC_Districts'")
    check(not county_re.search("School Districts"), "match: county rejects school districts")
    check(bool(city_re.search("City Council Districts")), "match: city council districts")
    check(not city_re.search("Commission Districts"), "match: city rejects commission layer")


def test_walk_and_layers():
    root = "https://gis.example.gov/arcgis/rest/services"
    responses = {
        root + "?f=json": {"folders": ["Admin"],
                           "services": [{"name": "Basemap", "type": "MapServer"},
                                        {"name": "Tables", "type": "GPServer"}]},
        root + "/Admin?f=json": {"services": [{"name": "Admin/Boundaries", "type": "FeatureServer"}]},
        root + "/Admin/Boundaries/FeatureServer?f=json": {
            "layers": [{"id": 0, "name": "Commission Districts"},
                       {"id": 1, "name": "Parcels"}]},
    }
    F.FETCH = fake_fetch(responses)
    log = []
    svcs = list(F.walk_services_directory(root, log))
    check(svcs == [root + "/Basemap/MapServer", root + "/Admin/Boundaries/FeatureServer"],
          f"walk: services incl. folder, GPServer skipped (got {svcs})")
    layers = F.layers_of_service(root + "/Admin/Boundaries/FeatureServer", log)
    check([l["name"] for l in layers] == ["Commission Districts", "Parcels"], "walk: layer listing")
    # unreachable host degrades to log entry, not an exception
    log2 = []
    got = list(F.walk_services_directory("https://down.example.gov/arcgis/rest/services", log2))
    check(got == [] and any("unreachable" in e for e in log2), "walk: dead host degrades gracefully")


def test_validate_candidate():
    url = "https://gismaps.myescambia.com/arcgis/rest/services/A/MapServer/3"
    target = F.TARGETS["county"]

    F.FETCH = fake_fetch({url + QUERY: county_fc()})
    cand = F.validate_candidate(url, target)
    check(cand["field"] == "COMMDIST" and len(cand["feats"]) == 5, "validate: good county layer passes")

    F.FETCH = fake_fetch({url + QUERY: county_fc(count=4)})
    try:
        F.validate_candidate(url, target)
        check(False, "validate: incomplete layer (4 of 5 districts) rejected")
    except Exception as e:
        check("no field covers districts" in str(e), "validate: incomplete layer (4 of 5 districts) rejected")

    F.FETCH = fake_fetch({url + QUERY: county_fc(distinct=False)})
    try:
        F.validate_candidate(url, target)
        check(False, "validate: all-one-district layer rejected")
    except Exception as e:
        check("no field covers districts" in str(e), "validate: all-one-district layer rejected")

    # Split-polygon districts: 9 features covering districts 1..7 must PASS —
    # the recompute tool merges split districts by value.
    city_url = "https://maps.cityofpensacola.com/arcgis/rest/services/A/MapServer/34"
    F.FETCH = fake_fetch({city_url + QUERY: city_fc(split=True)})
    cand = F.validate_candidate(city_url, F.TARGETS["city"])
    check(cand["field"] == "DISTRICT" and len(cand["feats"]) == 9,
          "validate: split-polygon layer (9 feats, districts 1-7) passes")


def test_choose():
    def cand(url, official, districts=(1, 2, 3, 4, 5), verts_scale=1):
        feats = [feat({"D": d}, square(0, 0, verts_scale, verts_scale)) for d in districts]
        return {"url": url, "official": official, "field": "D", "feats": feats}

    off = cand("https://gismaps.myescambia.com/x/0", True)
    agol1 = cand("https://services.arcgis.com/a/x/0", False)
    agol2 = cand("https://services.arcgis.com/b/x/0", False)
    check(F.choose([])[0] is None, "choose: empty -> none")
    check(F.choose([agol1])[0] is agol1, "choose: single candidate wins")
    check(F.choose([off, agol1, agol2])[0] is off, "choose: official outranks community copies")

    # Same dataset republished under several services (identical fingerprints):
    # pick deterministically instead of aborting.
    copy_a = cand("https://maps.cityofpensacola.com/b/MapServer/34", True)
    copy_b = cand("https://maps.cityofpensacola.com/a/MapServer/33", True)
    chosen, reason = F.choose([copy_a, copy_b])
    check(chosen is copy_b and "identical copies" in chosen.get("note", ""),
          "choose: identical copies collapse to deterministic pick")

    # Genuinely different data (different district sets) still aborts.
    diff = cand("https://maps.cityofpensacola.com/c/MapServer/9", True, districts=(1, 2, 3, 4, 5, 1, 2))
    chosen, reason = F.choose([copy_a, diff])
    check(chosen is None and "DIFFERING data" in reason, "choose: differing-data peers abort")


def test_report_no_candidates():
    results = {k: {"chosen": None, "reason": "no candidate layer passed validation",
                   "validated": [], "rejected": [("u", "p", "why")]}
               for k in F.TARGETS}
    out = F.render_report(results, ["log line"], None)
    check("No layer selected" in out and "Rejected candidates" in out,
          "report: no-candidate case is explicit")


def test_main_report_only_end_to_end():
    county_url = "https://gismaps.myescambia.com/arcgis/rest/services/Gov/MapServer/3"
    city_url = "https://maps.cityofpensacola.com/arcgis/rest/services/Gov/MapServer/7"
    F.FETCH = fake_fetch({county_url + QUERY: county_fc(), city_url + QUERY: city_fc()})
    with tempfile.TemporaryDirectory() as td:
        report_path = Path(td) / "report.md"
        rc = F.main(["--report-only", "--report", str(report_path),
                     "--county-url", county_url, "--city-url", city_url])
        check(rc == 0, "e2e: report-only exits 0")
        report = report_path.read_text()
        check("**Selected:** `" + county_url + "`" in report, "e2e: county selected in report")
        check("dry-run: nothing written" in report, "e2e: recompute ran dry (nothing written)")
        real_reps = json.loads((Path(F.ROOT) / "data" / "representatives.json").read_text())
        check(all(v == {"county": [], "city": []} for v in real_reps["corridor_districts"].values()),
              "e2e: real corridor_districts untouched by report-only run")
        check(not (Path(F.ROOT) / "data" / "district_boundaries").exists(),
              "e2e: no boundary files written under data/ in report-only mode")


if __name__ == "__main__":
    test_is_official()
    test_name_matching()
    test_walk_and_layers()
    test_validate_candidate()
    test_choose()
    test_report_no_candidates()
    test_main_report_only_end_to_end()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")
