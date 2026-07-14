#!/usr/bin/env python3
"""
Re-fetch Escambia crashes from FDOT SSO with the FULL field set so we can do
crash-type breakdowns for countermeasure recommendations.

Adds beyond v1: FRST_HARM_LOC_CD, JCT_CD (junction), INTCT_TYP_CD,
MOST_HARM_EVNT_CD, IMPCT_TYP_CD (manner of collision), LGHT_COND_CD (lighting),
RD_SRFC_COND_CD, SPEED_LIMIT, AVERAGE_DAILY_TRAFFIC, FUNCLASS, CNTOFLANES,
plus the boolean flags: PEDESTRIAN_RELATED_IND, BICYCLIST_RELATED_IND,
INTERSECTION_IND, LANE_DEPARTURE_IND, SPEEDING_IND, IMPAIRED_DRIVER_IND,
DISTRACTED_DRIVER_IND, MOTORCYCLE_INVOLVED_IND, WRONGWAY_IND, WORKZONE_IND,
NUMBER_OF_KILLED, NUMBER_OF_PEDESTRIANS, NUMBER_OF_BICYCLISTS.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://gis.fdot.gov/arcgis/rest/services/sso/ssogis/FeatureServer"
YEARS = [2018, 2019, 2020, 2021, 2022]
OUT = "data/private/escambia_crashes_full.geojson"

OUT_FIELDS = [
    # core
    "XID", "CALENDAR_YEAR", "CRASH_DATE", "CRASH_TIME",
    "COUNTY_TXT", "ON_ROADWAY_NAME", "INT_ROADWAY_NAME",
    "SAFETYLAT", "SAFETYLON", "STATE_ROAD_NUMBER", "US_ROAD_NUMBER",
    # severity
    "INJSEVER", "NUMBER_OF_INJURED", "NUMBER_OF_SERIOUS_INJURIES",
    "NUMBER_OF_KILLED", "NUMBER_OF_PEDESTRIANS", "NUMBER_OF_BICYCLISTS",
    "TOTAL_PERSONS",
    # crash type / collision
    "FRST_HARM_LOC_CD", "MOST_HARM_EVNT_CD", "IMPCT_TYP_CD",
    "JCT_CD", "INTCT_TYP_CD",
    # environment
    "LGHT_COND_CD", "EVNT_WTHR_COND_CD", "RD_SRFC_COND_CD",
    # road geometry
    "SPEED_LIMIT", "AVERAGE_DAILY_TRAFFIC", "FUNCLASS", "CNTOFLANES",
    "RCI_MEDIAN_WIDTH_FT", "RCI_SURFACE_WIDTH_FT",
    # contributing factor booleans
    "PEDESTRIAN_RELATED_IND", "BICYCLIST_RELATED_IND",
    "INTERSECTION_IND", "LANE_DEPARTURE_IND",
    "SPEEDING_IND", "AGGRESSIVE_DRIVING_IND",
    "IMPAIRED_DRIVER_IND", "DISTRACTED_DRIVER_IND",
    "MOTORCYCLE_INVOLVED_IND", "WRONGWAY_IND", "WORKZONE_IND",
    "COMMERCIAL_VEHICLE_IND",
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
        with urllib.request.urlopen(url, timeout=120) as resp:
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
        "name": "escambia_crashes_full_2018_2022",
        "description": (
            "Escambia County crash points from FDOT's publicly-released SSO layers, "
            "with full attribute set for countermeasure-type analysis. "
            "Includes crash type, ped/bike/intersection/lane-departure flags, "
            "lighting, speed limit, AADT, and severity. 2023+ data is not "
            "available in this layer (FL 60-day privacy window / SB 1614 March 2023)."
        ),
        "features": all_features,
    }
    with open(OUT, "w") as f:
        json.dump(gj, f)
    print(f"Wrote {OUT}: {len(all_features)} features")


if __name__ == "__main__":
    main()
