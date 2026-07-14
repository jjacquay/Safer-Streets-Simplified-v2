#!/usr/bin/env python3
"""Fetch American Community Survey (ACS) equity indicators for Escambia County, FL.

Reads the Census API key from the CENSUS_API_KEY environment variable — it is
NEVER hard-coded or committed. Store it as a GitHub Actions repository secret and
pass it to this script via `env:` in the workflow.

Output: data/acs_escambia_tracts.json — one record per census tract with the
equity variables most relevant to a safe-streets prioritization (population,
median household income, poverty rate, zero-vehicle households, and share of
people of color). A later step can spatially join these tracts to corridors
(needs TIGER/Line tract geometry); this script just pulls the tabular data.

Run locally:  CENSUS_API_KEY=xxxx python3 scripts/fetch_acs_escambia.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "acs_escambia_tracts.json"

YEAR = os.environ.get("ACS_YEAR", "2023")          # ACS 5-year end year
DATASET = f"https://api.census.gov/data/{YEAR}/acs/acs5"
STATE_FIPS = "12"     # Florida
COUNTY_FIPS = "033"   # Escambia

# variable code -> friendly key
VARS = {
    "B01003_001E": "population",
    "B19013_001E": "median_household_income",
    "B17001_002E": "poverty_below_count",
    "B17001_001E": "poverty_universe",
    "B25044_003E": "owner_no_vehicle",
    "B25044_010E": "renter_no_vehicle",
    "B25044_001E": "households_universe",
    "B03002_001E": "race_universe",
    "B03002_003E": "white_nonhispanic",
}

# ACS uses large negative sentinels (e.g. -666666666) for "not available"
def clean(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return None if n <= -666666660 else n


def main():
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        print("CENSUS_API_KEY not set — skipping ACS fetch (add it as a repo secret).",
              file=sys.stderr)
        return 0  # soft-skip so the wider refresh workflow doesn't hard-fail

    params = {
        "get": "NAME," + ",".join(VARS.keys()),
        "for": "tract:*",
        "in": f"state:{STATE_FIPS} county:{COUNTY_FIPS}",
        "key": key,
    }
    url = DATASET + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r)

    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    out = []
    for row in rows[1:]:
        rec = {"name": row[idx["NAME"]],
               "geoid": row[idx["state"]] + row[idx["county"]] + row[idx["tract"]]}
        vals = {friendly: clean(row[idx[code]]) for code, friendly in VARS.items()}
        # derived, defensible rates
        pov_u = vals["poverty_universe"]
        rec["poverty_rate"] = round(vals["poverty_below_count"] / pov_u, 4) if pov_u else None
        hh_u = vals["households_universe"]
        no_veh = (vals["owner_no_vehicle"] or 0) + (vals["renter_no_vehicle"] or 0)
        rec["zero_vehicle_hh_share"] = round(no_veh / hh_u, 4) if hh_u else None
        race_u = vals["race_universe"]
        wnh = vals["white_nonhispanic"]
        rec["people_of_color_share"] = round(1 - (wnh / race_u), 4) if (race_u and wnh is not None) else None
        rec["population"] = vals["population"]
        rec["median_household_income"] = vals["median_household_income"]
        out.append(rec)

    payload = {
        "source": f"US Census Bureau ACS 5-year {YEAR} (via api.census.gov)",
        "county": "Escambia County, FL (FIPS 12033)",
        "tract_count": len(out),
        "tracts": out,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {OUT} — {len(out)} tracts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
