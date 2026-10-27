#!/usr/bin/env python3
"""Write docs/parties.md from electionwatch/data/parties.yaml.

    python tools/parties_table.py           # rewrite docs/parties.md
    python tools/parties_table.py --check   # exit 1 if docs/parties.md is stale

The table is generated, never hand-edited: change the YAML and re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from electionwatch.parties import BUCKETS, load_parties  # noqa: E402

DOCS = ROOT / "docs" / "parties.md"

BUCKET_NOTES = {
    "dem": "Democratic Party — 🔵",
    "rep": "Republican Party — 🔴",
    "libertarian": "Libertarian Party and its state affiliates — 🟡",
    "green": "Green Party and the state parties with their own ballot line — 🟢",
    "socialist": "socialist and communist parties — a distinct emoji each, in order of party size",
    "far_right": "parties that reliable sources call neo-Nazi, white nationalist or fascist — "
    "a distinct emoji each",
    "constitution": "Constitution Party, its state affiliates and ballot lines — 📜",
    "independent": "independent and no-party labels — ⚪",
    "centrist": "centrist and big-tent reform parties",
    "other": "everything else: progressive, conservative, single-issue and regional parties",
}


def render() -> str:
    """Return the full markdown document for the current party table."""
    parties = load_parties()
    # stable sort: buckets in BUCKETS order, file order within a bucket
    rows = sorted(parties.values(), key=lambda p: BUCKETS.index(p.bucket))

    out = [
        "# Party codes and emoji markers",
        "",
        "`electionwatch/data/parties.yaml` is the single source of truth for party codes, their",
        "bucket and the marker the bot puts in front of a candidate. Adapters should store the",
        "canonical `code`; anything else (a state affiliate, an abbreviation with periods, an",
        "old ballot line) is resolved by `electionwatch/parties.py` through the alias list.",
        "",
        "```python",
        "from electionwatch.parties import party_code, party_emoji",
        "",
        "party_code(\"DFL\")                            # 'DEM'",
        "party_emoji(\"Constitution Party of Oregon\")  # '📜'",
        "party_emoji(\"Nobody We Know\")                # '⚪'",
        "```",
        "",
        "Matching is case- and punctuation-insensitive, and never touches the network. Unknown",
        "parties pass through uppercased with the ⚪ marker.",
        "",
        "This page is generated — edit the YAML, then run `python tools/parties_table.py`.",
        "",
        f"{len(parties)} parties, {sum(len(p.aliases) for p in parties.values())} aliases.",
        "",
        "## Buckets",
        "",
        "| Bucket | Meaning |",
        "| --- | --- |",
    ]
    counts = {b: sum(1 for p in parties.values() if p.bucket == b) for b in BUCKETS}
    for bucket in BUCKETS:
        out.append(f"| `{bucket}` | {BUCKET_NOTES[bucket]} ({counts[bucket]}) |")
    out += [
        "",
        "## Table",
        "",
        "| Code | Party | Bucket | Emoji | Aliases |",
        "| --- | --- | --- | --- | --- |",
    ]
    for party in rows:
        aliases = ", ".join(party.aliases).replace("|", "\\|")
        out.append(
            f"| `{party.code}` | {party.name} | {party.bucket} | {party.emoji} | {aliases} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    text = render()
    current = DOCS.read_text(encoding="utf-8") if DOCS.exists() else None
    if "--check" in args:
        if current != text:
            print(f"{DOCS} is stale: run tools/parties_table.py", file=sys.stderr)
            return 1
        print(f"{DOCS} is up to date")
        return 0
    DOCS.write_text(text, encoding="utf-8")
    print(f"wrote {DOCS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
