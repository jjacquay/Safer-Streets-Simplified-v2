# Street Smart (Escambia MVP)

_Formerly **SaferStreets Simplified**. Renamed to avoid confusion with its own
V1 and with Toole Design's separate [Safer Streets Priority
Finder](https://github.com/tooledesign/Safer-Streets-Priority-Finder) (see
Acknowledgements — different codebase, different purpose). Note: "Street Smart"
is also the name of unrelated regional pedestrian-safety *education campaigns*
(e.g., MWCOG's bestreetsmart.net); the "Escambia" qualifier matters when citing
this project._

**Live site:** https://street-smarts.vercel.app

A plain-language street safety app for Escambia County, Florida. Built for city council members, nonprofits, journalists, teachers, and residents — not just traffic engineers.

The app answers three questions:

1. **Where is the risk?** — A map of priority corridors with simple filters.
2. **What should we do?** — A recommended first step plus the three best-matched FHWA Proven Safety Countermeasures and a costed countermeasure table for each corridor.
3. **Why this corridor?** — A plain-language story and a one-page packet you can download.

## Stack

- Single static `index.html`
- Self-hosted type under `vendor/fonts/` (SIL OFL 1.1, licenses included): [Public Sans](https://public-sans.digital.gov/) (the USWDS open typeface) for UI, [Libre Caslon Text](https://github.com/impallari/Libre-Caslon-Text) for display — no font CDN, consistent with the CSP's `font-src 'self'`
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

The repo is **Vercel-ready** — `vercel.json` carries the full static config
(`cleanUrls`, security headers + CSP, and the GeoJSON content-type for
`/data/*`), so there is no build step and no repo changes are needed to deploy.

**This app deploys as its own Vercel project, separate from V1** (the
`safer-streets-simplified` domain belongs to SaferStreets Simplified **V1**;
this project is `street-smarts` → **https://street-smarts.vercel.app**). Settings:

- **Framework preset:** Other · **Build command:** (none) · **Root directory:** `.`

Vercel's Git Integration then deploys every push to `main` to production and
gives each PR a preview URL.

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
| `equity`                             | ACS 5-year context for the census tracts the corridor runs through (poverty rate, zero-vehicle households, people-of-color share, median income). Present for all 18 corridors in the committed data (built by `scripts/fetch_acs_escambia.py` + `scripts/join_acs_to_corridors.py`); surfaced in the corridor detail and packet. |
| `bca`                                | Planning-level benefit-cost analysis of the countermeasure package: annual monetized benefit, present value, and a BCR range (`bcr_low` uses the high cost estimate). Avoided fatal/serious-injury crash costs only — VSL $13.2M in 2023 dollars per the FY2025 USDOT BCA Guidance, serious injuries at the MAIS-3 coefficient (0.105×VSL), 20 yr @ 7% (OMB A-94, 1992 edition reinstated Apr 2025), all benefits reduced 50% for uncertainty. Built by `scripts/add_benefit_cost.py`; not an engineering BCA. |
| `hotspot`                            | The highest-crash fixed-length window within the corridor (default 0.5 mi) — start/end mileage, crash and KSI counts, and share of the corridor's total crashes. A simplified analog of the [Safer Streets Priority Finder](https://github.com/tooledesign/Safer-Streets-Priority-Finder)'s sliding-window method, applied within an already-selected corridor rather than across a whole network. Built by `scripts/add_hotspot_windows.py`; skipped for corridors under 1 mile or with fewer than 10 matched crashes. |
| `tags` / `sources`                   | Audience tags and source citations                                   |
| `summary` / `message`                | Plain-language description and public-packet message                 |

**Risk tiers are relative within a pre-filtered set.** Every corridor here is already on the regional high-injury network, so tiers compare risk *among* high-risk streets, not against all county roads — that is why the lowest internal tier (`moderate`) is still labeled "High" in the UI.

This data is real, not synthetic. It is built by the scripts in `scripts/` (see `docs/IMPLEMENTATION.md`) from FDOT public crash layers (2018–2022, all severities) and NHTSA FARS fatals (2022–2024); the 2022–2024 fatal extension works around Florida SB 1614's 60-day public-access privacy window, which keeps non-fatal 2023+ data from being public yet. ACS demographics are already joined (see the `equity` field above); remaining future work: add non-fatal 2023+ crashes as they become available. Cost and crash-reduction figures are planning estimates for prioritization, not engineering quotes.

### Featured case study: Fairfield Drive / Pensacola Boulevard

The homepage highlights a real, verifiable federal grant: **"ECRC Pensacola ITS Safety Demonstration Activities,"** a $10,000,000 Safe Streets and Roads for All (SS4A) grant USDOT awarded in FY24 to the [Emerald Coast Regional Council](https://www.ecrc.org/) (ECRC, formerly the West Florida Regional Planning Council) for ITS safety improvements on two Escambia corridors: Fairfield Drive (`C009` in this app) and Pensacola Boulevard. The featured stats (1,725 crashes / 4 deaths on Fairfield Drive, 2021–2025; 663 rear-end crashes, 38%; 160 of 524 Pensacola Boulevard crashes at one intersection) come from a **draft** (April 2026) baseline memo Kimley-Horn prepared for ECRC, built on **[Signal Four Analytics](https://signal4analytics.com/)** — the crash-report database maintained by FDOT and the University of Florida that also underlies this app's own FDOT crash layers. Those figures cover a narrower study window/segment than this app's own 2018–2022 + FARS corridor counts, so they're presented side by side rather than merged. (Two existing corridor entries, `C008` and `C009`, previously described this same $10M grant as funding "lighting" and "pedestrian crossings" — unverified specifics not stated in the source memo or the USDOT award title. That wording has been corrected to match the confirmed scope: "ITS safety-demonstration improvements.")

### Representatives & district overlaps

`data/representatives.json` holds the official-contact data layer: a roster
(Escambia County Commission, Pensacola City Council, FDOT D3) plus a
`corridor_districts` block mapping each corridor to the districts it runs
through. The **in-app email generator** (in the Packet builder section) consumes
it: select a corridor, review the recipients, and open a pre-filled email — or
copy the text — addressed to the officials responsible for that street.

> **Status — read before relying on this.** The generator works; the data is
> **partially verified**. The **county roster is verified** against the official
> Elected Officials page (myescambia.com/open-government/elected-officials,
> 2026-07-17): names, districts, role-based emails, phones, and term expirations
> all match. The **city roster's names/districts/roles are confirmed** against
> the official staff directory (cityofpensacola.com/directory.aspx?did=7), but
> that page shows emails only as links — so the seven **city email addresses
> remain unverified** (`verified: false`) and the UI keeps its "unverified
> addresses" warning until they're confirmed. The `corridor_districts` block is
> still a schema-complete scaffold (empty arrays) until
> `recompute_corridor_districts.py` runs against real district boundaries, so
> the generator can't yet auto-match a street to districts and asks the user to
> pick recipients.

**Verify before public use** (see the file's `_meta.note`): county-commission
addresses are role-based (`district{N}@myescambia.com`), officially confirmed,
and stable across elections; city-council addresses are person-based and change
every cycle — confirm all seven via the directory's email links or the City
Clerk, and re-check the roster each election.

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

Nobody has confirmed endpoints for those two boundary layers yet, so
`scripts/fetch_district_boundaries.py` **discovers** them: it searches the
public ArcGIS Online index and walks candidate government ArcGIS servers,
validates every candidate hard (exactly 5 county / 7 city polygon features, one
in-range district per feature via the recompute tool's own field detection),
prefers official-domain hosts, and **aborts rather than guesses** on ambiguity.
It must run in CI — the dev sandbox has no outbound internet to GIS hosts.
`.github/workflows/refresh-districts.yml` runs it two ways: opening a PR that
touches the discovery script runs a **report-only probe** (artifact + step
summary, writes nothing); a manual `workflow_dispatch` does the full run —
download boundaries to `data/district_boundaries/`, recompute
`corridor_districts`, and open a data PR for review. Dispatch inputs
`county_url` / `city_url` bypass discovery when the real endpoints are known.
Tests: `python3 scripts/test_fetch_district_boundaries.py` (also run in CI).

### Periodic email scan (drift check)

`scripts/scan_representative_emails.py` is a **non-destructive** drift check: it
fetches the official county/city directory pages, extracts any address on the
expected domain, diffs that against the roster, and writes a Markdown report. It
**never edits `representatives.json`** and never flips a `verified` flag — a
match on a page is a *candidate* for manual confirmation, not a confirmation.

The `.github/workflows/scan-rep-emails.yml` workflow runs it on a schedule and
opens a pull request carrying the report for a human to review; a PR that
touches the scanner runs it in **probe mode** (report as artifact + step
summary, no PR opened) — useful because CI runners can reach the gov sites the
dev sandbox cannot. Expect partial results: the official sites may block
automated fetches (both 403'd the sandbox in testing), in which case the
affected body is reported as "could not read source" rather than failing. Tests:
`python3 scripts/test_scan_representative_emails.py` (also run in CI). Offline
use: `--html-file county_commission=page.html` parses saved HTML without network.

### Pre-launch checklist (rep-contact + email feature)

Open items before rep-contact data is resident-facing. Ordered by priority — 1
and 2 are the real blockers; nothing below matters if the recipients are wrong.

| # | Item | What's needed | Status |
|---|------|---------------|--------|
| 1 | **Roster accuracy** | **County: verified twice** — against the official Elected Officials page (screenshot 2026-07-17) *and* by the CI email scan, which fetched that page and found all five `district{N}@myescambia.com` addresses live with zero drift. **City: names/districts/roles verified** against the official staff directory, but the CI scan confirmed the directory HTML exposes **0 email addresses** (link-only), so the 7 city addresses stay `verified: false` until confirmed manually (open each "Email Council …" link or ask the City Clerk). Re-check every election. | ◐ County verified; city emails pending |
| 2 | **District boundaries** | **Both layers found and validated by the CI probe (2026-07-17):** county `gismaps.myescambia.com/arcgis/rest/services/Escambia_County/MapServer/29` (5 districts, field `DISTRICT`); city `maps.cityofpensacola.com/arcgis/rest/services/Avineon_MIL1/MapServer/34` (9 features covering districts 1–7 — split polygons; 4 identical copies on the city server, deduped). The dry-run recompute assigned districts to **all 18 corridors with no orphans**. Next: merge, dispatch `refresh-districts.yml` from `main`, review the data PR it opens (spot-check a few corridors against the official district maps). | ◐ Layers validated in CI; dispatch after merge |
| 3 | **In-app email generator** | Built (Packet builder section): resolves a corridor's `corridor_districts` to officials, renders a recipient checklist (pre-checking matched districts), and opens a pre-filled `mailto:` or copies the text. Recipients are framed as "the officials responsible for this street." Logic is unit-tested (`scripts/test_email_generator.mjs`, in CI). Blocked on items 1–2 for accuracy: it shows an unverified-address warning and, with the district scaffold empty, asks the user to pick recipients. | ☑ Built; gated on roster + district data |
| 4 | **Crash date windows** | Crashes (2018–2022) and fatalities (2022–2024) must never be shown as one range. Done in the generated email body; the homepage still needs a pass. | ◐ Email done; homepage pending |

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
│                                    #  + recompute_corridor_districts.py, scan_representative_emails.py,
│                                    #    fetch_district_boundaries.py (+ tests)
└── docs/
    └── IMPLEMENTATION.md            # Implementation notes + backlog
```

## Acknowledgements & prior art

- **[Safer Streets Priority Finder](https://github.com/tooledesign/Safer-Streets-Priority-Finder)** (Toole Design, MIT license) — a more advanced, network-wide safety analysis tool, including Bayesian predictive risk modeling for road segments with no crash history. This repo is a separate, independently built codebase (different stack, no shared code), aimed at a simpler public-facing use case. Its `hotspot` feature (see the data table above) is a small, simplified analog of SSPF's sliding-window method, applied within an already-selected corridor rather than discovering corridors network-wide from raw crash data. **Predictive risk modeling for segments without crash history is out of scope here** — reach for SSPF if that's what you need; faking a simplified version of a statistical safety model would be irresponsible for a tool used to prioritize real safety spending.
- **[CyclingMAX](https://cyclingmax.worldbank.org/)** (World Bank / ITDP / Progress Analytics) — the flat conservatism discount applied to this site's benefit-cost estimates is adapted from CyclingMAX's "Induced Benefit Factor."
- The Fairfield Drive / Pensacola Boulevard featured case study draws on a draft (April 2026) baseline memo **Kimley-Horn** prepared for the **[Emerald Coast Regional Council](https://www.ecrc.org/)** (ECRC, formerly the West Florida Regional Planning Council) under its USDOT SS4A grant, and on ECRC's regional high-injury-network StoryMap. Crash figures there use **[Signal Four Analytics](https://signal4analytics.com/)**, the crash-report database maintained by FDOT and the University of Florida.
- Road centerlines: OpenStreetMap contributors. Crash data: FDOT, NHTSA FARS, Signal Four Analytics. Demographics: US Census Bureau ACS. Countermeasures: FHWA Proven Safety Countermeasures and CMF Clearinghouse.
- Type: [Public Sans](https://public-sans.digital.gov/) (Public Sans Project Authors / USWDS) and [Libre Caslon Text](https://github.com/impallari/Libre-Caslon-Text) (Libre Caslon Text Project Authors), both under the SIL Open Font License 1.1 — license texts in `vendor/fonts/`.

## License

Internal MVP — license TBD.
