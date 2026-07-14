#!/usr/bin/env python3
"""
Fetch Escambia County crash points from FDOT's public SSO crash layers.
This dataset is the publicly-released KSI (killed + serious injury) subset
per the §409 federal exemption. It's the right dataset for corridor
prioritization — these are the crashes that matter most for safety planning.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://gis.fdot.gov/arcgis/rest/services/sso/ssogis/FeatureServer"
YEARS = [2018, 2019, 2020, 2021, 2022]
OUT = "data/private/escambia_crashes_raw.geojson"

OUT_FIELDS = [
    "XID", "CALENDAR_YEAR", "CRASH_DATE", "CRASH_TIME",
    "COUNTY_TXT", "ON_ROADWAY_NAME", "INT_ROADWAY_NAME",
    "SAFETYLAT", "SAFETYLON",
    "INJSEVER", "NUMBER_OF_INJURED", "NUMBER_OF_SERIOUS_INJURIES",
    "STATE_ROAD_NUMBER", "US_ROAD_NUMBER",
]

INJ_SEVERITY = {
    "1": "no_injury",
    "2": "possible_injury",
    "3": "non_incapacitating_injury",
    "4": "incapacitating_injury",  # serious
    "5": "fatal",
}


def fetch_year(year):
    where = f"COUNTY_TXT='Escambia' AND CALENDAR_YEAR={year}"
    params = {
        "where": where,
        "outFields": ",".join(OUT_FIELDS),
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": "2000",
        "resultOffset": "0",
    }
    out = []
    offset = 0
    while True:
        params["resultOffset"] = str(offset)
        url = f"{BASE}/{year}/query?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        feats = data.get("features", [])
        out.extend(feats)
        if len(feats) < 2000:
            break
        offset += 2000
        time.sleep(0.2)
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    all_features = []
    for y in YEARS:
        feats = fetch_year(y)
        for f in feats:
            sev = (f.get("properties") or {}).get("INJSEVER")
            f["properties"]["severity_label"] = INJ_SEVERITY.get(str(sev), "unknown")
        print(f"{y}: {len(feats)} crashes", flush=True)
        all_features.extend(feats)

    gj = {
        "type": "FeatureCollection",
        "name": "escambia_crashes_ksi_2018_2022",
        "description": (
            "Escambia County crash points from FDOT's publicly-released SSO layers. "
            "This is the killed+serious-injury (KSI) subset; the full ALL-crash dataset "
            "is restricted under 23 USC §409. Fields include lat/lng, severity, "
            "and on-roadway name."
        ),
        "features": all_features,
    }
    with open(OUT, "w") as f:
        json.dump(gj, f)
    print(f"Wrote {len(all_features)} features to {OUT}", flush=True)


if __name__ == "__main__":
    main()
