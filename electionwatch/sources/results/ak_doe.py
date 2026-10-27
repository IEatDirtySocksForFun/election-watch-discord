"""Alaska — Division of Elections precinct-level results CSV.

Alaska publishes one statewide CSV per election on its results site:

    https://www.elections.alaska.gov/results/24GENR/ENRbyPrecinct.csv

The file is one row per precinct x contest x candidate, with the contest name in
``Contest_title`` and vote channels broken out. ``total_votes`` is that candidate's
votes in that precinct; the state's own summary totals are the sum of every row for
the contest (the file also carries at-large / absentee buckets as extra rows, so no
precinct-level de-duplication is needed — summing all rows reproduces the published
statewide totals: 2024 U.S. Representative summed to 328,805 votes).

District naming: state House districts are numbered (``House District 7``), state
Senate districts are lettered (``Senate District B``), and the U.S. House seat is
at-large (``U.S. Representative``). Ballot measures and judicial retention appear as
``BM#1 - 23AMLS`` / ``Supreme Court - Borghesan`` and become LOCAL races.

Note: Alaska runs ranked-choice general elections. These are the first-choice
totals the Division publishes on election night; the final RCV round is in the
Election Summary Report PDF, not in this CSV.
"""
from __future__ import annotations

import csv
import io
import re
from collections import OrderedDict
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://www.elections.alaska.gov"

#: date -> results folder (the Division's own <YY><TYPE> id, e.g. 24GENR).
#: 2026 general is not published yet; the folder follows the same pattern and the
#: adapter says so instead of guessing a wrong URL (see docs/results/states-2.md).
ELECTIONS: dict[str, str] = {
    "2024-11-05": "24GENR",
    "2026-08-18": "26PRIM",
    "2026-11-03": "26GENR",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "TREASURER": "TREAS",
    "AUDITOR": "AUD",
}


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def race_key(title: str) -> RaceKey:
    """Map an Alaska contest title to a RaceKey."""
    n = _upper(title)
    special = c.is_special(n)
    if n.startswith("U.S. PRESIDENT") or n.startswith("US PRESIDENT"):
        return c.local_key("AK", "us-president")
    if re.match(r"^U\.?S\.? SENATOR", n):
        return RaceKey("AK", Level.SENATE, "SEN", None, special)
    if n in ("U.S. REPRESENTATIVE", "US REPRESENTATIVE", "UNITED STATES REPRESENTATIVE"):
        return RaceKey("AK", Level.HOUSE, "HOUSE", "AL", special)
    m = re.match(r"^U\.?S\.? REPRESENTATIVE\s+(?:DISTRICT|DIST\.?)\s*0?(\d+)$", n)
    if m:
        return RaceKey("AK", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^(?:STATE\s+)?SENATE\s+DISTRICT\s+([A-T])$", n)
    if m:
        return RaceKey("AK", Level.STATE, "SD", m.group(1), special)
    m = re.match(r"^(?:STATE\s+)?HOUSE\s+DISTRICT\s+0?(\d{1,2})$", n)
    if m:
        return RaceKey("AK", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    for name, code in _STATEWIDE.items():
        if n == name:
            return RaceKey("AK", Level.GOVERNOR, code, None, special)
    # "GOVERNOR / LT. GOVERNOR" is a single ticket race.
    if n.startswith("GOVERNOR"):
        return RaceKey("AK", Level.GOVERNOR, "GOV", None, special)
    return c.local_key("AK", n)


def parse_ak(text: str, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Pure parse of ENRbyPrecinct.csv (summing precinct rows into statewide totals)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    contests: "OrderedDict[str, dict]" = OrderedDict()
    for row in reader:
        title = (row.get("Contest_title") or "").strip()
        name = (row.get("candidate_name") or "").strip()
        if not title or not name:
            continue
        votes = c.to_int(row.get("total_votes"))
        bucket = contests.setdefault(title, {"cands": OrderedDict()})
        cid = (row.get("candidate_id") or "").strip() or name
        cand = bucket["cands"].get(cid)
        if cand is None:
            cand = bucket["cands"][cid] = {
                "name": name,
                "party": c.norm_party((row.get("Party_Code") or "").strip() or None),
                "votes": 0,
            }
        cand["votes"] += votes

    out: list[RaceResult] = []
    seen: set[str] = set()
    for title, bucket in contests.items():
        key = race_key(title)
        if str(key) in seen:
            continue
        cands = [
            CandidateResult(name=v["name"], party=v["party"], votes=v["votes"])
            for v in bucket["cands"].values() if v["name"]
        ]
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=AkDoe.name,
            url=url,
            total_votes=sum(x.votes for x in cands),
        ))
    return out


def csv_url(date: str) -> str:
    folder = ELECTIONS.get(date)
    if not folder:
        raise SourceUnavailable(
            f"AK: no results folder configured for {date}; the Division of Elections "
            f"publishes one per election at {BASE}/results/ (add the <YY>GENR id to ELECTIONS)"
        )
    return f"{BASE}/results/{folder}/ENRbyPrecinct.csv"


@register
class AkDoe(ResultsSource):
    name = "ak_doe"
    states = frozenset({"AK"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ak_keys = [k for k in keys if k.state == "AK"]
        if not ak_keys:
            return []
        text = await c.get_text(client, csv_url(c.election_date()))
        return c.select(parse_ak(text, fetched_at=c.utcnow()), ak_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "AK":
            return []
        text = await c.get_text(client, csv_url(c.election_date()))
        return [(r.key, r.title or str(r.key)) for r in parse_ak(text, fetched_at=c.utcnow())]
