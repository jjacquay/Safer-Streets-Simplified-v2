// Unit tests for the email-generator pure logic embedded in index.html.
//
// The app is deliberately a single inline <script>, so this test extracts the
// DOM-free block between the EMAIL-GEN PURE LOGIC markers and evaluates it in a
// bare function scope — no browser, no duplication of the logic. If the markers
// or the function names change, update this file to match.
//
// Run:  node scripts/test_email_generator.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(join(ROOT, 'index.html'), 'utf8');

const m = html.match(/EMAIL-GEN PURE LOGIC START ====([\s\S]*?)\/\/ ==== EMAIL-GEN PURE LOGIC END/);
if (!m) {
  console.error('FAIL: could not find the EMAIL-GEN PURE LOGIC block in index.html');
  process.exit(1);
}
const factory = new Function(
  m[1] + '\nreturn { resolveOfficials, officialLabel, anyUnverified, buildEmailSubject, buildEmailBody, buildMailto };'
);
const F = factory();

const failures = [];
function check(cond, label) {
  console.log((cond ? 'PASS ' : 'FAIL ') + label);
  if (!cond) failures.push(label);
}

const REPS = {
  roster: {
    county_commission: { members: [
      { district: 1, name: 'Steve Stroberger', email: 'district1@myescambia.com', verified: false },
      { district: 3, name: 'Lumon May', email: 'district3@myescambia.com', verified: false },
    ] },
    city_council: { members: [
      { district: 1, name: 'Jennifer Brahier', role: 'Vice President', email: 'jbrahier@cityofpensacola.com', verified: false },
      { district: 2, name: 'Charles Bare', email: 'cbare@cityofpensacola.com', verified: false },
    ] },
    fdot_district_3: { members: [
      { name: 'FDOT D3 PIO', email: null, verified: false },
    ] },
  },
  corridor_districts: {
    C001: { county: ['3'], city: [1, 2] },
    C002: { county: [], city: [] },
  },
};

const CORRIDOR = { id: 'C001', name: 'Test Street', place: 'Testville', crash_count: 1725, fatal_count: 4, priority: 88, treatment: 'Road diet' };

// resolveOfficials
{
  const off = F.resolveOfficials(REPS, 'C001');
  check(off.matched === true, 'resolve: C001 matches districts');
  check(off.county.length === 1 && off.county[0].district === 3, 'resolve: county district 3 matched (string vs number tolerant)');
  check(off.city.length === 2, 'resolve: both city districts matched');
  check(off.fdot.length === 0, 'resolve: fdot member with null email excluded');

  const empty = F.resolveOfficials(REPS, 'C002');
  check(empty.matched === false && empty.county.length === 0 && empty.city.length === 0,
        'resolve: empty scaffold -> matched false, no officials');

  const missing = F.resolveOfficials(REPS, 'C999');
  check(missing.matched === false, 'resolve: unknown corridor id -> matched false');

  check(F.resolveOfficials(null, 'C001').matched === false, 'resolve: null reps -> no crash, matched false');
}

// officialLabel
{
  check(F.officialLabel({ name: 'Jane Doe', role: 'President', district: 6 }) === 'Jane Doe (President) — District 6',
        'label: name + role + district');
  check(F.officialLabel({ name: 'FDOT PIO' }) === 'FDOT PIO', 'label: no role/district');
}

// anyUnverified
{
  check(F.anyUnverified([{ email: 'a@b.com', verified: false }]) === true, 'unverified: flags verified:false');
  check(F.anyUnverified([{ email: 'a@b.com', verified: true }]) === false, 'unverified: all verified -> false');
  check(F.anyUnverified([{ email: null, verified: false }]) === false, 'unverified: null-email member ignored');
}

// buildEmailSubject
{
  check(F.buildEmailSubject(CORRIDOR) === 'Street safety on Test Street', 'subject: includes corridor name');
}

// buildEmailBody — the crash-window separation is the key correctness property
{
  const body = F.buildEmailBody(CORRIDOR, {});
  check(body.includes('2018–2022'), 'body: crash window 2018–2022 present');
  check(body.includes('2022–2024'), 'body: fatal window 2022–2024 present');
  check(!/2018[–-]2024/.test(body), 'body: windows NOT merged into one 2018–2024 range');
  check(body.includes('1,725'), 'body: crash count formatted with thousands separator');
  check(body.includes('4 traffic deaths'), 'body: fatal count pluralized');
  check(body.includes('Road diet'), 'body: treatment included');
  check(body.includes('not a traffic engineering study'), 'body: disclaimer present');

  const singleFatal = F.buildEmailBody({ ...CORRIDOR, fatal_count: 1 }, {});
  check(singleFatal.includes('1 traffic death') && !singleFatal.includes('1 traffic deaths'),
        'body: single fatality is singular');

  const noFatal = F.buildEmailBody({ ...CORRIDOR, fatal_count: 0 }, {});
  check(!noFatal.includes('traffic death'), 'body: no death claimed when zero fatalities (FARS still cited as a source)');
}

// buildMailto
{
  const url = F.buildMailto(['a@x.com', 'b@y.com'], 'Sub ject', 'line1\nline2');
  check(url.startsWith('mailto:a@x.com,b@y.com?'), 'mailto: recipients joined with comma, @ not encoded');
  check(url.includes('subject=Sub%20ject'), 'mailto: subject encoded');
  check(url.includes('body=line1%0Aline2'), 'mailto: body newline encoded');
  check(F.buildMailto([null, undefined, 'c@z.com'], 's', 'b').startsWith('mailto:c@z.com?'),
        'mailto: falsy recipients filtered out');
}

console.log('');
if (failures.length) {
  console.log(`${failures.length} FAILED: ${JSON.stringify(failures)}`);
  process.exit(1);
}
console.log('ALL TESTS PASSED');
