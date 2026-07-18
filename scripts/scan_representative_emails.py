#!/usr/bin/env python3
"""Scan official directory pages for representative emails and diff them against
data/representatives.json.

NON-DESTRUCTIVE BY DESIGN. This script never edits representatives.json and never
flips a `verified` flag. It fetches the official county/city directory pages,
extracts any address on the expected domain, diffs that against the roster, and
writes a human-readable Markdown report. Everything it finds is a CANDIDATE for
manual verification — a match on the page is not a confirmation, and the roster's
`verified: false` flags stay false until a human checks the source and edits the
file by hand.

Why a scan-then-PR flow instead of auto-update:
  * The primary sites block automated fetches (both returned HTTP 403 during
    development), so a run may legitimately fetch nothing — it degrades to
    "could not reach source" rather than failing.
  * Government directory pages restructure, obfuscate emails ("name [at] domain"),
    or hide them behind contact forms. Regex extraction is best-effort.
  * Contact accuracy on a tool that emails real officials must have a human in
    the loop. The scan surfaces drift; a person confirms and commits.

Usage:
  python3 scripts/scan_representative_emails.py                 # fetch live, print report
  python3 scripts/scan_representative_emails.py --out report.md # also write the report
  python3 scripts/scan_representative_emails.py --html-file county=page.html city=page.html
                                                               # offline: parse saved HTML
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPS = ROOT / "data" / "representatives.json"

# Official directory pages, keyed by the roster body they back (confirmed real
# pages, 2026-07-17 — the county one shows emails in plain text; the city one
# renders them as "Email Council <name>" links, so extraction may find nothing
# there if the address only lives behind a form). `domain` is the only address
# space we trust from each source.
SOURCES = {
    "county_commission": {
        "label": "Escambia County Commission",
        "domain": "myescambia.com",
        "url": "https://myescambia.com/open-government/elected-officials",
    },
    "city_council": {
        "label": "Pensacola City Council",
        "domain": "cityofpensacola.com",
        "url": "https://www.cityofpensacola.com/directory.aspx?did=7",
    },
}

# Deliberately permissive; we filter to the source's own domain afterward.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def extract_emails(html, domain):
    """All lowercased addresses in `html` whose host ends with `domain`, sorted."""
    found = set()
    for m in EMAIL_RE.finditer(html or ""):
        addr = m.group(0).lower().rstrip(".")
        host = addr.split("@", 1)[1]
        if host == domain or host.endswith("." + domain):
            found.add(addr)
    return sorted(found)


def roster_emails(reps, body_key):
    """{email: label} for one roster body, skipping null/placeholder addresses."""
    body = reps.get("roster", {}).get(body_key, {})
    out = {}
    for m in body.get("members", []):
        email = (m.get("email") or "").strip().lower()
        if not email:
            continue
        who = m.get("name", "?")
        dist = m.get("district")
        out[email] = f"{who}" + (f" (D{dist})" if dist is not None else "")
    return out


def diff_source(reps, body_key, page_emails):
    """Categorize roster vs page addresses for one body."""
    roster = roster_emails(reps, body_key)
    roster_set, page_set = set(roster), set(page_emails)
    return {
        "still_present": sorted(roster_set & page_set),   # on page AND in roster (reassuring, still unverified)
        "missing_from_page": sorted(roster_set - page_set),  # in roster, not found on page (possibly stale)
        "new_on_page": sorted(page_set - roster_set),     # on page, not in roster (candidate to add)
        "roster_labels": roster,
    }


def fetch(url, timeout=30):
    req = urllib.request.Request(
        url, headers={"User-Agent": "street-smart-rep-email-scan/1.0 (+github actions; non-destructive)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, "replace")


def render_report(results, generated_note):
    lines = [
        "# Representative email scan report",
        "",
        f"_{generated_note}_",
        "",
        "**This is a drift report, not a verification.** Addresses found on a page are",
        "candidates for manual confirmation. Nothing here changes `data/representatives.json`",
        "or any `verified` flag — a human must confirm against the official source and edit",
        "the roster by hand.",
        "",
    ]
    for body_key, r in results.items():
        src = SOURCES[body_key]
        lines.append(f"## {src['label']}  (`{body_key}`)")
        lines.append("")
        lines.append(f"- Source: {src['url']}")
        if r.get("error"):
            lines.append(f"- **Could not read source:** {r['error']}")
            lines.append("- No diff computed for this body this run.")
            lines.append("")
            continue
        lines.append(f"- Addresses found on page: {len(r['new_on_page']) + len(r['still_present'])}")
        lines.append("")
        if r["new_on_page"]:
            lines.append("### ⚠ New on page, not in roster (review — add if legitimate)")
            for e in r["new_on_page"]:
                lines.append(f"- `{e}`")
            lines.append("")
        if r["missing_from_page"]:
            lines.append("### ⚠ In roster, not found on page (possibly stale — confirm officeholder)")
            for e in r["missing_from_page"]:
                lines.append(f"- `{e}` — {r['roster_labels'].get(e, '?')}")
            lines.append("")
        if r["still_present"]:
            lines.append("### ✓ Still present on page (reassuring; still requires manual verification)")
            for e in r["still_present"]:
                lines.append(f"- `{e}` — {r['roster_labels'].get(e, '?')}")
            lines.append("")
        if not (r["new_on_page"] or r["missing_from_page"]):
            lines.append("_No drift detected against the roster._")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_html_files(pairs):
    """--html-file county=path city=path -> {body_key: html}"""
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--html-file expects body=path, got {p!r}")
        key, path = p.split("=", 1)
        if key not in SOURCES:
            raise SystemExit(f"unknown body {key!r}; expected one of {list(SOURCES)}")
        out[key] = Path(path).read_text(encoding="utf-8", errors="replace")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", default=str(REPS))
    ap.add_argument("--out", default=None, help="write the Markdown report to this path")
    ap.add_argument("--html-file", nargs="*", default=None,
                    help="offline mode: body=path pairs of pre-downloaded HTML (skips network)")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    reps = json.loads(Path(args.reps).read_text(encoding="utf-8"))
    offline = _parse_html_files(args.html_file) if args.html_file else None

    results = {}
    for body_key, src in SOURCES.items():
        if offline is not None:
            html = offline.get(body_key)
            note = f"file:{body_key}"
            if html is None:
                results[body_key] = {"error": "no --html-file provided for this body"}
                continue
        else:
            try:
                html = fetch(src["url"], timeout=args.timeout)
                note = src["url"]
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                # 403 from these gov sites is expected in some environments; degrade, don't fail.
                results[body_key] = {"error": f"{type(e).__name__}: {e}"}
                print(f"WARN: could not fetch {src['url']}: {e}", file=sys.stderr)
                continue
        page_emails = extract_emails(html, src["domain"])
        results[body_key] = diff_source(reps, body_key, page_emails)
        results[body_key]["_note"] = note

    generated_note = "Generated by scripts/scan_representative_emails.py"
    report = render_report(results, generated_note)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
