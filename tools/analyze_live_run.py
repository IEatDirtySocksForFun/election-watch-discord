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
        match = LINE.match(line)
        if match and match.group("dot") in {"🔴", "🔵", "⚪"} | {line[:1]} and "expected vote" not in line:
            yield match


def analyse(records: list[dict], log_lines: list[str], expected: list[str]) -> dict:
    report: dict = {"sends": len(records)}
    successful = [record for record in records if record.get("ok")]
    report["failed_sends"] = Counter(record["error"].split(":")[0] for record in records if not record.get("ok"))
    latencies = [record["secs"] for record in successful if "secs" in record]
    if latencies:
        report["send_latency_s"] = {
            "median": statistics.median(latencies),
            "p95": sorted(latencies)[int(len(latencies) * 0.95) - 1],
            "max": max(latencies),
        }
    report["rate_limited"] = sum("rate limit" in line.lower() for line in log_lines)
    report["app_errors"] = Counter(
        re.sub(r"\d+", "N", line.split(": ", 1)[-1])[:120]
        for line in log_lines
        if " ERROR " in line or "Traceback" in line
    )
    issues: dict[str, list] = defaultdict(list)
    last_votes: dict[tuple[str, str], int] = {}
    final_leader: dict[str, str] = {}
    calls: dict[str, str] = {}
    posted = Counter()
    for record in successful:
        race = record["race"]
        if record["kind"] == "call":
            match = re.search(r"Projected winner: (.+?)(?: \(|$)", record["lines"][0] if record["lines"] else "")
            if match:
                calls[race] = match.group(1).strip()
            continue
        if record["kind"] != "results":
            continue
        posted[race] += 1
        shown = list(candidates(record["lines"]))
        pct = sum(float(match.group("pct") or 0) for match in shown)
        if pct > 100.5:
            issues["pct_sum"].append((race, record["sim"], round(pct, 1)))
        for index, match in enumerate(shown):
            name, party = match.group("name"), (match.group("party") or "").strip()
            votes = int(match.group("votes").replace(",", ""))
            if index == 0:
                final_leader[race] = name
            if NON_CANDIDATE.search(name) and not re.search(r"write[- ]?ins?", name, re.I):
                issues["non_candidate"].append((race, name))
            if (name.isupper() and len(name) > 4) or BAD_CASE.search(name):
                issues["name_case"].append((race, name))
            if match.group("dot") == "⚪" and party and party.upper() not in NO_PARTY:
                issues["white_party"].append((race, party))
            previous = last_votes.get((race, name))
            if previous is not None and votes < previous:
                issues["votes_down"].append((race, name, record["sim"], previous, votes))
            last_votes[(race, name)] = votes
    for race, winner in calls.items():
        if race in final_leader and final_leader[race] != winner:
            issues["call_mismatch"].append((race, winner, final_leader[race]))
    for race in expected:
        if not posted[race]:
            issues["no_posts"].append(race)
    report["posts_per_race"] = {
        "races": len(posted),
        "min": min(posted.values(), default=0),
        "max": max(posted.values(), default=0),
    }
    # de-duplicate repeated findings, keep counts
    report["issues"] = {
        issue_kind: {
            "count": len(findings),
            "examples": sorted(
                set(map(tuple, (finding if isinstance(finding, tuple) else (finding,) for finding in findings)))
            )[:15],
        }
        for issue_kind, findings in issues.items()
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--expected", default="", help="comma-separated races that should have posted")
    ap.add_argument("--json")
    args = ap.parse_args()
    records = [json.loads(line) for line in open(args.record, encoding="utf-8") if line.strip()]
    log_lines = open(args.log, encoding="utf-8", errors="replace").read().splitlines()
    report = analyse(records, log_lines, [race for race in args.expected.split(",") if race])
    text = json.dumps(
        report,
        indent=1,
        default=lambda value: dict(value) if isinstance(value, Counter) else str(value),
        ensure_ascii=False,
    )
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
