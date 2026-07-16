#!/usr/bin/env python3
"""Offline tests for scan_representative_emails.py.

Exercises the pure extraction/diff/render logic against synthetic HTML — no
network. Run:  python3 scripts/test_scan_representative_emails.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_representative_emails as S

FAILURES = []


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILURES.append(label)


REPS = {
    "roster": {
        "county_commission": {
            "members": [
                {"district": 1, "name": "A One", "email": "district1@myescambia.com"},
                {"district": 2, "name": "B Two", "email": "district2@myescambia.com"},
            ]
        },
        "city_council": {
            "members": [
                {"district": 1, "name": "C Vice", "email": "cvice@cityofpensacola.com"},
                {"district": 2, "name": "Gone Person", "email": "gone@cityofpensacola.com"},
                {"district": 3, "name": "No Email", "email": None},
            ]
        },
    }
}


def test_extract_emails():
    html = """
      <a href="mailto:District1@MyEscambia.com">D1</a>
      contact district2@myescambia.com.
      off-domain: someone@gmail.com
      subdomain ok: clerk@mail.myescambia.com
    """
    got = S.extract_emails(html, "myescambia.com")
    check(got == ["clerk@mail.myescambia.com", "district1@myescambia.com", "district2@myescambia.com"],
          f"extract: on-domain only, lowercased, sorted (got {got})")
    check("someone@gmail.com" not in got, "extract: off-domain excluded")
    check(S.extract_emails("", "myescambia.com") == [], "extract: empty html -> []")
    trailing = S.extract_emails("write to a@cityofpensacola.com.", "cityofpensacola.com")
    check(trailing == ["a@cityofpensacola.com"], f"extract: strips trailing dot (got {trailing})")


def test_roster_emails():
    r = S.roster_emails(REPS, "city_council")
    check(set(r) == {"cvice@cityofpensacola.com", "gone@cityofpensacola.com"},
          "roster_emails: null email skipped")
    check(r["cvice@cityofpensacola.com"] == "C Vice (D1)", "roster_emails: label format")


def test_diff():
    page = ["cvice@cityofpensacola.com", "newperson@cityofpensacola.com"]
    d = S.diff_source(REPS, "city_council", page)
    check(d["still_present"] == ["cvice@cityofpensacola.com"], "diff: still_present")
    check(d["missing_from_page"] == ["gone@cityofpensacola.com"], "diff: missing_from_page (stale)")
    check(d["new_on_page"] == ["newperson@cityofpensacola.com"], "diff: new_on_page")


def test_render_handles_fetch_error():
    results = {"county_commission": {"error": "HTTPError: 403"}}
    out = S.render_report(results, "test")
    check("Could not read source" in out, "render: fetch error surfaced, no crash")
    check("verified" in out.lower(), "render: keeps the not-a-verification disclaimer")


def test_main_offline_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        reps_path = Path(td) / "reps.json"
        reps_path.write_text(json.dumps(REPS))
        county_html = Path(td) / "county.html"
        county_html.write_text("mailto:district1@myescambia.com and district2@myescambia.com and district3@myescambia.com")
        out_path = Path(td) / "report.md"
        rc = S.main(["--reps", str(reps_path),
                     "--html-file", f"county_commission={county_html}",
                     "--out", str(out_path)])
        check(rc == 0, "main: offline run returns 0")
        report = out_path.read_text()
        check("district3@myescambia.com" in report, "main: new-on-page address appears in report")
        # city_council had no --html-file -> should be reported as unreadable, not crash
        check("Could not read source" in report or "no --html-file" in report,
              "main: body without html degrades gracefully")


if __name__ == "__main__":
    test_extract_emails()
    test_roster_emails()
    test_diff()
    test_render_handles_fetch_error()
    test_main_offline_end_to_end()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")
