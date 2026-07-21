# Implementation notes

## Design principles

1. **Three user questions, three flows.** Every screen serves one of: *Where is the risk? What should we do? Why this corridor?*
2. **Plain language over jargon.** No "VRU exposure," "arterial operations," or "regression model outputs" in the UI. Reserve technical terms for `docs/`.
3. **One obvious next action per section.** Each section has a single primary button.
4. **Visible status.** Active filters, selected corridor, and packet summary are always shown.

## Information architecture

| Section          | User question        | Primary action            |
|------------------|----------------------|---------------------------|
| Find your street | Where near me?       | Enter an address          |
| Overview         | Why are we here?     | Open the priority map     |
| Priority map     | Where is the risk?   | Select a corridor         |
| Corridor ranking | Which is worst?      | Open corridor details     |
| Best fixes       | What should we do?   | Review the three matched countermeasures |
| Packet builder   | How do I explain it? | Download the packet       |
| Countermeasures  | What does it cost / return? | Read the cost + BCR tiles (full table collapsed) |

Developer-facing content (data files, deploy notes) lives in the footer's
"About the data" disclosure and this docs folder — not in the public nav.
The former "Workflow" section was removed: the layout should imply the
sequence without a manual.

## WCAG 2.2 AA checklist

- [x] Text contrast 4.5:1 / large text 3:1 (audited via design tokens)
- [x] Non-text contrast 3:1 for borders, focus rings, icons
- [x] Visible focus indicators (`:focus-visible` ring, 2px, high-contrast)
- [x] Keyboard reachable everywhere; no traps; logical tab order
- [x] Skip-to-content link
- [x] Target size 44×44 CSS px minimum
- [x] Labels associated with inputs/selects via `for`/`id`
- [x] Semantic landmarks: `header`, `nav`, `main`, `section`, `footer`
- [x] `aria-label` on icon-only controls (mobile menu, theme toggle, download)
- [x] `aria-live` regions for filter and packet status updates
- [x] No horizontal scroll at 320px width (verified in headless Chromium)
- [x] Corridor selection is keyboard-operable via the ranked list of buttons (the map itself is mouse/touch click)

## CI as the network probe

The dev sandbox has no outbound internet beyond package registries (government
and GIS hosts 403 at the proxy), but GitHub-hosted runners do. Anything that
needs the open web — the rep-email scan, district-boundary discovery, the data
refresh — therefore runs as a workflow. The scan and discovery workflows also
trigger on PRs that touch their own scripts, in **report-only probe mode**
(artifact + step summary, no writes, no PR), so a change pushed from the sandbox
gets real-network results minutes later without merging first.

## Backlog (post-MVP)

- **In-app email generator (built).** Lives in the Packet builder section of
  `index.html`. `resolveOfficials()` maps a corridor's `corridor_districts` to
  roster members; the UI renders a recipient checklist (matched districts
  pre-checked), shows an "unverified addresses" warning, and builds a `mailto:`
  or copies the email text. Recipients are framed as "the officials responsible
  for this street." The DOM-free logic (`resolveOfficials`, `buildEmailSubject`,
  `buildEmailBody`, `buildMailto`, …) sits between the `EMAIL-GEN PURE LOGIC`
  markers and is unit-tested by `scripts/test_email_generator.mjs` (Node, no npm
  deps; runs in CI). **Still gated on data, not code:** (1) all roster emails are
  `verified: false` (from an AI Overview) — confirm with the City Clerk; (2) the
  `corridor_districts` block is an empty scaffold until
  `recompute_corridor_districts.py` runs against real boundaries, so today the
  generator can't auto-match districts and asks the user to pick recipients.

- ACS equity join is built (`scripts/fetch_acs_escambia.py` + `scripts/join_acs_to_corridors.py`, surfaced in the corridor detail + packet, gated on data presence). It is populated for all 18 corridors in the committed data (ACS 5-year, via those two scripts). Follow-up: the tract→corridor join uses a pure-Python point-in-polygon over line vertices — good enough for equity context, but not a length-exact GIS overlay. Add non-fatal 2023+ crashes once Florida SB 1614's public-access window allows.
- Hotspot windows (`scripts/add_hotspot_windows.py`) surface the highest-crash 0.5-mile stretch within each corridor — a simplified analog of the Safer Streets Priority Finder's sliding-window method (see README Acknowledgements). Populates on the next `refresh-data.yml` run (needs raw crash points fetched in CI; not present in this dev environment, so it hasn't run against live data yet as of this writing — verify the first CI output before trusting the numbers in a real packet). Deliberately does NOT attempt SSPF's Bayesian predictive risk modeling for un-observed segments — that requires a real statistical model and dedicated validation, not a client-side simplification, and a fake version would risk misleading prioritization decisions.
- Add a `before/after` diagram for each treatment
- Persist user-built packets to localStorage and shareable URLs
- Translate UI to Spanish and Vietnamese (Escambia language access)
- Add a printable one-page PDF export of the packet
- Optional: HubSpot form submission for "request a briefing"

## Known limitations

- Leaflet's default zoom controls are 30×30 px; we override to 44×44.
- **Leaflet is self-hosted under `vendor/leaflet/`** (JS, CSS, and marker images), so a third-party CDN outage can no longer take down the app — previously it loaded from unpkg and a CDN failure left `L` undefined, which aborted the whole script (corridor list, packet builder, and theme toggle, not just the map). The marker-icon path resolves automatically because the images sit next to `leaflet.css`. The map is still initialized near the top of the main script and isn't wrapped in a `typeof L` guard, but the dependency is now first-party and same-origin.
- **Map tiles and the geocoder are centralized in the `CONFIG` object** at the top of the script. They still point at free shared community endpoints (OpenStreetMap / CARTO tiles, Nominatim geocoding) whose usage policies cap production volume; before heavy public traffic, swap `CONFIG` to a keyed provider (Mapbox, MapTiler, Stadia, …). The geocoder has an in-flight guard and a 3-character minimum to avoid hammering Nominatim. Tiles remain remote and degrade to a blank basemap without breaking the app.
- The NACTO "Transit Streets" countermeasure has no local illustration; its remote hot-link was removed, so the card now renders a clean "No image" placeholder instead of firing a doomed external request. Add a licensed local image to `assets/psc/` and set `image_local` on that PSC entry to restore the illustration. (FHWA `highways.dot.gov` URLs remain in the data only as dormant fallbacks behind local images and are never requested at runtime.)
- **Content-Security-Policy** is set in `vercel.json`. It uses `script-src 'self' 'unsafe-inline'` because the app's logic is a single inline `<script>` (the deliberate single-file design); to move to a strict policy, externalize the script or add per-script hashes. The former inline `onerror` image handler was replaced with a JS-attached listener so no inline event handlers remain.
