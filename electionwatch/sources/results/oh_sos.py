"""Ohio — Secretary of State live election results (liveresults.ohiosos.gov).

The site is a single-page app over a small JSON API (no auth, no key):

    https://liveresults.ohiosos.gov/Api/v1/Contest/getContestList
        -> [{"displayName": "Representative to Congress",
             "contestGroupings": [{"contestName": "Representative to Congress",
                                   "displayName": "Representative to Congress - District 01",
                                   "contestObjectId": "_55A7..."}, ...]}, ...]

    https://liveresults.ohiosos.gov/Api/v1/Contest/GetContestDetailsObject
        ?contestName=<grouping.contestName>&contestObjectId=<grouping id>
        -> {"contestDisplayName": "...", "contestList": [ ... ]}

`contestList` is one entry per reporting jurisdiction: a single statewide row for a
statewide contest, one row per county for a district contest. Each row carries
`reportedPrecincts` / `totalPrecincts` and `candidatesRunning[]` with `voteCount`
and `candidateParty`. Row votes are summed, so the CD-1 race is the sum over Clinton,
Hamilton and Warren.

The API only ever describes the election the site is currently pointed at, so there is
nothing to date-pin: whatever `getContestList` says is the live election. Rows for a
statewide contest report 0/0 precincts; the real statewide precinct count comes from
`contestStatistics` ("Election Day Precincts Reporting", "Total Number of Election Day
Precincts"). Ohio publishes no winner flag, so `winner` stays False.

Running mates are not folded into candidate names ("Governor and Lieutenant Governor"
rows carry the running mate in `runningMate`) so names stay comparable with the call
sources, which print one name per ticket head.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://liveresults.ohiosos.gov/Api/v1/Contest"
HUMAN = "https://liveresults.ohiosos.gov/"

_STATEWIDE = {
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "AUDITOR OF STATE": "AUD",
    "SECRETARY OF STATE": "SOS",
    "TREASURER OF STATE": "TREAS",
}

_HOUSE = re.compile(r"^REPRESENTATIVE TO CONGRESS\b")
_SENATE = re.compile(r"^STATE SENATOR\b")
_REP = re.compile(r"^STATE REPRESENTATIVE\b")


_SENATE_NAMES = {"U.S. SENATOR", "US SENATOR", "U.S. SENATE", "US SENATE"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    # "U.S. Senator - Unexpired Term Ending 01/03/2029" is the race, the suffix is the term.
    base = re.split(r"\s+-\s+", n)[0].strip()
    if base in _SENATE_NAMES:
        return RaceKey("OH", Level.SENATE, "SEN", None, special)
    if base in _STATEWIDE:
        return RaceKey("OH", Level.GOVERNOR, _STATEWIDE[base], None, special)
    if _HOUSE.match(n):
        return RaceKey("OH", Level.HOUSE, "HOUSE", c.district_from(n) or "AL", special)
    if _SENATE.match(n):
        return RaceKey("OH", Level.STATE, "SD", c.district_from(n) or "00", special)
    if _REP.match(n):
        return RaceKey("OH", Level.STATE, "HD", c.district_from(n) or "00", special)
    return c.local_key("OH", n)


def _stat(stats, label: str) -> str | None:
    for item in ((stats or {}).get("allStatistics") or []):
        if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == label.lower():
            return str(item.get("value") or "").strip()
    return None


def _precincts(detail: dict, rows: list[dict]) -> tuple[int | None, int | None]:
    """Reported/total precincts, falling back to Ohio's statewide statistics block."""
    reporting = sum(c.to_int(r.get("reportedPrecincts")) for r in rows)
    total = sum(c.to_int(r.get("totalPrecincts")) for r in rows)
    if total:
        return reporting, total
    stats = detail.get("contestStatistics")
    total = c.to_int(_stat(stats, "Total Number of Election Day Precincts")) or None
    reporting_text = _stat(stats, "Election Day Precincts Reporting")
    reporting = c.to_int(reporting_text) if reporting_text else None
    return reporting, total


def parse_oh(contest_list, details, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of getContestList + the GetContestDetailsObject payloads."""
    order = {
        str(g.get("displayName")): i for i, g in enumerate(_groupings(contest_list or []))
    }
    out: list[RaceResult] = []
    seen: set[str] = set()
    for detail in details or []:
        if not isinstance(detail, dict):
            continue
        title = str(detail.get("contestDisplayName") or detail.get("contestName") or "").strip()
        rows = [r for r in detail.get("contestList") or [] if isinstance(r, dict)]
        if not title or not rows:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        tally: dict[tuple[str, str | None], CandidateResult] = {}
        for row in rows:
            for cand in row.get("candidatesRunning") or []:
                if not isinstance(cand, dict):
                    continue
                name, incumbent = c.strip_incumbent(str(cand.get("primaryCandidate") or "").strip())
                if not name:
                    continue
                party = c.norm_party(cand.get("candidateParty"))
                if cand.get("isWriteIn") and not party:
                    party = "WRI"
                slot = (name, party)
                if slot not in tally:
                    tally[slot] = CandidateResult(name=name, party=party, votes=0, incumbent=incumbent)
                tally[slot].votes += c.to_int(cand.get("voteCount"))
        cands = list(tally.values())
        if not cands:
            continue
        seen.add(str(key))
        reporting, total = _precincts(detail, rows)
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=OhSos.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=reporting or None,
            precincts_total=total or None,
        ))

    # The site lists statewide offices first; keep that order rather than dict order.
    out.sort(key=lambda result: order.get(str(result.title), len(order)))
    return out


async def _details(client: httpx.AsyncClient, groupings: list[dict]) -> list[dict]:
    out = []
    for grouping in groupings:
        url = str(httpx.URL(f"{BASE}/GetContestDetailsObject", params={
            "contestName": str(grouping.get("contestName") or ""),
            "contestObjectId": str(grouping["contestObjectId"]),
            "resultsLevel": "",
            "countyId": "",
        }))
        out.append(await c.get_json(client, url))
    return out


async def _contest_list(client: httpx.AsyncClient) -> list[dict]:
    payload = await c.get_json(client, f"{BASE}/getContestList")
    if not isinstance(payload, list):
        raise SourceUnavailable("OH: getContestList did not return a JSON list")
    return [x for x in payload if isinstance(x, dict)]


def _groupings(contest_list: list[dict]) -> list[dict]:
    return [
        grouping
        for contest in contest_list
        for grouping in (contest.get("contestGroupings") or [])
        if isinstance(grouping, dict) and grouping.get("contestObjectId")
    ]


@register
class OhSos(ResultsSource):
    name = "oh_sos"
    states = frozenset({"OH"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = [k for k in keys if k.state == "OH"]
        if not wanted:
            return []
        contest_list = await _contest_list(client)
        selected = [
            g for g in _groupings(contest_list)
            if str(race_key(str(g.get("displayName") or g.get("contestName") or ""))) in
            {str(k) for k in wanted}
        ]
        if not selected:
            return []
        parsed = parse_oh(contest_list, await _details(client, selected), fetched_at=c.utcnow())
        return c.select(parsed, wanted)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "OH":
            return []
        contest_list = await _contest_list(client)
        seen: dict[str, tuple[RaceKey, str]] = {}
        for grouping in _groupings(contest_list):
            title = str(grouping.get("displayName") or grouping.get("contestName") or "").strip()
            if not title:
                continue
            key = race_key(title)
            seen.setdefault(str(key), (key, title))
        return list(seen.values())
