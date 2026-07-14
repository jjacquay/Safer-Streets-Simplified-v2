#!/usr/bin/env python3
"""Find the highest-crash sub-segment within each corridor ("hotspot windows").

Simplified analog of Toole Design's Safer Streets Priority Finder (SSPF)
sliding-window methodology (https://github.com/tooledesign/Safer-Streets-Priority-Finder,
MIT license). SSPF slides overlapping windows across an ENTIRE road network to
discover high-injury corridors from scratch. This script does a related but
smaller job: given a corridor that is ALREADY selected (from the curated
high-injury network), it divides that corridor into fixed-length,
non-overlapping windows and reports which window concentrates the most crash
risk — "here is the specific mile of Cervantes St to fix first," not just
"Cervantes St is risky." That's a real simplification of SSPF's approach, not
an attempt to reproduce it: no overlap/sliding step, no whole-network
discovery, no statistical smoothing across windows.

Method:
  1. Re-run the same 30 m buffer crash join used by build_corridor_geojson.py
     to get each corridor's matched crash points.
  2. Project each matched crash onto the corridor centerline to get its
     along-corridor distance in miles (nearest segment, nearest point).
  3. Bucket crashes into fixed WINDOW_MI-mile windows measured from the corridor
     start. MultiLineString parts are first chained into one connected route
     (order_line_parts) so cumulative distance carries across parts; without this
     the distance resets per part and every crash collapses into mile 0. Ordering
     follows greedy endpoint-chaining, not a true route-direction guarantee, but
     is stable and reproducible corridor-to-corridor.
  4. Report the single window with the most KSI (ties broken by crash count).

Adds an additive `hotspot` property; never touches curated fields. Skips
corridors shorter than 2x WINDOW_MI (too short to sub-divide meaningfully) or
with fewer than MIN_CRASHES_FOR_HOTSPOT matched crashes.
"""
import json
import math
from collections import deque
from pathlib import Path

from build_corridor_geojson import load_crash_pts, crash_in_bbox, line_length_m, haversine_m, BUFFER_M

ROOT = Path(__file__).resolve().parent.parent
CORR = ROOT / "data" / "escambia_corridors.geojson"

WINDOW_MI = 0.5
MIN_CORRIDOR_MI_FOR_WINDOWS = 1.0   # need at least 2 windows to be meaningful
MIN_CRASHES_FOR_HOTSPOT = 10        # below this, a "hotspot" claim is noise
METERS_PER_MILE = 1609.34


def _to_xy(pt, lat_mid):
    """Rough local planar projection (meters), matching build_corridor_geojson's approach."""
    cos_lat = max(0.01, math.cos(math.radians(lat_mid)))
    return (pt[0] * cos_lat * 111_320.0, pt[1] * 111_320.0)


def project_onto_segment(p, a, b):
    """Return (distance_m, t) of point p's projection onto segment a->b. t in [0,1]."""
    lat_mid = (a[1] + b[1] + p[1]) / 3.0
    px, py = _to_xy(p, lat_mid)
    ax, ay = _to_xy(a, lat_mid)
    bx, by = _to_xy(b, lat_mid)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), t


def order_line_parts(lines):
    """Chain a MultiLineString's parts into a single, connected ordering.

    OSM centerline parts arrive in arbitrary order and are often individually
    reversed (a 4.5 mi corridor can be 35 parts whose consecutive end→start gaps
    are kilometres apart). Concatenating them as-listed makes cumulative
    along-corridor distance meaningless. Greedily append the part whose nearest
    endpoint follows the current route end, flipping it when its far end connects
    better, so along-distance is as monotonic as the geometry allows. Returns the
    ordered list of parts (each a coord list); gaps between parts are NOT counted
    as distance by the caller, so the total still equals the sum of part lengths.

    Known limitation: for a divided highway represented as two parallel
    centerlines, chaining walks up one side and back down the other, so
    along-distance folds at the far end. Good enough to spread crashes across
    windows instead of collapsing them to mile 0; not a substitute for a true
    route-dissolve. Verify regenerated numbers against CI output before trusting.
    """
    parts = [list(p) for p in lines if len(p) >= 2]
    if not parts:
        return []
    # Double-ended chaining: grow the route from BOTH its head and tail so an
    # arbitrary (possibly interior) starting part still yields a linear route
    # rather than one that folds back on itself.
    route = deque([parts.pop(0)])
    while parts:
        head = route[0][0]       # first coordinate of the route
        tail = route[-1][-1]     # last coordinate of the route
        best = None              # (dist, index, where, flip)
        for i, p in enumerate(parts):
            s, e = p[0], p[-1]
            for d, where, flip in (
                (haversine_m(tail, s), "append", False),
                (haversine_m(tail, e), "append", True),
                (haversine_m(head, e), "prepend", False),
                (haversine_m(head, s), "prepend", True),
            ):
                if best is None or d < best[0]:
                    best = (d, i, where, flip)
        _, i, where, flip = best
        p = parts.pop(i)
        if flip:
            p = p[::-1]
        if where == "append":
            route.append(p)
        else:
            route.appendleft(p)
    return list(route)


def along_distance_mi(pt, ordered_parts):
    """Distance-along-corridor (miles) of pt's nearest projection onto the route.

    `ordered_parts` must come from order_line_parts. Cumulative distance carries
    ACROSS parts (via base_m) so a crash near a later part is measured from the
    corridor start, not reset to mile 0 for each part. Gaps between parts are not
    added, so the maximum along-distance equals the sum of part lengths (matching
    the window count derived from corridor length).
    """
    best = {"dist_m": float("inf"), "along_m": 0.0}
    base_m = 0.0
    for coords in ordered_parts:
        cum_m = 0.0
        for i in range(len(coords) - 1):
            a, b = coords[i], coords[i + 1]
            seg_len_m = haversine_m(a, b)
            d, t = project_onto_segment(pt, a, b)
            if d < best["dist_m"]:
                best = {"dist_m": d, "along_m": base_m + cum_m + t * seg_len_m}
            cum_m += seg_len_m
        base_m += cum_m
    return best["along_m"] / METERS_PER_MILE


def corridor_hotspot(lines, crash_pts):
    all_lons = [pt[0] for coords in lines for pt in coords]
    all_lats = [pt[1] for coords in lines for pt in coords]
    if not all_lons:
        return None
    bbox = [min(all_lons), min(all_lats), max(all_lons), max(all_lats)]
    candidates = [c for c in crash_pts if crash_in_bbox(c["lat"], c["lon"], bbox)]

    length_mi = sum(line_length_m(coords) for coords in lines) / METERS_PER_MILE
    if length_mi < MIN_CORRIDOR_MI_FOR_WINDOWS:
        return None

    matched = []
    for c in candidates:
        pt = (c["lon"], c["lat"])
        # reuse the corridor's own buffer distance check via along_distance_mi's
        # nearest-segment search, but we still need the strict buffer filter:
        d_mi = None
        best_d_m = float("inf")
        for coords in lines:
            for i in range(len(coords) - 1):
                d, _t = project_onto_segment(pt, coords[i], coords[i + 1])
                if d < best_d_m:
                    best_d_m = d
        if best_d_m <= BUFFER_M:
            matched.append(c)

    total = len(matched)
    if total < MIN_CRASHES_FOR_HOTSPOT:
        return None

    n_windows = max(2, math.ceil(length_mi / WINDOW_MI))
    windows = [{"start_mi": round(i * WINDOW_MI, 2),
                "end_mi": round(min((i + 1) * WINDOW_MI, length_mi), 2),
                "crash_count": 0, "ksi_count": 0} for i in range(n_windows)]

    # Order the parts into one connected route so along-distance accumulates
    # across the whole corridor instead of resetting per (arbitrarily-ordered) part.
    ordered = order_line_parts(lines)
    for c in matched:
        along_mi = along_distance_mi((c["lon"], c["lat"]), ordered)
        idx = min(int(along_mi / WINDOW_MI), n_windows - 1)
        windows[idx]["crash_count"] += 1
        if c["injsever"] in ("4", "5"):
            windows[idx]["ksi_count"] += 1

    worst = max(windows, key=lambda w: (w["ksi_count"], w["crash_count"]))
    if worst["crash_count"] == 0:
        return None

    return {
        "window_length_mi": WINDOW_MI,
        "worst_window": {
            "start_mi": worst["start_mi"],
            "end_mi": worst["end_mi"],
            "crash_count": worst["crash_count"],
            "ksi_count": worst["ksi_count"],
            "share_of_corridor_crashes_pct": round(worst["crash_count"] / total * 100),
        },
        "corridor_length_mi": round(length_mi, 2),
        "corridor_crash_count": total,
        "method": (
            f"Corridor divided into fixed, non-overlapping {WINDOW_MI}-mile windows; "
            "crashes assigned by nearest-point projection onto the centerline. "
            "Simplified analog of the sliding-window hotspot method in Toole Design's "
            "Safer Streets Priority Finder (which slides overlapping windows across an "
            "entire network to discover corridors from scratch; here windows are fixed "
            "and applied only within this already-selected corridor)."
        ),
    }


def main():
    d = json.loads(CORR.read_text())
    crash_pts = load_crash_pts()
    added = skipped = 0
    for feat in d["features"]:
        geom = feat.get("geometry") or {}
        lines = geom["coordinates"] if geom.get("type") == "MultiLineString" else (
            [geom["coordinates"]] if geom.get("type") == "LineString" else [])
        hotspot = corridor_hotspot(lines, crash_pts)
        if hotspot is None:
            feat["properties"].pop("hotspot", None)
            skipped += 1
            continue
        feat["properties"]["hotspot"] = hotspot
        added += 1
        p = feat["properties"]
        w = hotspot["worst_window"]
        print(f"[{p['id']}] hotspot mile {w['start_mi']}-{w['end_mi']}: "
              f"{w['crash_count']} crashes ({w['share_of_corridor_crashes_pct']}% of corridor), "
              f"{w['ksi_count']} KSI")
    CORR.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    print(f"Hotspot windows attached to {added} corridors ({skipped} skipped — too short or too few crashes)")


if __name__ == "__main__":
    main()
