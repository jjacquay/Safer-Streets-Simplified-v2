#!/usr/bin/env python3
"""
Extract Escambia County FL fatal crashes from FARS 2022-2024 bulk CSVs and
write a GeoJSON FeatureCollection compatible with our pipeline.

Why FARS: FDOT SSO crash layers stop in 2022 for Escambia due to FL SB 1614
(March 2023 60-day privacy window). FARS is fatals-only, but they are released
annually as bulk CSVs and include 2023 and 2024 already — fresher than anything
else available without a Signal4 login.
"""
import csv
import json
import os

FARS_DIR = "data/private/fars"
YEARS = [2022, 2023, 2024]
OUT = "data/private/escambia_fars_fatals.geojson"


def parse_int(s, default=0):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def parse_float(s):
    try:
        f = float(s)
        # FARS uses 77.7777 / 88.8888 / 99.9999 sentinels for unknown
        if f >= 77.0 or f == 0.0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def extract(year):
    path = f"{FARS_DIR}/fars_{year}/FARS{year}NationalCSV/accident.csv"
    out = []
    with open(path, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("STATENAME") != "Florida":
                continue
            if row.get("COUNTY") != "33":
                continue
            lat = parse_float(row.get("LATITUDE"))
            lon = parse_float(row.get("LONGITUD"))
            if lat is None or lon is None:
                continue
            props = {
                "source": "FARS",
                "CALENDAR_YEAR": parse_int(row.get("YEAR"), year),
                "MONTH": parse_int(row.get("MONTH")),
                "DAY": parse_int(row.get("DAY")),
                "HOUR": parse_int(row.get("HOUR")),
                "ON_ROADWAY_NAME": row.get("TWAY_ID", "").strip() or None,
                "ROUTENAME": row.get("ROUTENAME"),
                "FUNCLASS": row.get("FUNC_SYSNAME"),
                "MOST_HARM_EVNT": row.get("HARM_EVNAME"),
                "MAN_COLL": row.get("MAN_COLLNAME"),
                "LIGHTING": row.get("LGT_CONDNAME"),
                "INT_TYPE": row.get("TYP_INTNAME"),
                "REL_ROAD": row.get("REL_ROADNAME"),
                "RUR_URB": row.get("RUR_URBNAME"),
                "NUMBER_OF_KILLED": parse_int(row.get("FATALS")),
                "NUMBER_OF_PEDESTRIANS": parse_int(row.get("PEDS")),
                "NUMBER_OF_VEHICLES": parse_int(row.get("VE_TOTAL")),
                "TOTAL_PERSONS": parse_int(row.get("PERSONS")),
                # normalized to our existing pipeline
                "INJSEVER": "5",
                "severity_label": "fatal",
                "SAFETYLAT": lat,
                "SAFETYLON": lon,
                "COUNTY_TXT": "ESCAMBIA",
                "ST_CASE": row.get("ST_CASE"),
                # flags derived from FARS categorical fields
                "PEDESTRIAN_RELATED_IND": "Y" if parse_int(row.get("PEDS")) > 0 else "N",
                "INTERSECTION_IND": "Y" if "Intersection" in (row.get("TYP_INTNAME") or "") and "Not" not in (row.get("TYP_INTNAME") or "") else "N",
            }
            out.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": props,
            })
    return out


def main():
    all_feats = []
    for y in YEARS:
        feats = extract(y)
        print(f"{y}: {len(feats)} Escambia fatals with coords")
        all_feats.extend(feats)
    gj = {
        "type": "FeatureCollection",
        "name": "escambia_fars_fatals_2022_2024",
        "description": (
            "Escambia County FL fatal crashes from NHTSA FARS bulk CSVs, "
            "2022-2024. Used to fill the post-2022 gap left by FL SB 1614 "
            "(60-day public-access privacy window). Fatals only — "
            "non-fatal/serious-injury 2023+ crashes are not in any public source."
        ),
        "features": all_feats,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(gj, f)
    print(f"Wrote {OUT}: {len(all_feats)} features")


if __name__ == "__main__":
    main()
