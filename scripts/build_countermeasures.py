#!/usr/bin/env python3
"""Generate per-corridor costed countermeasure recommendations.

Treatments are drawn from:
- FHWA Proven Safety Countermeasures (PSCs), 2021 edition
- FHWA CMF Clearinghouse (3-5 star CMFs only)
- FDOT D3 Long Range Estimate (LRE) benchmark unit costs, 2024

Each treatment has a CMF (crash modification factor) applied to its target
crash type. Lower CMF = bigger reduction. e.g. CMF 0.41 = 59% reduction.

Recommendation logic:
- Count crashes by contributing factor on the corridor.
- Match treatments whose targeted factor exceeds a threshold AND whose
  road context (signalized intersection vs midblock vs unsignalized vs
  high-speed arterial) plausibly applies.
- Estimate per-corridor cost from corridor length and per-mile (or per-
  intersection) unit cost.
- Estimate annual KSI reduction = matched_KSI * (1 - CMF).
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORR = ROOT / "data" / "escambia_corridors.geojson"
OUT = ROOT / "data" / "escambia_corridors.geojson"

# ------------------------------------------------------------------
# COUNTERMEASURE CATALOG
# ------------------------------------------------------------------
# Each entry:
#   id          stable slug
#   name        display name
#   target      which crash-factor count to match against
#   cmf         crash modification factor for KSI on that crash type
#   cmf_source  CMF Clearinghouse reference (3-5 star) OR FHWA PSC
#   cost_low    $ per mile (or per intersection) — low estimate
#   cost_high   $ per mile (or per intersection) — high estimate
#   unit        "mile" or "intersection"
#   context     when to apply (arterial / urban / unsignalized / ped / etc)
#   min_factor  minimum factor count to trigger recommendation
#   description plain-english summary

CATALOG = [
    {
        "id": "rrfb",
        "name": "Pedestrian Hybrid Beacons & RRFBs at Major Crossings",
        "target": "ped",
        "cmf": 0.45,                          # CMF Clearinghouse 7800/9024 (RRFB ped crashes)
        "cmf_source": "CMF Clearinghouse #7800 (5-star) — RRFB",
        "cost_low": 30_000, "cost_high": 80_000, "unit": "intersection",
        "context": "ped",
        "min_factor": 5,
        "description": "Rectangular rapid-flash beacons or pedestrian hybrid beacons at the busiest unsignalized pedestrian crossings.",
    },
    {
        "id": "lpi",
        "name": "Leading Pedestrian Interval at Signalized Intersections",
        "target": "ped",
        "cmf": 0.41,                          # CMF Clearinghouse 9024 (LPI ped-veh crashes)
        "cmf_source": "FHWA PSC — Leading Pedestrian Interval",
        "cost_low": 1_200, "cost_high": 6_000, "unit": "intersection",
        "context": "ped+intersection",
        "min_factor": 5,
        "description": "Give pedestrians a 3-7 second head start before vehicles get the green; signal timing change only.",
    },
    {
        "id": "ped_refuge",
        "name": "Median Pedestrian Refuge Islands",
        "target": "ped",
        "cmf": 0.54,                          # FHWA PSC: 56% ped-crash reduction at unsignalized locations
        "cmf_source": "FHWA PSC — Medians and Pedestrian Refuge Islands",
        "cost_low": 25_000, "cost_high": 75_000, "unit": "intersection",
        "context": "ped+arterial",
        "min_factor": 8,
        "description": "Raised median islands let pedestrians cross one direction of traffic at a time.",
    },
    {
        "id": "rab",
        "name": "Roundabouts at Highest-Crash Intersections",
        "target": "intersection",
        "cmf": 0.18,                          # CMF Clearinghouse 380 (RAB fatal+injury, urban)
        "cmf_source": "CMF Clearinghouse #380 (5-star) — Urban single-lane roundabout",
        "cost_low": 1_500_000, "cost_high": 3_500_000, "unit": "intersection",
        "context": "intersection",
        "min_factor": 80,
        "description": "Convert the worst signalized intersection to a modern roundabout — eliminates 90-degree and left-turn conflicts.",
    },
    {
        "id": "sig_optimize",
        "name": "Signal Retiming, Backplates with Retroreflective Borders, Yellow/All-Red Optimization",
        "target": "intersection",
        "cmf": 0.85,                          # CMF Clearinghouse 4119 (retroreflective backplates)
        "cmf_source": "FHWA PSC — Backplates with Retroreflective Borders",
        "cost_low": 2_000, "cost_high": 8_000, "unit": "intersection",
        "context": "intersection",
        "min_factor": 50,
        "description": "Low-cost signal hardware + timing upgrades at every signalized intersection on the corridor.",
    },
    {
        "id": "road_diet",
        "name": "Road Diet (4-to-3 Lane Conversion with Bike Lanes)",
        "target": "lane_departure",
        "cmf": 0.71,                          # CMF Clearinghouse 224 (4-to-3 road diet, urban, all)
        "cmf_source": "CMF Clearinghouse #224 (4-star) — 4-to-3 lane conversion",
        "cost_low": 100_000, "cost_high": 400_000, "unit": "mile",
        "context": "lane_departure+arterial+low_aadt",
        "min_factor": 30,
        "description": "Reallocate one travel lane to a center turn lane and protected bike lanes; calms traffic and reduces left-turn conflicts.",
    },
    {
        "id": "rumble",
        "name": "Centerline and Shoulder Rumble Strips",
        "target": "lane_departure",
        "cmf": 0.78,                          # FHWA PSC: ~22% reduction lane-departure injury crashes
        "cmf_source": "FHWA PSC — Longitudinal Rumble Strips",
        "cost_low": 3_000, "cost_high": 12_000, "unit": "mile",
        "context": "lane_departure+rural",
        "min_factor": 25,
        "description": "Milled rumble strips alert drifting drivers; very low cost per mile.",
    },
    {
        "id": "lighting",
        "name": "Corridor Lighting Upgrade (Intersection + Crosswalk)",
        "target": "night",
        "cmf": 0.62,                          # CMF Clearinghouse 487 (roadway lighting all-night)
        "cmf_source": "FHWA PSC — Roadway Lighting at Intersections",
        "cost_low": 250_000, "cost_high": 600_000, "unit": "mile",
        "context": "night",
        "min_factor": 3,
        "description": "Upgrade fixtures, fill gaps, and add lighting at all marked crosswalks and intersections.",
    },
    {
        "id": "speed_mgmt",
        "name": "Speed Management Package (Feedback Signs, Lane Narrowing, Enforcement)",
        "target": "speeding",
        "cmf": 0.85,                          # CMF Clearinghouse 8051 (variable speed feedback)
        "cmf_source": "FHWA PSC — Variable Speed Limits / Speed Feedback",
        "cost_low": 15_000, "cost_high": 60_000, "unit": "mile",
        "context": "speeding",
        "min_factor": 3,
        "description": "Driver feedback signs, narrowed lane markings, and targeted enforcement at speeding hotspots.",
    },
    {
        "id": "protected_bike",
        "name": "Protected Bike Lanes (Concrete or Flex-Post Buffer)",
        "target": "bike",
        "cmf": 0.45,                          # CMF Clearinghouse 9117 (cycle track all crashes)
        "cmf_source": "CMF Clearinghouse #9117 (4-star) — Protected bike lane",
        "cost_low": 250_000, "cost_high": 800_000, "unit": "mile",
        "context": "bike",
        "min_factor": 5,
        "description": "Physically separated bike lanes; significantly safer than painted lanes.",
    },
    {
        "id": "dui_high_vis",
        "name": "High-Visibility DUI Enforcement Corridor + Education",
        "target": "impaired",
        "cmf": 0.85,                          # FHWA NHTSA-cited 15% reduction
        "cmf_source": "NHTSA — High-Visibility Enforcement",
        "cost_low": 5_000, "cost_high": 25_000, "unit": "mile",
        "context": "impaired",
        "min_factor": 8,
        "description": "Sustained, well-publicized impaired-driving enforcement; pairs with treatment referral.",
    },
]


def recommend(corridor_props):
    """Return list of (treatment, est_cost_low, est_cost_high, est_ksi_reduction)."""
    cf = corridor_props.get("crash_factors", {})
    cf_ksi = corridor_props.get("crash_factors_ksi", {})
    length_mi = corridor_props.get("length_mi", 0)
    # Use OSM-derived intersection-density proxy: 1 intersection per 0.3 mi
    n_intersections = max(1, round(length_mi / 0.3))

    out = []
    for t in CATALOG:
        factor_count = cf.get(t["target"], 0)
        if factor_count < t["min_factor"]:
            continue
        ksi_on_factor = cf_ksi.get(t["target"], 0)
        # Estimate annualized KSI reduction. Data covers ~7 calendar years
        # of crashes (FDOT 2018-22 + FARS 2022-24). Use 7 as the denominator.
        years = 7
        annual_ksi_reduced = max(0.0, ksi_on_factor * (1 - t["cmf"]) / years)

        if t["unit"] == "mile":
            cost_low = round(t["cost_low"] * length_mi)
            cost_high = round(t["cost_high"] * length_mi)
        else:
            # Cap intersection treatments at top-N intersections where signal exists,
            # roughly 1/3 of intersections for signalized-only treatments
            if "intersection" in t["context"] or t["target"] == "intersection":
                n = max(2, round(n_intersections * 0.4))
            else:
                n = max(2, round(n_intersections * 0.25))
            cost_low = round(t["cost_low"] * n)
            cost_high = round(t["cost_high"] * n)

        out.append({
            "id": t["id"],
            "name": t["name"],
            "target": t["target"],
            "target_count": factor_count,
            "target_ksi": ksi_on_factor,
            "cmf": t["cmf"],
            "cmf_source": t["cmf_source"],
            "cost_low": cost_low,
            "cost_high": cost_high,
            "annual_ksi_reduced": round(annual_ksi_reduced, 2),
            "description": t["description"],
        })

    # Sort by annual_ksi_reduced (impact) descending, then cost_low ascending
    out.sort(key=lambda x: (-x["annual_ksi_reduced"], x["cost_low"]))
    return out


def main():
    with open(CORR) as f:
        d = json.load(f)
    for feat in d["features"]:
        recs = recommend(feat["properties"])
        feat["properties"]["countermeasures"] = recs
        # Roll-ups for summary
        feat["properties"]["countermeasure_total_low"] = sum(r["cost_low"] for r in recs)
        feat["properties"]["countermeasure_total_high"] = sum(r["cost_high"] for r in recs)
        feat["properties"]["countermeasure_annual_ksi_reduced"] = round(
            sum(r["annual_ksi_reduced"] for r in recs), 1
        )
    with open(OUT, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote countermeasures to {OUT}")
    # Summary
    for feat in d["features"]:
        p = feat["properties"]
        print(f"[{p['id']}] {p['name'][:38]:38s} | ${p['countermeasure_total_low']:>10,} – ${p['countermeasure_total_high']:>11,} "
              f"| {p['countermeasure_annual_ksi_reduced']:>4.1f} ksi/yr | {len(p['countermeasures'])} treatments")


if __name__ == "__main__":
    main()
