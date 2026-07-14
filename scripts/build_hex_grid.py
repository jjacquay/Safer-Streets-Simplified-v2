#!/usr/bin/env python3
"""Build a coarse hex-like grid (actually 100m square grid for simplicity)
of crash counts across Escambia. This anonymizes individual crash points
into aggregate counts per cell.

Outputs data/escambia_crash_grid.geojson (commit; aggregated only).
"""
import json
import math
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
CRASHES_FDOT = ROOT / "data" / "private" / "escambia_crashes_full.geojson"
CRASHES_FARS = ROOT / "data" / "private" / "escambia_fars_fatals.geojson"
OUT = ROOT / "data" / "escambia_crash_grid.geojson"

# ~100m grid
CELL_M = 150.0
DEG_LAT_M = 111_320.0

# Suppress cells with very low counts to limit re-identification risk
MIN_CELL_COUNT = 2


def main():
    crashes = {"features": []}
    with open(CRASHES_FDOT) as f:
        crashes["features"].extend(json.load(f)["features"])
    with open(CRASHES_FARS) as f:
        crashes["features"].extend(json.load(f)["features"])

    # Aggregate by grid cell. Use mid-latitude for lon scaling (Escambia ~30.5°N).
    mid_lat = 30.50
    cell_lat = CELL_M / DEG_LAT_M
    cell_lon = CELL_M / (DEG_LAT_M * math.cos(math.radians(mid_lat)))

    counts = defaultdict(lambda: {"total": 0, "fatal": 0, "serious": 0})
    for feat in crashes["features"]:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if lon is None or lat is None:
            continue
        # Sanity: Escambia rough bounds
        if not (-87.75 <= lon <= -87.10 and 30.20 <= lat <= 31.05):
            continue
        ix = math.floor(lon / cell_lon)
        iy = math.floor(lat / cell_lat)
        key = (ix, iy)
        c = counts[key]
        c["total"] += 1
        sev = str(feat["properties"].get("INJSEVER") or "")
        if sev == "5":
            c["fatal"] += 1
        elif sev == "4":
            c["serious"] += 1

    features = []
    for (ix, iy), c in counts.items():
        if c["total"] < MIN_CELL_COUNT:
            continue
        x0 = ix * cell_lon
        y0 = iy * cell_lat
        x1 = x0 + cell_lon
        y1 = y0 + cell_lat
        poly = [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]
        # weight = log scale for heatmap
        weight = round(math.log1p(c["total"]) * 10) / 10
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": poly},
            "properties": {
                "total": c["total"],
                "fatal": c["fatal"],
                "serious": c["serious"],
                "weight": weight,
            },
        })

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "cell_size_m": CELL_M,
            "min_cell_count": MIN_CELL_COUNT,
            "crash_years": "2018-2022 (FDOT public KSI)",
            "note": "Aggregated counts only. Cells below threshold suppressed.",
        },
        "features": features,
    }
    with open(OUT, "w") as f:
        json.dump(fc, f)
    suppressed = sum(1 for _, c in counts.items() if c["total"] < MIN_CELL_COUNT)
    print(f"Wrote {len(features)} cells ({suppressed} cells suppressed) to {OUT}")


if __name__ == "__main__":
    main()
