"""
Pick ONE FHWA Proven Safety Countermeasure per corridor based on the
corridor's dominant crash factor (not just highest CMF).

Tie-break order favors variety so the spotlight feels different for
each corridor and showcases the breadth of FHWA's PSC library.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORR = ROOT / "data" / "escambia_corridors.geojson"
PSC_INDEX = ROOT / "data" / "fhwa_psc_index.json"

# Map a dominant crash factor to the FHWA PSC slug we want to spotlight.
# Order in each list = preference (first one we haven't used yet wins,
# unless we run out of unused, in which case we fall back to first).
FACTOR_PSC_PREF = {
    "ped": [
        "rectangular-rapid-flashing-beacons-rrfb",
        "leading-pedestrian-interval",
        "medians-and-pedestrian-refuge-islands-urban-and-suburban-areas",
        "pedestrian-hybrid-beacons",
        "crosswalk-visibility-enhancements",
        "walkways",
    ],
    "bike": [
        "bicycle-lanes",
    ],
    "intersection": [
        "roundabouts",
        "dedicated-left-and-right-turn-lanes-intersections",
        "backplates-retroreflective-borders",
        "systemic-application-multiple-low-cost-countermeasures-stop",
    ],
    "lane_departure": [
        "longitudinal-rumble-strips-and-stripes-two-lane-roads",
    ],
    "speeding": [
        "appropriate-speed-limits-all-road-users",
        "speed-safety-cameras",
        "road-diets-roadway-reconfiguration",
    ],
    "impaired": [
        "lighting",
    ],
    "night": [
        "lighting",
    ],
}

# Default fallback when nothing matches above (rare).
DEFAULT_PSC = "road-safety-audit"


def load_index():
    return {p["slug"]: p for p in json.loads(PSC_INDEX.read_text())}


# Per-mile weights: a factor that's small absolutely but is a high SHARE of
# the corridor's KSI deserves to be the spotlight. We weight intersection
# down because it's nearly universal and not the most teachable PSC pick.
FACTOR_BOOST = {
    "ped": 4.0,        # vulnerable user; equity + severity
    "bike": 4.0,
    "lane_departure": 2.0,
    "impaired": 1.8,
    "night": 1.6,
    "speeding": 1.4,
    "intersection": 1.0,
}


def _rank_factors(factors_ksi, factors_count):
    """Return all relevant factors in descending order of severity-weighted score."""
    candidates = []
    for fac in FACTOR_PSC_PREF:
        ksi = (factors_ksi or {}).get(fac, 0)
        cnt = (factors_count or {}).get(fac, 0)
        boost = FACTOR_BOOST.get(fac, 1.0)
        score = (ksi * 10 + cnt) * boost
        if score > 0:
            candidates.append((fac, score))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def pick_dominant_factor(factors_ksi, factors_count):
    """Return the highest-signal crash factor.

    Severity-weighted with per-factor boosts. KSI counts ~10x crashes.
    Vulnerable-user factors get an extra boost so they don't get drowned
    out by intersection-flagged crashes (which are ubiquitous in FDOT).
    """
    candidates = []
    for fac, _ in FACTOR_PSC_PREF.items():
        ksi = (factors_ksi or {}).get(fac, 0)
        cnt = (factors_count or {}).get(fac, 0)
        boost = FACTOR_BOOST.get(fac, 1.0)
        score = (ksi * 10 + cnt) * boost
        candidates.append((fac, score, ksi, cnt))
    candidates.sort(key=lambda x: x[1], reverse=True)
    if not candidates or candidates[0][1] == 0:
        return None
    return candidates[0][0]


def main():
    psc_idx = load_index()
    data = json.loads(CORR.read_text())
    used_count = {}  # slug -> times assigned

    # Sort corridors by score descending so the highest-priority corridor
    # gets first dibs on its preferred PSC.
    feats = sorted(
        data["features"],
        key=lambda f: f["properties"].get("score", 0),
        reverse=True,
    )

    assignments = {}
    for feat in feats:
        p = feat["properties"]
        cid = p["id"]
        fac_ksi = p.get("crash_factors_ksi", {})
        fac_cnt = p.get("crash_factors", {})
        dominant = pick_dominant_factor(fac_ksi, fac_cnt)
        prefs = FACTOR_PSC_PREF.get(dominant, [DEFAULT_PSC]) if dominant else [DEFAULT_PSC]

        # Diversity-aware pick: prefer a PSC matching the dominant factor
        # that hasn't been used yet. If the factor's preferred PSCs are
        # exhausted, try the next-best factor for this corridor.
        ranked_factors = _rank_factors(fac_ksi, fac_cnt)
        chosen = None
        for fac in ranked_factors:
            for slug in FACTOR_PSC_PREF.get(fac, []):
                if used_count.get(slug, 0) < 2:  # cap each PSC at 2 corridors
                    chosen = slug
                    dominant = fac
                    break
            if chosen:
                break
        if chosen is None:
            # All caps hit — fall back to least-used pref of the top factor.
            top_fac = ranked_factors[0] if ranked_factors else None
            opts = FACTOR_PSC_PREF.get(top_fac, [DEFAULT_PSC])
            chosen = min(opts, key=lambda s: used_count.get(s, 0))
            dominant = top_fac or dominant

        used_count[chosen] = used_count.get(chosen, 0) + 1
        assignments[cid] = {
            "dominant_factor": dominant or "n/a",
            "psc_slug": chosen,
            "psc_title": psc_idx[chosen]["title"],
            "psc_url": psc_idx[chosen]["url"],
        }

    # Write assignments back into the geojson features
    for feat in data["features"]:
        cid = feat["properties"]["id"]
        if cid in assignments:
            feat["properties"]["psc"] = assignments[cid]

    CORR.write_text(json.dumps(data, separators=(",", ":")))

    print(f"Assigned PSC to {len(assignments)} corridors")
    print(f"Distribution:")
    from collections import Counter
    c = Counter(a["psc_slug"] for a in assignments.values())
    for slug, n in c.most_common():
        print(f"  {n:2d}  {slug}")
    print()
    print(f"Per corridor:")
    for cid in sorted(assignments):
        a = assignments[cid]
        print(f"  {cid}: {a['dominant_factor']:14s} -> {a['psc_slug']}")


if __name__ == "__main__":
    main()
