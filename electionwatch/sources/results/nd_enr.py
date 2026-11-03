"""North Dakota — Election Night Reporting API (resultsnd.sos.nd.gov).

For the 2026 general election North Dakota moved its live count to a new
Election Night Reporting site, linked from the Secretary of State's
``/elections/election-results`` page. The site is a single-page app over a JSON
API on its own host:

    https://api.resultsnd.sos.nd.gov/Election/GetElectionList?cid=north-dakota
    https://api.resultsnd.sos.nd.gov/Contest/GetContestSearchList?cid=north-dakota&electionID=348
    https://api.resultsnd.sos.nd.gov/Contest/GetContestResults?cId=north-dakota&electionID=348&contestType=SW

``GetElectionList`` gives the election ids and dates, ``GetContestSearchList``
one object per contest with its choices (candidate names, party ids, write-in
flag), and ``GetContestResults`` the vote counts per choice, one call per
contest type (``SW`` statewide, ``LEG`` legislative districts). The results
payload carries choice ids only, so the two payloads are joined on
``contestID`` / ``choiceID``.

The older ASP.NET pages on results.sos.nd.gov (read by ``nd_sos``) still serve
the 2024 general election and are now an archive; this adapter is the live
2026 feed. Before polls close every contest answers with zero votes and zero
precincts reported, which is the state of the fixtures.

Party ids are the state's own (``REP``, ``DEM``, ``IND``, ``LIB``, ``GRE``,
``CON``, ``NON``, ``OTH``); a ballot measure's choices carry a blank party.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from electionwatch.models import CandidateResult, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from .nd_sos import race_key

BASE = "https://api.resultsnd.sos.nd.gov"
CLIENT = "north-dakota"
LIST = f"{BASE}/Election/GetElectionList?cid={CLIENT}"
CONTESTS = f"{BASE}/Contest/GetContestSearchList?cid={CLIENT}&electionID={{}}"
RESULTS = f"{BASE}/Contest/GetContestResults?cId={CLIENT}&electionID={{}}&contestType={{}}"
HUMAN = "https://resultsnd.sos.nd.gov/#election={}"

#: Contest types the bot tracks: statewide (includes the at-large House seat,
#: the row offices and the measures) and legislative districts. County, city,
#: school and conservation contests are not tracked.
TYPES = ("SW", "LEG")

#: ISO election date -> election id. The 2026 general id was read from
#: GetElectionList on 2026-11-02; other dates are discovered from that list.
ELECTIONS = {"2026-11-03": "348"}

_WRITE_IN = re.compile(r"write[- ]?in(\s+write[- ]?in)?", re.I)


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _contests(payload) -> dict:
    """Contest map from a GetContestSearchList or GetContestResults response."""
    if not isinstance(payload, dict):
        return {}
    body = payload.get("response")
    contests = body.get("contests") if isinstance(body, dict) else None
    return contests if isinstance(contests, dict) else {}


def _timestamp(value) -> datetime | None:
    """'2026-10-22T19:14:18.2013678Z' -> an aware UTC datetime."""
    text = _clean(value)
    if not text:
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # the API sends seven fraction digits
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def pick_election(payload, date: str) -> str | None:
    """Election id whose date is ``date`` (ISO), from GetElectionList."""
    entries = payload if isinstance(payload, list) else None
    if entries is None and isinstance(payload, dict):
        body = payload.get("response")
        entries = body if isinstance(body, list) else None
    for entry in entries or []:
        if isinstance(entry, dict) and _clean(entry.get("electionDate"))[:10] == date:
            return _clean(entry.get("electionID")) or None
    return None


def parse_nd_enr(contest_list, results, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Join GetContestSearchList with one GetContestResults payload per contest type."""
    meta = {}
    for contest_id, contest in _contests(contest_list).items():
        if isinstance(contest, dict):
            meta[str(contest.get("contestId") or contest.get("contestID") or contest_id)] = contest

    parsed: list[RaceResult] = []
    seen: set[str] = set()
    for payload in results if isinstance(results, list) else [results]:
        for contest_id, counted in _contests(payload).items():
            if not isinstance(counted, dict):
                continue
            contest = meta.get(str(counted.get("contestID") or contest_id))
            if contest is None:
                continue  # a contest the race list did not describe
            title = _clean(contest.get("contestName") or counted.get("contestName"))
            if not title:
                continue
            key = race_key(title)
            if str(key) in seen:
                continue
            described = {
                str(choice.get("id") or ""): choice
                for choice in (contest.get("choices") or {}).values()
                if isinstance(choice, dict)
            }
            candidates: list[CandidateResult] = []
            for row in counted.get("choices") or []:
                if not isinstance(row, dict):
                    continue
                described_choice = described.get(str(row.get("choiceID") or "")) or {}
                name = _clean(described_choice.get("name"))
                if not name:
                    continue
                if described_choice.get("isWriteIn") or _WRITE_IN.fullmatch(name):
                    name = "Write-in"
                candidates.append(
                    CandidateResult(
                        name=name,
                        party=c.norm_party(described_choice.get("partyID") or row.get("partyID")),
                        votes=c.to_int(row.get("totalVotes")),
                        pct=c.to_pct(row.get("votePercent")),
                        winner=bool(row.get("isWinner")),
                    )
                )
            if not candidates:
                continue
            seen.add(str(key))
            parsed.append(
                c.make_result(
                    key,
                    title,
                    candidates,
                    fetched_at,
                    source=NdEnr.name,
                    url=url,
                    total_votes=c.to_int(counted.get("totalVotes")),
                    precincts_reporting=c.to_int(counted.get("precinctsReporting")),
                    precincts_total=c.to_int(counted.get("totalPrecincts")),
                    source_updated_at=_timestamp(payload.get("lastUpdated") if isinstance(payload, dict) else None),
                )
            )
    return parsed


async def election_id(date: str, client: httpx.AsyncClient) -> str:
    """Election id for ``date``: pinned in ELECTIONS, else discovered."""
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    found = pick_election(await c.get_json(client, LIST), date)
    if not found:
        raise SourceUnavailable(f"ND: no election dated {date} in {LIST}")
    return found


@register
class NdEnr(ResultsSource):
    name = "nd_enr"
    states = frozenset({"ND"})
    scrape = False  # JSON API published by the state

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = [key for key in keys if key.state == "ND"]
        if not wanted:
            return []
        ident = await election_id(c.election_date(), client)
        contest_list = await c.get_json(client, CONTESTS.format(ident))
        payloads: list = []
        for contest_type in TYPES:
            try:
                payloads.append(await c.get_json(client, RESULTS.format(ident, contest_type)))
            except SourceUnavailable:
                continue
        if not payloads:
            raise SourceUnavailable(f"ND: no contest results returned ({RESULTS.format(ident, 'SW')})")
        parsed = parse_nd_enr(contest_list, payloads, fetched_at=c.utcnow(), url=HUMAN.format(ident))
        return c.select(parsed, wanted)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "ND":
            return []
        ident = await election_id(c.election_date(), client)
        contest_list = await c.get_json(client, CONTESTS.format(ident))
        rows: dict[str, tuple[RaceKey, str]] = {}
        for contest in _contests(contest_list).values():
            if not isinstance(contest, dict) or contest.get("contestTypeCode") not in TYPES:
                continue
            title = _clean(contest.get("contestName"))
            if not title:
                continue
            key = race_key(title)
            rows.setdefault(str(key), (key, title))
        return list(rows.values())
