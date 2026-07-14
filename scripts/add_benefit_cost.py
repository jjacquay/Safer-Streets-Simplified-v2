#!/usr/bin/env python3
"""Attach a planning-level benefit-cost analysis to each corridor.

Monetizes the countermeasure package's estimated annual KSI reduction using
published USDOT values, discounts over a multi-year horizon, and writes an
additive `bca` object onto each corridor. Never touches curated fields —
same non-destructive pattern as join_acs_to_corridors.py.

Method (kept deliberately simple and conservative so it survives scrutiny):
  1. Split the corridor's annual KSI reduction into fatal vs. serious-injury
     shares using that corridor's own observed K:SI crash ratio.
  2. Monetize: fatal = VSL; serious injury = 0.105 x VSL, USDOT's MAIS-3
     ("serious") coefficient from the departmental VSL guidance.
  3. Apply a 50% conservatism haircut to ALL benefits (pattern borrowed from
     the World Bank/ITDP CyclingMAX tool's Induced Benefit Factor) so the
     headline claim is "even if the model is 2x optimistic, the ratio holds."
  4. Discount the annual benefit over HORIZON_YEARS at DISCOUNT_RATE and
     divide by the package's capital cost range to get a BCR range
     (low BCR uses the high cost estimate, and vice versa).

Parameter provenance (verified via web research, July 2026):
  - VSL $13.2M in 2023 DOLLARS: USDOT departmental VSL guidance; the FY2025
    USDOT Benefit-Cost Analysis Guidance directs applicants to present values
    in 2023 dollars, so this is the matching VSL. (Later base years are
    higher: $13.7M for 2024, $14.2M for 2025.)
  - 7% real discount rate is the CURRENT official rate: OMB revoked the
    Nov 2023 Circular A-94 update (3.1%) on 2025-04-08 (memo M-25-23) and
    reinstated the October 1992 A-94; USDOT's May 2025 BCA guidance revision
    adopted the reinstated 7% rate.
  - 0.105 is USDOT's MAIS-3 ("serious injury") fraction of VSL. Our
    serious-injury counts are police-reported (KABCO "A" equivalent); the BCA
    guidance's police-report conversion may value KABCO-A somewhat lower than
    MAIS-3. Sensitivity: even halving this coefficient lowers total benefits
    only ~11-15% for these corridors (the fatal share dominates), well inside
    the 50% haircut. Confirm the exact KABCO-A table value in the current BCA
    Guidance Appendix A before a formal grant submission.

Known limitations (also stated in the app):
  - Costs are capital-only (the countermeasure data carries no maintenance
    stream), which overstates BCR slightly; the 50% benefit haircut more than
    offsets this in practice.
  - Benefits are crash-cost avoidance only — no travel-time, health-activity,
    or emissions benefits are claimed (unlike CyclingMAX), keeping the claim
    surface small and defensible for a safety tool.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORR = ROOT / "data" / "escambia_corridors.geojson"

PARAMS = {
    "vsl_usd": 13_200_000,             # USDOT VSL, 2023 dollars (FY2025 BCA guidance basis)
    "serious_injury_fraction": 0.105,  # USDOT MAIS-3 "serious injury" fraction of VSL
    "discount_rate": 0.07,             # current official rate (1992 OMB A-94, reinstated Apr 2025)
    "horizon_years": 20,
    "benefit_haircut": 0.5,            # conservatism factor applied to all benefits
    "sources": (
        "USDOT Departmental Guidance on Valuation of a Statistical Life "
        "(VSL $13.2M in 2023 dollars; serious injury = MAIS-3 fraction 0.105); "
        "FY2025 USDOT Benefit-Cost Analysis Guidance (values in 2023 dollars); "
        "7% real discount rate per OMB Circular A-94 (1992 edition, reinstated "
        "April 2025, adopted in USDOT's May 2025 BCA guidance revision)."
    ),
}


def annuity_factor(rate, years):
    """Present value of $1/year for `years` at `rate`."""
    return (1 - (1 + rate) ** -years) / rate


def corridor_bca(props, params=PARAMS):
    """Return the additive `bca` dict for one corridor, or None if not computable."""
    ksi_reduced = props.get("countermeasure_annual_ksi_reduced") or 0
    cost_low = props.get("countermeasure_total_low") or 0
    cost_high = props.get("countermeasure_total_high") or 0
    fatal = props.get("fatal_count") or 0
    serious = props.get("serious_injury_count") or 0
    ksi = fatal + serious
    if ksi_reduced <= 0 or cost_low <= 0 or cost_high <= 0 or ksi <= 0:
        return None

    fatal_share = fatal / ksi
    per_ksi_value = (fatal_share * params["vsl_usd"]
                     + (1 - fatal_share) * params["serious_injury_fraction"] * params["vsl_usd"])
    annual_benefit = ksi_reduced * per_ksi_value * params["benefit_haircut"]
    pv_benefits = annual_benefit * annuity_factor(params["discount_rate"],
                                                  params["horizon_years"])
    return {
        "annual_benefit_usd": round(annual_benefit),
        "pv_benefits_usd": round(pv_benefits),
        "bcr_low": round(pv_benefits / cost_high, 1),   # conservative end
        "bcr_high": round(pv_benefits / cost_low, 1),
        "fatal_share": round(fatal_share, 3),
        "params": {
            "vsl_usd": params["vsl_usd"],
            "serious_injury_fraction": params["serious_injury_fraction"],
            "discount_rate": params["discount_rate"],
            "horizon_years": params["horizon_years"],
            "benefit_haircut": params["benefit_haircut"],
        },
        "method": (
            "Planning-level estimate: avoided fatal/serious-injury crash costs only, "
            "valued at USDOT figures (VSL $13.2M in 2023 dollars per the FY2025 BCA "
            f"guidance), discounted over {params['horizon_years']} years at "
            f"{params['discount_rate']:.0%} (OMB Circular A-94), with all benefits "
            f"reduced {params['benefit_haircut']:.0%} for uncertainty. "
            "Capital costs only. Not an engineering BCA."
        ),
        "sources": params["sources"],
    }


def main():
    d = json.loads(CORR.read_text())
    added = skipped = 0
    for feat in d["features"]:
        bca = corridor_bca(feat["properties"])
        if bca is None:
            feat["properties"].pop("bca", None)
            skipped += 1
            continue
        feat["properties"]["bca"] = bca
        added += 1
        p = feat["properties"]
        print(f"[{p['id']}] BCR {bca['bcr_low']:>5.1f}–{bca['bcr_high']:>5.1f} | "
              f"annual benefit ${bca['annual_benefit_usd']:>12,} | "
              f"cost ${p['countermeasure_total_low']:,}–${p['countermeasure_total_high']:,}")
    CORR.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    print(f"BCA attached to {added} corridors ({skipped} skipped — no countermeasures/KSI)")


if __name__ == "__main__":
    main()
