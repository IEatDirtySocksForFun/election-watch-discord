"""Analyse a replay.discord_live run: Discord send failures, rate limiting, app errors, bad data.

    python tools/analyze_live_run.py --record run.jsonl --log run.log [--json out.json]

Bad-data checks run on every results post as the bot rendered it:
  non_candidate    a row like "Write-ins", "Over Votes", "Blank", "Scattering", "Total"
  name_case        ALL-CAPS names or a broken capitalisation such as "Jd Vance", "Mcmorris"
  white_party      a party label shown with the white dot that is not a no-party label
  pct_sum          the shown percentages add up to more than 100.5
  votes_down       a candidate's votes dropped between two posts of the same race
  call_mismatch    the race call names someone other than the final leader
  no_posts         a race that never got a results post (from --expected)
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict

LINE = re.compile(
    r"^(?P<dot>\S+) (?P<name>.+?)(?: \((?P<party>[^)]+)\))? (?P<votes>[\d,]+)(?: · (?P<pct>[\d.]+)%)?(?: ✓)?$"
)
NON_CANDIDATE = re.compile(
    r"\b(over ?votes?|under ?votes?|blank|scattering|total"
    r"|uncommitted|registered voters|ballots cast|times? (?:over|under)voted)\b",
    re.I,
)
BAD_CASE = re.compile(r"\b(Mc[a-z]|O'[a-z]|Ii\b|Iii\b|Iv\b)")
NO_PARTY = {
    "NPA",
    "NA",
    "NON",
    "NP",
    "IND",
    "I",
    "U",
    "UNA",
    "UNENROLLED",
    "NONPARTISAN",
    "NON PARTISAN",
    "NO PARTY PRE",
    "STATES NO PA",
    "OTHER",
    "OTH",
    "WRI",
    "UNAFFILIATED",
    "NOM",
    "NONC",
    "NONI",
    "NONW",
    "ONA",
    "A",
    "NONE",
    "NPD",
    "PC",
    "WI",
    "UNENROLLED",
}


def candidates(lines: list[str]):
    for line in lines:
        m = LINE.match(line)
        if m and m.group("dot") in {"🔴", "🔵", "⚪"} | {line[:1]} and "expected vote" not in line:
            yield m


def analyse(records: list[dict], log_lines: list[str], expected: list[str]) -> dict:
    out: dict = {"sends": len(records)}
    ok = [r for r in records if r.get("ok")]
    out["failed_sends"] = Counter(r["error"].split(":")[0] for r in records if not r.get("ok"))
    lat = [r["secs"] for r in ok if "secs" in r]
    if lat:
        out["send_latency_s"] = {
            "median": statistics.median(lat),
            "p95": sorted(lat)[int(len(lat) * 0.95) - 1],
            "max": max(lat),
        }
    out["rate_limited"] = sum("rate limit" in line.lower() for line in log_lines)
    out["app_errors"] = Counter(
        re.sub(r"\d+", "N", line.split(": ", 1)[-1])[:120]
        for line in log_lines
        if " ERROR " in line or "Traceback" in line
    )
    issues: dict[str, list] = defaultdict(list)
    last_votes: dict[tuple[str, str], int] = {}
    final_leader: dict[str, str] = {}
    calls: dict[str, str] = {}
    posted = Counter()
    for r in ok:
        race = r["race"]
        if r["kind"] == "call":
            m = re.search(r"Projected winner: (.+?)(?: \(|$)", r["lines"][0] if r["lines"] else "")
            if m:
                calls[race] = m.group(1).strip()
            continue
        if r["kind"] != "results":
            continue
        posted[race] += 1
        shown = list(candidates(r["lines"]))
        pct = sum(float(m.group("pct") or 0) for m in shown)
        if pct > 100.5:
            issues["pct_sum"].append((race, r["sim"], round(pct, 1)))
        for i, m in enumerate(shown):
            name, party = m.group("name"), (m.group("party") or "").strip()
            votes = int(m.group("votes").replace(",", ""))
            if i == 0:
                final_leader[race] = name
            if NON_CANDIDATE.search(name) and not re.search(r"write[- ]?ins?", name, re.I):
                issues["non_candidate"].append((race, name))
            if (name.isupper() and len(name) > 4) or BAD_CASE.search(name):
                issues["name_case"].append((race, name))
            if m.group("dot") == "⚪" and party and party.upper() not in NO_PARTY:
                issues["white_party"].append((race, party))
            prev = last_votes.get((race, name))
            if prev is not None and votes < prev:
                issues["votes_down"].append((race, name, r["sim"], prev, votes))
            last_votes[(race, name)] = votes
    for race, winner in calls.items():
        if race in final_leader and final_leader[race] != winner:
            issues["call_mismatch"].append((race, winner, final_leader[race]))
    for race in expected:
        if not posted[race]:
            issues["no_posts"].append(race)
    out["posts_per_race"] = {
        "races": len(posted),
        "min": min(posted.values(), default=0),
        "max": max(posted.values(), default=0),
    }
    # de-duplicate repeated findings, keep counts
    out["issues"] = {
        k: {"count": len(v), "examples": sorted(set(map(tuple, (x if isinstance(x, tuple) else (x,) for x in v))))[:15]}
        for k, v in issues.items()
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--expected", default="", help="comma-separated races that should have posted")
    ap.add_argument("--json")
    a = ap.parse_args()
    records = [json.loads(line) for line in open(a.record, encoding="utf-8") if line.strip()]
    log_lines = open(a.log, encoding="utf-8", errors="replace").read().splitlines()
    res = analyse(records, log_lines, [r for r in a.expected.split(",") if r])
    text = json.dumps(
        res, indent=1, default=lambda o: dict(o) if isinstance(o, Counter) else str(o), ensure_ascii=False
    )
    if a.json:
        open(a.json, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
