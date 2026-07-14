#!/usr/bin/env python3
"""Map corridors to top-3 treatments from the unified catalog (FHWA + NACTO + FDOT + CNU).

Algorithm:
  1. Build factor shares per corridor (normalize crash_factors over total).
  2. Score each treatment for each corridor:
       fit_score = sum(corridor.share[f] * 1{f in treatment.factors}) * 100
       boost by source preference: FHWA gets a small reliability boost over framework pages
  3. Assign top-3 per corridor with uniqueness preference at position #1:
       - Greedy pass over corridors ranked by total KSI (most consequential first).
       - At each corridor, prefer the highest-scoring treatment that no other
         corridor has already taken at position #1 (only enforced when alt
         treatment is within 0.85 * best score).
  4. Positions #2 and #3: best remaining scores, no uniqueness constraint.

Writes psc field as a 3-element array per corridor:
  [{slug, title, source, source_full, url, image_local, image_url, ...,
    why_factor, why_text, fit_score, position}]
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORR = ROOT / 'data' / 'escambia_corridors.geojson'
TREATMENTS = ROOT / 'data' / 'treatments.json'

# Source priority: applied as small multiplier when scores tie
SOURCE_BOOST = {
    'FHWA': 1.10,   # most actionable, highest reliability
    'NACTO': 1.05,  # excellent visuals + design language
    'FDOT': 1.03,   # state-specific framing for FL audiences
    'CNU': 0.95,    # framework-level; useful but less concrete
}

# Treatments that are broad framework pages — only useful as #3 picks,
# never as primary recommendation.
FRAMEWORK_TREATMENTS = {
    'street-networks-101',
    'sustainable-street-network-principles',
    'fdot-context-based-solutions',
    'florida-dot-hits-milestone-context-based-design',
    'road-safety-audit',  # diagnostic process, not a physical countermeasure
    'design-speed',       # framework concept, not a single intervention
}

# Hard caps: a single treatment may appear at most these many times.
# Position #1 cap is stricter to ensure variety in primary recommendations.
MAX_USES_AT_POSITION_ONE = 3
MAX_USES_PER_TREATMENT = 6

# Factor name aliases between corridor properties and treatment tags
FACTOR_ALIASES = {
    'intersection': ['intersection'],
    'ped': ['ped', 'pedestrian'],
    'bike': ['bike', 'bicycle'],
    'lane_departure': ['lane_departure'],
    'speeding': ['speeding'],
    'impaired': ['impaired'],
    'night': ['night'],
}

# Crash factors we score on (must match treatment factor tags)
SCORED_FACTORS = ['ped', 'bike', 'intersection', 'lane_departure', 'speeding', 'impaired', 'night']

# Why-factor phrasing helper for the 3rd person
WHY_PHRASING = {
    'ped': 'pedestrian-involved crashes',
    'bike': 'bicycle-involved crashes',
    'intersection': 'intersection crashes',
    'lane_departure': 'lane-departure crashes',
    'speeding': 'speeding-related crashes',
    'impaired': 'impaired-driving crashes',
    'night': 'nighttime crashes',
}


def fmt_int(n):
    return f'{int(n):,}'


def normalize_shares(crash_factors):
    """Convert raw crash factor counts into shares of total (capped to scored factors)."""
    total = sum(max(0, crash_factors.get(f, 0)) for f in SCORED_FACTORS)
    if total == 0:
        return {f: 0.0 for f in SCORED_FACTORS}
    return {f: max(0, crash_factors.get(f, 0)) / total for f in SCORED_FACTORS}


def compute_distinctiveness(all_corridor_factors):
    """Compute a per-factor average share across corridors.

    A corridor's 'distinctive' factors are the ones where its share exceeds
    the average. This downweights universal patterns (intersection crashes
    are everywhere) and elevates corridors with unusual factor mixes.
    """
    sums = {f: 0.0 for f in SCORED_FACTORS}
    n = 0
    for cf in all_corridor_factors:
        s = normalize_shares(cf)
        for f in SCORED_FACTORS:
            sums[f] += s[f]
        n += 1
    if n == 0:
        return {f: 0.0 for f in SCORED_FACTORS}
    return {f: sums[f] / n for f in SCORED_FACTORS}


def score_treatment(corridor_shares, treatment, avg_shares=None):
    """Score a single treatment for a single corridor.

    Uses 'distinctive share': corridor_share[f] - 0.5 * avg_share[f].
    This rewards treatments that match the factors that make THIS corridor
    different from the typical Escambia corridor, not just the universal
    intersection-crash baseline.

    Penalize treatments that tag many factors (generic catch-alls).
    """
    factors = set(treatment.get('factors') or [])
    if not factors:
        return 0.0
    avg = avg_shares or {f: 0.0 for f in SCORED_FACTORS}
    raw = 0.0
    for f in factors:
        if f not in SCORED_FACTORS:
            continue
        # Distinctive share: how much this corridor over-indexes on factor f
        # vs the average. Floor at 0 so dead factors don't hurt.
        distinctive = corridor_shares.get(f, 0.0) - 0.5 * avg.get(f, 0.0)
        raw += max(0.0, distinctive)
    # Specificity bonus: prefer focused treatments
    n = len(factors & set(SCORED_FACTORS))
    if n == 0:
        return 0.0
    specificity = 1.0 / (1 + 0.30 * max(0, n - 1))
    boost = SOURCE_BOOST.get(treatment.get('source', ''), 1.0)
    return raw * boost * specificity * 100


def dominant_factor_for_treatment(corridor_shares, treatment):
    """Which crash factor on this corridor most justifies this treatment?"""
    factors = treatment.get('factors') or []
    best_f, best_s = None, -1
    for f in factors:
        s = corridor_shares.get(f, 0.0)
        if s > best_s:
            best_s, best_f = s, f
    return best_f


def why_text(corridor, treatment_factor):
    """Human sentence: 'pedestrian-involved crashes (13 total, 5 KSI) make this corridor…'"""
    cf = corridor.get('crash_factors') or {}
    cfk = corridor.get('crash_factors_ksi') or {}
    phrasing = WHY_PHRASING.get(treatment_factor, 'the crash mix on this corridor')
    if treatment_factor in cf:
        total = cf.get(treatment_factor, 0)
        ksi = cfk.get(treatment_factor, 0)
        if treatment_factor in ('speeding',):
            return f'{phrasing} ({fmt_int(total)} total) make this corridor a strong fit.'
        return f'{phrasing} ({fmt_int(total)} total, {fmt_int(ksi)} KSI) make this corridor a strong fit.'
    return f'{phrasing} make this corridor a strong fit.'


def assign_top_three(corridors_props, treatments):
    """Returns dict {corridor_id: [3 treatment_slugs]}.

    Pass 1 (uniqueness pass):
      Order corridors by total KSI desc. For each corridor, pick a unique #1
      from the top-scoring treatments, falling through if the best treatment
      is already taken (only if alternative is within 0.85 * best).

    Pass 2: fill #2 and #3 by remaining best scores per corridor.
    """
    # Build per-corridor ranked lists, using distinctiveness to avoid the
    # universal-intersection bias.
    all_factor_dicts = [p.get('crash_factors') or {} for p in corridors_props.values()]
    avg = compute_distinctiveness(all_factor_dicts)
    ranked = {}  # cid -> list of (slug, score, dominant_factor)
    for cid, props in corridors_props.items():
        shares = normalize_shares(props.get('crash_factors') or {})
        rows = []
        for slug, t in treatments.items():
            s = score_treatment(shares, t, avg_shares=avg)
            rows.append((slug, s, dominant_factor_for_treatment(shares, t)))
        rows.sort(key=lambda r: -r[1])
        ranked[cid] = rows

    # Pass 1: assign #1 picks with a soft cap (MAX_USES_AT_POSITION_ONE).
    # Order corridors by KSI so highest-impact corridors get first pick.
    ksi_by_cid = {cid: (props.get('ksi_count') or 0) for cid, props in corridors_props.items()}
    cid_order = sorted(corridors_props.keys(), key=lambda c: -ksi_by_cid[c])
    used_at_one_count = {}
    picks = {cid: [] for cid in corridors_props}

    for cid in cid_order:
        candidates = ranked[cid]
        if not candidates:
            continue
        viable = [(s, sc, f) for (s, sc, f) in candidates if s not in FRAMEWORK_TREATMENTS]
        if not viable:
            viable = candidates
        best_score = viable[0][1] or 0.001
        chosen = None
        # Relaxed band: accept up to 40% below the best score to find variety
        threshold = best_score * 0.60
        for (slug, sc, f) in viable:
            if sc < threshold:
                break
            if used_at_one_count.get(slug, 0) < MAX_USES_AT_POSITION_ONE:
                chosen = (slug, sc, f)
                break
        if not chosen:
            # Fallback: take best regardless of cap
            chosen = viable[0]
        used_at_one_count[chosen[0]] = used_at_one_count.get(chosen[0], 0) + 1
        picks[cid].append(chosen)

    # Pass 2: fill #2 and #3 with best remaining, enforcing the global cap.
    usage_count = dict(used_at_one_count)
    for cid in cid_order:
        taken = {p[0] for p in picks[cid]}
        for (slug, sc, f) in ranked[cid]:
            if len(picks[cid]) >= 3:
                break
            if slug in taken:
                continue
            if usage_count.get(slug, 0) >= MAX_USES_PER_TREATMENT:
                continue
            picks[cid].append((slug, sc, f))
            taken.add(slug)
            usage_count[slug] = usage_count.get(slug, 0) + 1
        # If we still don't have 3, fall back ignoring the cap
        if len(picks[cid]) < 3:
            for (slug, sc, f) in ranked[cid]:
                if len(picks[cid]) >= 3:
                    break
                if slug in taken:
                    continue
                picks[cid].append((slug, sc, f))
                taken.add(slug)
                usage_count[slug] = usage_count.get(slug, 0) + 1

    return picks


def build_psc_entries(corridor_props, picks, treatments):
    """Convert picks into the array of psc objects written into corridor.properties.psc."""
    out = []
    for position, (slug, score, factor) in enumerate(picks, start=1):
        t = treatments[slug]
        entry = {
            'position': position,
            'slug': slug,
            'title': t.get('title'),
            'tagline': t.get('tagline'),
            'narrative': t.get('narrative'),
            'crash_reduction': t.get('crash_reduction'),
            'source': t.get('source'),
            'source_full': t.get('source_full'),
            'url': t.get('url'),
            'image_local': t.get('image_local'),
            'image_url': t.get('image_url'),
            'image_alt': t.get('image_alt'),
            'image_is_icon': t.get('image_is_icon', False),
            'fit_score': round(float(score), 2),
            'why_factor': factor,
            'why_text': why_text(corridor_props, factor),
        }
        out.append(entry)
    return out


def main():
    corr = json.loads(CORR.read_text())
    treatments = json.loads(TREATMENTS.read_text())

    corridors_props = {}
    for f in corr['features']:
        p = f['properties']
        corridors_props[p['id']] = p

    picks = assign_top_three(corridors_props, treatments)

    # Mutate corridor features: replace psc field
    counts_at_one = {}
    for f in corr['features']:
        p = f['properties']
        cid = p['id']
        entries = build_psc_entries(p, picks[cid], treatments)
        p['psc'] = entries
        # Keep legacy 'psc' fields for back-compat readers, plus add new
        if entries:
            p['psc_primary_slug'] = entries[0]['slug']
            counts_at_one[entries[0]['slug']] = counts_at_one.get(entries[0]['slug'], 0) + 1

    CORR.write_text(json.dumps(corr, indent=2, ensure_ascii=False) + '\n')

    # Stats summary
    print(f'Mapped {len(corr["features"])} corridors → 3 treatments each.')
    print(f'Unique #1 picks: {len(counts_at_one)} / {len(corr["features"])}')
    print()
    for f in corr['features']:
        p = f['properties']
        psc = p['psc']
        slugs = ' | '.join(f'{e["source"]}:{e["slug"][:30]}' for e in psc)
        print(f'  {p["id"]:5}  {p["name"][:30]:30}  {slugs}')


if __name__ == '__main__':
    main()
