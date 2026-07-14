#!/usr/bin/env python3
"""Build the final, publishable corridor GeoJSON.

For each corridor:
- Take its OSM ways (LineStrings), keep them as a MultiLineString geometry
- Buffer each segment ~30m and count crashes inside
- Compute fatal/serious/total counts, time-of-day buckets, and length
- v3: pull crash-type / contributing-factor profile (ped, bike, intersection,
  lane departure, speeding, impaired, distracted, night, motorcycle)
- v3: merge FDOT 2018-2022 (all severities, full attrs) with FARS 2022-2024
  Escambia fatals — extends temporal coverage past FL SB 1614 60-day window
"""
import json
import math
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "scripts" / "corridors_master.json"
CENTERLINES = ROOT / "data" / "private" / "osm_centerlines.geojson"
CRASHES_FDOT = ROOT / "data" / "private" / "escambia_crashes_full.geojson"
CRASHES_FARS = ROOT / "data" / "private" / "escambia_fars_fatals.geojson"
OUT = ROOT / "data" / "escambia_corridors.geojson"

BUFFER_M = 30.0  # meters
DEG_LAT_M = 111_320.0  # meters per degree latitude (approx)


def deg_lat_per_m():
    return 1.0 / DEG_LAT_M


def deg_lon_per_m(lat_deg):
    return 1.0 / (DEG_LAT_M * max(0.01, math.cos(math.radians(lat_deg))))


def haversine_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def line_length_m(coords):
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def point_seg_distance_m(p, a, b):
    lat_mid = (a[1] + b[1] + p[1]) / 3.0
    cos_lat = max(0.01, math.cos(math.radians(lat_mid)))

    def to_xy(c):
        return (c[0] * cos_lat * DEG_LAT_M, c[1] * DEG_LAT_M)

    px, py = to_xy(p)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def point_to_lines_min_m(p, lines):
    best = float("inf")
    for coords in lines:
        for i in range(len(coords) - 1):
            d = point_seg_distance_m(p, coords[i], coords[i + 1])
            if d < best:
                best = d
                if best < 1.0:
                    return best
    return best


def crash_in_bbox(lat, lon, bbox, pad_m=BUFFER_M):
    w, s, e, n = bbox
    pad_lat = pad_m * deg_lat_per_m()
    pad_lon = pad_m * deg_lon_per_m((s + n) / 2)
    return (w - pad_lon) <= lon <= (e + pad_lon) and (s - pad_lat) <= lat <= (n + pad_lat)


def is_y(v):
    """FDOT booleans: 'Y' / 'N' / None."""
    return str(v).upper().startswith("Y") if v is not None else False


def fars_is_dark(lighting):
    """FARS LGT_CONDNAME: 'Dark - Lighted', 'Dark - Not Lighted', etc."""
    if not lighting:
        return False
    return "Dark" in lighting


def fdot_is_dark(code):
    """FDOT LGHT_COND_CD: 2=Dawn, 3=Dusk, 4=Dark-Lighted, 5=Dark-Not Lighted, 6=Dark-Unknown."""
    return str(code) in ("4", "5", "6")


def crash_flags(props, source):
    """Return contributing-factor flags as a dict of bools."""
    if source == "FARS":
        return {
            "ped": (props.get("NUMBER_OF_PEDESTRIANS") or 0) > 0,
            "bike": False,  # FARS encodes via person-type; we treat fatals separately
            "intersection": "Not at" not in (props.get("INT_TYPE") or "Not"),
            "lane_departure": "Roadside" in (props.get("HARM_EVNT_NAME") or "")
                or "Off Roadway" in (props.get("REL_ROAD") or ""),
            "night": fars_is_dark(props.get("LIGHTING")),
            "motorcycle": False,
            "speeding": False,
            "impaired": False,
            "distracted": False,
        }
    # FDOT
    return {
        "ped": is_y(props.get("PEDESTRIAN_RELATED_IND")),
        "bike": is_y(props.get("BICYCLIST_RELATED_IND")),
        "intersection": is_y(props.get("INTERSECTION_IND")),
        "lane_departure": is_y(props.get("LANE_DEPARTURE_IND")),
        "night": fdot_is_dark(props.get("LGHT_COND_CD")),
        "motorcycle": is_y(props.get("MOTORCYCLE_INVOLVED_IND")),
        "speeding": is_y(props.get("SPEEDING_IND")) or is_y(props.get("AGGRESSIVE_DRIVING_IND")),
        "impaired": is_y(props.get("IMPAIRED_DRIVER_IND")),
        "distracted": is_y(props.get("DISTRACTED_DRIVER_IND")),
    }


def load_crash_pts():
    """Return unified crash list with source flag."""
    pts = []
    with open(CRASHES_FDOT) as f:
        fdot = json.load(f)
    for feat in fdot["features"]:
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        p = feat["properties"]
        pts.append({
            "source": "FDOT",
            "lon": coords[0], "lat": coords[1],
            "year": p.get("CALENDAR_YEAR"),
            "time": p.get("CRASH_TIME") or "",
            "injsever": str(p.get("INJSEVER") or ""),
            "n_injured": p.get("NUMBER_OF_INJURED") or 0,
            "n_serious": p.get("NUMBER_OF_SERIOUS_INJURIES") or 0,
            "n_killed": p.get("NUMBER_OF_KILLED") or 0,
            "flags": crash_flags(p, "FDOT"),
        })
    with open(CRASHES_FARS) as f:
        fars = json.load(f)
    # Deduplicate FARS 2022 against FDOT 2022 — FDOT 2022 only has 189 records left
    # and just 8 of those were fatal in the prior pipeline; FARS will be more complete.
    # Strategy: drop FDOT 2022 fatals (we'll use FARS for that year+severity)
    pts = [p for p in pts if not (p["source"] == "FDOT" and p["year"] == 2022 and p["injsever"] == "5")]
    for feat in fars["features"]:
        geom = feat.get("geometry") or {}
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        p = feat["properties"]
        pts.append({
            "source": "FARS",
            "lon": coords[0], "lat": coords[1],
            "year": p.get("CALENDAR_YEAR"),
            "time": f"{p.get('HOUR', 0):02d}00",
            "injsever": "5",
            "n_injured": 0,
            "n_serious": 0,
            "n_killed": p.get("NUMBER_OF_KILLED") or 1,
            "flags": crash_flags(p, "FARS"),
        })
    return pts


def main():
    with open(MASTER) as f:
        master = json.load(f)
    with open(CENTERLINES) as f:
        centerlines = json.load(f)

    ways_by_corridor = {}
    for feat in centerlines["features"]:
        cid = feat["properties"].get("corridor_id")
        if not cid:
            continue
        ways_by_corridor.setdefault(cid, []).append(feat["geometry"]["coordinates"])

    crash_pts = load_crash_pts()
    by_year = Counter(c["year"] for c in crash_pts)
    by_src = Counter(c["source"] for c in crash_pts)
    print(f"Loaded {len(crash_pts)} crashes")
    print(f"  by source: {dict(by_src)}")
    print(f"  by year: {dict(sorted(by_year.items()))}")

    def risk_label(score):
        if score >= 70: return "very-high"
        if score >= 40: return "high"
        if score >= 20: return "moderate"
        return "lower"

    out_features = []
    for corridor in master["corridors"]:
        cid = corridor["id"]
        lines = ways_by_corridor.get(cid, [])
        if not lines:
            print(f"[{cid}] no centerline, skipping")
            continue
        all_lons, all_lats = [], []
        for coords in lines:
            for pt in coords:
                all_lons.append(pt[0]); all_lats.append(pt[1])
        bbox = [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]
        candidates = [c for c in crash_pts if crash_in_bbox(c["lat"], c["lon"], bbox)]
        matched = []
        for c in candidates:
            d = point_to_lines_min_m((c["lon"], c["lat"]), lines)
            if d <= BUFFER_M:
                matched.append(c)

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

        # Contributing-factor breakdown
        flag_keys = ["ped", "bike", "intersection", "lane_departure",
                     "night", "motorcycle", "speeding", "impaired", "distracted"]
        flag_counts = {k: 0 for k in flag_keys}
        flag_ksi = {k: 0 for k in flag_keys}
        for c in matched:
            for k in flag_keys:
                if c["flags"].get(k):
                    flag_counts[k] += 1
                    if c["injsever"] in ("4", "5"):
                        flag_ksi[k] += 1

        length_m = sum(line_length_m(coords) for coords in lines)
        length_mi = length_m / 1609.34
        per_mi = (total / length_mi) if length_mi > 0 else 0
        score = round(fatal * 10 + serious * 4 + total * 0.5 + per_mi * 0.5)
        risk = risk_label(score)

        props = {
            "id": cid,
            "name": corridor["name"],
            "place": corridor["place"],
            "summary": corridor["summary"],
            "message": corridor["message"],
            "treatment": corridor["treatment"],
            "tags": corridor["tags"],
            "sources": corridor["sources"],
            "risk": risk,
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
                c["source"] for c in matched if c["injsever"] == "5"
            )),
        }

        out_features.append({
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": lines},
            "properties": props,
        })
        print(f"[{cid}] {corridor['name'][:38]:38s} | {length_mi:5.2f}mi | "
              f"{total:4d} crashes | {fatal:2d}F {serious:3d}S | score {score:4d} ({risk})")

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "generated": "2026-06-28",
            "buffer_m": BUFFER_M,
            "crash_years": "FDOT 2018-2022 (all severities) + FARS 2022-2024 (fatals)",
            "data_vintage_note": (
                "Florida SB 1614 (March 2023) created a 60-day public-access "
                "privacy window. Post-2022 non-fatal crashes are not publicly "
                "available without a Signal4 authenticated account. We use "
                "NHTSA FARS for 2023-2024 fatal data so the highest-severity "
                "tier stays current."
            ),
            "centerline_source": "OpenStreetMap via Overpass API",
            "corridor_sources": "ECRC SS4A HIN, Pensacola ATP HIN, FDOT HSIP, MVP list",
            "note": "Counts are aggregated within buffer; raw points not published.",
        },
        "features": out_features,
    }
    with open(OUT, "w") as f:
        json.dump(fc, f)
    print(f"\nWrote {len(out_features)} corridors to {OUT}")


if __name__ == "__main__":
    main()
