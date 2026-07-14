#!/usr/bin/env python3
"""Copy each corridor's `resilience_message` from the master list onto the GeoJSON.

The resilience frame is a parallel, audience-shifted line (workforce/freight/
stewardship) kept ALONGSIDE the plain-language `message`. It now lives in
scripts/corridors_master.json as the single source of truth — earlier versions
hard-coded it here with unsourced economic claims (lost work-days, insurance
pools, tort exposure); that wording was removed in favor of defensible,
sourced framing carried by the master file.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORR = ROOT / "data" / "escambia_corridors.geojson"
MASTER = ROOT / "scripts" / "corridors_master.json"


def main():
    master = json.loads(MASTER.read_text())
    resilience = {c["id"]: c.get("resilience_message")
                  for c in master["corridors"] if c.get("resilience_message")}

    d = json.loads(CORR.read_text())
    missing = []
    for feat in d["features"]:
        cid = feat["properties"]["id"]
        if cid in resilience:
            feat["properties"]["resilience_message"] = resilience[cid]
        else:
            missing.append(cid)
    if missing:
        print(f"WARNING: no resilience_message in master for: {missing}")
    CORR.write_text(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"Applied resilience_message to {len(d['features']) - len(missing)} corridors")


if __name__ == "__main__":
    main()
