# SaferStreets Simplified (Escambia MVP)

**Live site:** https://safer-streets-simplified.vercel.app

A plain-language street safety app for Escambia County, Florida. Built for city council members, nonprofits, journalists, teachers, and residents — not just traffic engineers.

The app answers three questions:

1. **Where is the risk?** — A map of priority corridors with simple filters.
2. **What should we do?** — A recommended first step plus the three best-matched FHWA Proven Safety Countermeasures and a costed countermeasure table for each corridor.
3. **Why this corridor?** — A plain-language story and a one-page packet you can download.

## Stack

- Single static `index.html`
- [Leaflet](https://leafletjs.com/) 1.9.4 for the maps (hero overview, priority map, and per-corridor detail map), self-hosted under `vendor/leaflet/` so there is no third-party CDN dependency
- A real `data/escambia_corridors.geojson` with 18 corridors as MultiLineString geometries (OpenStreetMap centerlines), joined to FDOT public crash data (2018–2022, all severities) and NHTSA FARS fatals (2022–2024), plus a `data/escambia_crash_grid.geojson` aggregated heatmap (150 m cells, low cells suppressed)
- No build step; deploys directly to Vercel as static files

## Local preview

```bash
# Any static server works
python3 -m http.server 5173
# then open http://localhost:5173
```

## Deploy

This repo is wired to Vercel with GitHub CI/CD. Every push to `main` triggers a new production deployment at **https://safer-streets-simplified.vercel.app**; pull requests get preview URLs. (Note: the Vercel project's production domain is `safer-streets-simplified`, which differs from the repo name — the dashboard lives at vercel.com/vcllc/saferstreets-simplified-escambia-mvp.)

Vercel settings:

- **Framework preset:** Other (Static HTML)
- **Build command:** *(none)*
- **Output directory:** `.`
- `vercel.json` provides `cleanUrls`, security headers, and GeoJSON content-type for `/data/*`.

## Accessibility

Designed to meet **WCAG 2.2 AA**:

- Body text ≥ 16px, labels ≥ 12px
- 4.5:1 contrast on normal text, 3:1 on large text and UI components
- Visible `:focus-visible` rings on every interactive element
- Minimum 44×44 CSS px hit areas for buttons and selects
- Semantic landmarks (`header`, `nav`, `main`, `section`, `footer`)
- Every form control has an associated `<label>`
- Mobile-first responsive layout with no horizontal scroll on common breakpoints (verified down to 320px)
- The map shows corridors but is selected via mouse/touch click; keyboard users select corridors from the ranked list, which is fully keyboard-operable

## Data

`data/escambia_corridors.geojson` contains 18 corridors (MultiLineString geometries from OSM centerlines). Key properties:

| field                                | meaning                                                              |
|--------------------------------------|----------------------------------------------------------------------|
| `id`                                 | Stable corridor ID (e.g. `C001`)                                     |
| `name` / `place`                     | Human-readable corridor name and neighborhood                        |
| `risk`                               | `very-high` / `high` / `moderate` (relabeled in the UI as Highest / Higher / High — see note below) |
| `score`                              | Composite risk score (currently ranges ~36–879; higher = worse)      |
| `crash_count` / `fatal_count` / `serious_injury_count` / `ksi_count` | Crash counts within a 30 m buffer of the corridor |
| `ksi_per_mile` / `length_mi`         | Killed-or-seriously-injured density and corridor length              |
| `years` / `time_of_day`              | Crash counts by calendar year and time-of-day bucket                 |
| `crash_factors` / `crash_factors_ksi`| Contributing-factor profile (ped, bike, intersection, lane departure, speeding, impaired, distracted, night, motorcycle) |
| `treatment`                          | Recommended first intervention                                       |
| `countermeasures` + `countermeasure_total_low/high` + `countermeasure_annual_ksi_reduced` | Costed FHWA countermeasure package and estimated annual KSI reduction |
| `psc` / `psc_primary_slug`           | Top three matched FHWA Proven Safety Countermeasures                 |
| `equity`                             | ACS 5-year context for the census tracts the corridor runs through (poverty rate, zero-vehicle households, people-of-color share, median income). Added by the data-refresh workflow when a Census API key is configured; the UI shows it only when present. |
| `bca`                                | Planning-level benefit-cost analysis of the countermeasure package: annual monetized benefit, present value, and a BCR range (`bcr_low` uses the high cost estimate). Avoided fatal/serious-injury crash costs only — VSL $13.2M in 2023 dollars per the FY2025 USDOT BCA Guidance, serious injuries at the MAIS-3 coefficient (0.105×VSL), 20 yr @ 7% (OMB A-94, 1992 edition reinstated Apr 2025), all benefits reduced 50% for uncertainty. Built by `scripts/add_benefit_cost.py`; not an engineering BCA. |
| `hotspot`                            | The highest-crash fixed-length window within the corridor (default 0.5 mi) — start/end mileage, crash and KSI counts, and share of the corridor's total crashes. A simplified analog of the [Safer Streets Priority Finder](https://github.com/tooledesign/Safer-Streets-Priority-Finder)'s sliding-window method, applied within an already-selected corridor rather than across a whole network. Built by `scripts/add_hotspot_windows.py`; skipped for corridors under 1 mile or with fewer than 10 matched crashes. |
| `tags` / `sources`                   | Audience tags and source citations                                   |
| `summary` / `message`                | Plain-language description and public-packet message                 |

**Risk tiers are relative within a pre-filtered set.** Every corridor here is already on the regional high-injury network, so tiers compare risk *among* high-risk streets, not against all county roads — that is why the lowest internal tier (`moderate`) is still labeled "High" in the UI.

This data is real, not synthetic. It is built by the scripts in `scripts/` (see `docs/IMPLEMENTATION.md`) from FDOT public crash layers (2018–2022, all severities) and NHTSA FARS fatals (2022–2024); the 2022–2024 fatal extension works around Florida SB 1614's 60-day public-access privacy window, which keeps non-fatal 2023+ data from being public yet. Future work: join ACS demographics and add non-fatal 2023+ crashes as they become available. Cost and crash-reduction figures are planning estimates for prioritization, not engineering quotes.

### Featured case study: Fairfield Drive / Pensacola Boulevard

The homepage highlights a real, verifiable federal grant: **"ECRC Pensacola ITS Safety Demonstration Activities,"** a $10,000,000 Safe Streets and Roads for All (SS4A) grant USDOT awarded in FY24 to the [Emerald Coast Regional Council](https://www.ecrc.org/) (ECRC, formerly the West Florida Regional Planning Council) for ITS safety improvements on two Escambia corridors: Fairfield Drive (`C009` in this app) and Pensacola Boulevard. The featured stats (1,725 crashes / 4 deaths on Fairfield Drive, 2021–2025; 663 rear-end crashes, 38%; 160 of 524 Pensacola Boulevard crashes at one intersection) come from a **draft** (April 2026) baseline memo Kimley-Horn prepared for ECRC, built on **[Signal Four Analytics](https://signal4analytics.com/)** — the crash-report database maintained by FDOT and the University of Florida that also underlies this app's own FDOT crash layers. Those figures cover a narrower study window/segment than this app's own 2018–2022 + FARS corridor counts, so they're presented side by side rather than merged. (Two existing corridor entries, `C008` and `C009`, previously described this same $10M grant as funding "lighting" and "pedestrian crossings" — unverified specifics not stated in the source memo or the USDOT award title. That wording has been corrected to match the confirmed scope: "ITS safety-demonstration improvements.")

### Representatives & district overlaps

`data/representatives.json` holds the official-contact data layer: a roster
(Escambia County Commission, Pensacola City Council, FDOT D3) plus a
`corridor_districts` block mapping each corridor to the districts it runs
through.

> **Status — read before relying on this.** This is the *data layer and its
> tooling only.* There is **not yet an in-app email generator** that consumes it;
> that remains future work (see `docs/IMPLEMENTATION.md` backlog). The roster is
> also **unverified**: every email currently comes from a Google AI Overview
> screenshot (a secondhand summary, not an official page) and is flagged
> `verified: false` with its source and retrieval date. The `corridor_districts`
> block is a schema-complete scaffold (empty arrays) until
> `recompute_corridor_districts.py` is run against real district boundaries.

**Verify before public use** (see the file's `_meta.note`): county-commission
addresses are role-based (`district{N}@myescambia.com`) and stable across
elections; city-council addresses are person-based and change every cycle —
confirm all seven with the City Clerk. No map or API supplies contact info; that
stays a manual check.

`scripts/recompute_corridor_districts.py` populates the `corridor_districts`
block from **current** district boundaries (the block currently ships as an empty
scaffold — running this tool fills it). It reads county and city district polygons from a
live ArcGIS REST query **or** a pre-downloaded GeoJSON file (`--*-file`, for when
the GIS hosts are unreachable), and marks a corridor as overlapping a district
when any vertex falls inside it **or** any segment crosses its boundary — so a
street that passes through a district between vertices is still caught. It fails
loudly (exit 2, no write) on ArcGIS error bodies, zero features, a missing or
out-of-range district field, an incomplete layer, or an orphaned corridor, and
prints a before→after diff. Always dry-run first:

```
python3 scripts/recompute_corridor_districts.py \
  --county-url "https://…/MapServer/<n>/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson" \
  --city-url   "https://…/FeatureServer/<n>/query?where=1%3D1&outFields=*&returnGeometry=true&outSR=4326&f=geojson" \
  --dry-run
# review the diff, then re-run without --dry-run to write
```

Geometry must be WGS84 lon/lat (`outSR=4326`). If a layer needs a token,
download it once and pass `--county-file` / `--city-file`. Tests:
`python3 scripts/test_recompute_corridor_districts.py` (also run in CI).

### Periodic email scan (drift check)

`scripts/scan_representative_emails.py` is a **non-destructive** drift check: it
fetches the official county/city directory pages, extracts any address on the
expected domain, diffs that against the roster, and writes a Markdown report. It
**never edits `representatives.json`** and never flips a `verified` flag — a
match on a page is a *candidate* for manual confirmation, not a confirmation.

The `.github/workflows/scan-rep-emails.yml` workflow runs it on a schedule and
opens a pull request carrying the report for a human to review. Expect partial
results: the official sites may block automated fetches (both returned HTTP 403
in testing), in which case the affected body is reported as "could not read
source" rather than failing. Tests:
`python3 scripts/test_scan_representative_emails.py` (also run in CI). Offline
use: `--html-file county_commission=page.html` parses saved HTML without network.

### Pre-launch checklist (rep-contact + email feature)

Open items before rep-contact data is resident-facing. Ordered by priority — 1
and 2 are the real blockers; nothing below matters if the recipients are wrong.

| # | Item | What's needed | Status |
|---|------|---------------|--------|
| 1 | **Roster accuracy** | Every email in `representatives.json` is currently **unverified** — sourced from a Google AI Overview screenshot, not an official page, and flagged `verified: false`. Confirm all 7 city-council emails with the City Clerk (person-based, change each election) and re-check the full roster each cycle. County addresses are role-based and structurally stable. No map or API supplies contact info; this is manual. | ☐ Emails imported (unverified) |
| 2 | **District boundaries** | Confirm the real county (`gismaps.myescambia.com`) and city (`maps.cityofpensacola.com`) ArcGIS layer numbers + district field, then run `scripts/recompute_corridor_districts.py` (dry-run first). The `corridor_districts` block is an empty scaffold until this runs. | ☐ Tooling merged; needs the real layers |
| 3 | **Build the in-app email generator** | The UI feature that turns a selected corridor + its `corridor_districts` into an addressed email does **not exist yet**. When built, label recipients "the officials responsible for this street" (not "your representatives" — the email targets the corridor's districts, not necessarily the sender's own). | ☐ Not started (data layer + tooling ready) |
| 4 | **Crash date windows** | Wherever crash and fatality figures appear together (homepage; the email, once built): split the ranges — crashes are 2018–2022, fatalities run through 2024. Don't present them as one window. | ☐ Copy drafted |

## Project structure

```
.
├── index.html                       # Main app (all markup, styles, and JS)
├── README.md                        # This file
├── vercel.json                      # Static config + headers
├── data/
│   ├── escambia_corridors.geojson   # 18 corridors + crash/countermeasure/PSC attributes
│   ├── escambia_crash_grid.geojson  # 150 m crash-density grid (heatmap)
│   ├── treatments.json              # 31-treatment catalog
│   ├── psc_content.json             # FHWA PSC narratives
│   └── representatives.json         # Official-contact roster (UNVERIFIED) + corridor→district scaffold
├── assets/
│   ├── treatments/                  # Treatment illustration images
│   └── psc/                         # PSC spotlight images
├── vendor/
│   └── leaflet/                     # Self-hosted Leaflet 1.9.4 (js, css, marker images)
├── scripts/                         # Data pipeline (fetch FDOT/FARS/OSM, join, build GeoJSON)
│                                    #  + recompute_corridor_districts.py, scan_representative_emails.py (+ tests)
└── docs/
    └── IMPLEMENTATION.md            # Implementation notes + backlog
```

## Acknowledgements & prior art

- **[Safer Streets Priority Finder](https://github.com/tooledesign/Safer-Streets-Priority-Finder)** (Toole Design, MIT license) — a more advanced, network-wide safety analysis tool, including Bayesian predictive risk modeling for road segments with no crash history. This repo is a separate, independently built codebase (different stack, no shared code), aimed at a simpler public-facing use case. Its `hotspot` feature (see the data table above) is a small, simplified analog of SSPF's sliding-window method, applied within an already-selected corridor rather than discovering corridors network-wide from raw crash data. **Predictive risk modeling for segments without crash history is out of scope here** — reach for SSPF if that's what you need; faking a simplified version of a statistical safety model would be irresponsible for a tool used to prioritize real safety spending.
- **[CyclingMAX](https://cyclingmax.worldbank.org/)** (World Bank / ITDP / Progress Analytics) — the flat conservatism discount applied to this site's benefit-cost estimates is adapted from CyclingMAX's "Induced Benefit Factor."
- The Fairfield Drive / Pensacola Boulevard featured case study draws on a draft (April 2026) baseline memo **Kimley-Horn** prepared for the **[Emerald Coast Regional Council](https://www.ecrc.org/)** (ECRC, formerly the West Florida Regional Planning Council) under its USDOT SS4A grant, and on ECRC's regional high-injury-network StoryMap. Crash figures there use **[Signal Four Analytics](https://signal4analytics.com/)**, the crash-report database maintained by FDOT and the University of Florida.
- Road centerlines: OpenStreetMap contributors. Crash data: FDOT, NHTSA FARS, Signal Four Analytics. Demographics: US Census Bureau ACS. Countermeasures: FHWA Proven Safety Countermeasures and CMF Clearinghouse.

## License

Internal MVP — license TBD.
