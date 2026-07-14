"""
Clean wide_browse output into a slug-indexed PSC content file the UI consumes.

Strips inline markdown link junk like "([FHWA](url))" that the extractor
sometimes leaves behind.
"""
import json
import re
from pathlib import Path

WIDE = Path("/home/user/workspace/wide/browse_results_mqy5yavs.json")
ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "data" / "fhwa_psc_index.json"
OUT = ROOT / "data" / "psc_content.json"


def clean(text):
    if not text:
        return ""
    s = str(text)
    # Strip "([text](url))" and "[text](url)" markdown link wrappers
    s = re.sub(r"\(\[[^\]]+\]\([^)]+\)\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def url_to_slug(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


def main():
    raw = json.loads(WIDE.read_text())
    rows = raw.get("results") if isinstance(raw, dict) else raw

    # wide_browse stores column values under the verbose schema 'title'
    # strings rather than snake_case property names. Look them up by
    # fuzzy match.
    def pick(row, *needles):
        for k in row:
            kl = k.lower()
            if all(n.lower() in kl for n in needles):
                return row[k]
        return ""

    print(f"Found {len(rows)} rows in wide_browse output")

    index = {p["slug"]: p for p in json.loads(INDEX.read_text())}
    content = {}
    for row in rows:
        url = row.get("entity") or ""
        url = clean(url)
        m = re.search(r"https?://[^\s)]+", url)
        if m:
            url = m.group(0)
        slug = url_to_slug(url)
        if slug not in index:
            print(f"  skip unknown slug: {slug} ({url})")
            continue
        content[slug] = {
            "slug": slug,
            "title": clean(pick(row, "title")) or index[slug]["title"],
            "tagline": clean(pick(row, "one-sentence")),
            "narrative": clean(pick(row, "plain-english", "description")),
            "crash_reduction": clean(pick(row, "crash-reduction")),
            "image_url": clean(pick(row, "absolute url", "photo")),
            "image_alt": clean(pick(row, "alt text", "caption")),
            "url": index[slug]["url"],
        }

    print(f"\nWrote content for {len(content)} PSCs")
    for slug, c in sorted(content.items()):
        print(f"  {slug}")
        print(f"     tagline: {c['tagline'][:90]}")
        print(f"     image:   {c['image_url']}")
        print(f"     reduce:  {c['crash_reduction']}")

    OUT.write_text(json.dumps(content, indent=2))


if __name__ == "__main__":
    main()
