#!/usr/bin/env python3
"""Refresh ONLY the crash-derived numeric fields on the existing corridor GeoJSON.

This is the non-destructive counterpart to build_corridor_geojson.py. Instead of
rebuilding the file from scratch (which would drop the curated v5 `psc` card list,
`countermeasures`, `equity`, and softened text), it:

  1. reads data/escambia_corridors.geojson as-is (keeping every curated field and
     the existing corridor geometry),
  2. re-runs the same 30 m buffer crash-join used by build_corridor_geojson,
  3. overwrites only these derived fields on each corridor:
       risk, score, crash_count, fatal_count, serious_injury_count, ksi_count,
       ksi_per_mile, length_mi, years, time_of_day, crash_factors,
       crash_factors_ksi, fatal_source_breakdown

The scoring formula and thresholds are kept in sync with build_corridor_geojson.py.
After this runs, chain build_countermeasures.py so cost/KSI figures track the new
crash_factors. Requires the fresh crash extracts in data/private/ (fetched in CI).
"""
import json
from collections import Counter
from pathlib import Path

# Reuse the exact geometry/crash helpers so behavior can't drift.
from build_corridor_geojson import (
    load_crash_pts, crash_in_bbox, point_to_lines_min_m, line_length_m, BUFFER_M,
)

ROOT = Path(__file__).resolve().parent.parent
CORR = ROOT / "data" / "escambia_corridors.geojson"

FLAG_KEYS = ["ped", "bike", "intersection", "lane_departure",
             "night", "motorcycle", "speeding", "impaired", "distracted"]


def risk_label(score):
    if score >= 70: return "very-high"
    if score >= 40: return "high"
    if score >= 20: return "moderate"
    return "lower"


def lines_of(geometry):
    """Normalize a corridor geometry to a list of coordinate rings (lines)."""
    if not geometry:
        return []
    t = geometry.get("type")
    if t == "MultiLineString":
        return geometry["coordinates"]
    if t == "LineString":
        return [geometry["coordinates"]]
    return []


def corridor_stats(lines, crash_pts):
    """Compute the crash-derived numeric fields for one corridor.

    Mirrors build_corridor_geojson.py main() (kept in sync intentionally).
    """
    all_lons, all_lats = [], []
    for coords in lines:
        for pt in coords:
            all_lons.append(pt[0]); all_lats.append(pt[1])
    if not all_lons:
        return None
    bbox = [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]
    candidates = [c for c in crash_pts if crash_in_bbox(c["lat"], c["lon"], bbox)]
    matched = [c for c in candidates
               if point_to_lines_min_m((c["lon"], c["lat"]), lines) <= BUFFER_M]

    fatal = sum(1 for c in matched if c["injsever"] == "5")
    serious = sum(1 for c in matched if c["injsever"] == "4")
    total = len(matched)
    ksi = fatal + serious
    years = Counter(c["year"] for c in matched)

    tod = Counter()
    for c in matched:
        t = c["time"]
        try:
            hh = int(t[:2]) if t and len(t) >= 2 else None
        except ValueError:
            hh = None
        if hh is None:
            tod["unknown"] += 1
        elif 6 <= hh < 12:
            tod["morning"] += 1
        elif 12 <= hh < 18:
            tod["afternoon"] += 1
        elif 18 <= hh < 22:
            tod["evening"] += 1
        else:
            tod["night"] += 1

    flag_counts = {k: 0 for k in FLAG_KEYS}
    flag_ksi = {k: 0 for k in FLAG_KEYS}
    for c in matched:
        for k in FLAG_KEYS:
            if c["flags"].get(k):
                flag_counts[k] += 1
                if c["injsever"] in ("4", "5"):
                    flag_ksi[k] += 1

    length_m = sum(line_length_m(coords) for coords in lines)
    length_mi = length_m / 1609.34
    per_mi = (total / length_mi) if length_mi > 0 else 0
    score = round(fatal * 10 + serious * 4 + total * 0.5 + per_mi * 0.5)

    return {
        "risk": risk_label(score),
        "score": score,
        "crash_count": total,
        "fatal_count": fatal,
        "serious_injury_count": serious,
        "ksi_count": ksi,
        "ksi_per_mile": round(ksi / length_mi, 2) if length_mi > 0 else 0,
        "length_mi": round(length_mi, 2),
        "years": dict(sorted(years.items())),
        "time_of_day": dict(tod),
        "crash_factors": flag_counts,
        "crash_factors_ksi": flag_ksi,
        "fatal_source_breakdown": dict(Counter(
            c["source"] for c in matched if c["injsever"] == "5")),
    }


def main():
    crash_pts = load_crash_pts()
    print(f"Loaded {len(crash_pts)} crashes for count refresh")
    d = json.loads(CORR.read_text())
    updated = 0
    for feat in d["features"]:
        stats = corridor_stats(lines_of(feat.get("geometry")), crash_pts)
        if stats is None:
            print(f"[{feat['properties'].get('id')}] no geometry — left unchanged")
            continue
        feat["properties"].update(stats)   # only the derived fields; curated fields untouched
        updated += 1
        p = feat["properties"]
        print(f"[{p['id']}] {p['crash_count']:4d} crashes | {p['fatal_count']:2d}F "
              f"{p['serious_injury_count']:3d}S | score {p['score']:4d} ({p['risk']})")
    CORR.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    print(f"Refreshed counts on {updated} corridors (curated fields preserved)")


if __name__ == "__main__":
    main()
