"""Arkansas — Secretary of State results, served by Tally-ENR (JSON).

The SOS "Election Results" page (https://www.sos.arkansas.gov/elections/research/
election-results) lists every election with a link to the vendor's results app,
`https://arkansas.tally-enr.com/#election=<id>`. The app loads one static file:

    https://arkansas.tally-enr.com/results/arkansas/<id>/FullDataFile.json

Older elections use numeric ids and the file carries the names:

    {"ElectionInfo": {"ElectionID": 1846, "ElectionName": "2024 General", ...},
     "Turnout": {"VotesCast": 1190172, "RegisteredVoters": 1828129, ...},
     "ContestData": [{"ContestName": "U.S. Congress District 01",
                      "TotalVotes": 267177, "PrecinctsReporting": 687,
                      "TotalPrecincts": 687,
                      "Candidates": [{"Name": "...", "PartyName": "Republican Party",
                                      "TotalVotes": 194711}]}]}

Elections from 2026 on are addressed by UUID and use a newer shape
(`ElectionData` / `TurnoutData` / `ContestData[].ContestID` + `Choices`) that has
*no contest names in the file* - the app resolves them through an authenticated
API. Those files cannot be mapped to races, so the adapter raises SourceUnavailable
with an explanation instead of guessing. 2026 TODO: revisit if the SOS starts
publishing a named feed (or a contest-name dictionary) for the new format.

No anti-bot measures, no key: the JSON is a plain S3 object. Discovery is a single
HTML page fetch; polling is the cost of one ~15 MB download per update, so the
poller's interval (not this adapter) decides the load.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

DISCOVERY = "https://www.sos.arkansas.gov/elections/research/election-results"
FILE = "https://arkansas.tally-enr.com/results/arkansas/{eid}/FullDataFile.json"

#: tracked election day -> how the SOS page starts the label for that election.
ELECTIONS: dict[str, str] = {
    "2024-11-05": "2024 General Election",
    "2026-03-03": "2026 Preferential Primary",
    "2026-11-03": "2026 General Election",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "TREASURER": "TREAS",
    "STATE AUDITOR": "AUD",
    "AUDITOR": "AUD",
    "COMMISSIONER OF STATE LANDS": "LANDCOM",
    "LAND COMMISSIONER": "LANDCOM",
}

_SKIP = {"over votes", "under votes", "overvote", "undervote", "write-in", "write in"}


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


_CODES = frozenset({"DEM", "REP", "LIB", "GRN", "CON", "IND", "WRI", "NPA", "ASP", "FWD", "PF"})


def _party(value) -> str | None:
    """'Republican Party' -> 'REP'; 'Nevada Green Party' -> 'GRN'."""
    text = re.sub(r"\s+party\s*$", "", str(value or "").strip(), flags=re.I)
    if not text:
        return None
    words = text.split()
    for i in range(len(words)):
        code = c.norm_party(" ".join(words[i:]))
        if code in _CODES:
            return code
    return c.norm_party(text)


def race_key(contest: str) -> RaceKey:
    """Map a Tally-ENR contest name to a RaceKey."""
    office_name = _upper(contest)
    special = c.is_special(office_name)
    if office_name == "U.S. PRESIDENT":
        return c.local_key("AR", "us-president")
    if office_name in ("U.S. SENATE", "U.S. SENATOR"):
        return RaceKey("AR", Level.SENATE, "SEN", None, special)
    if office_name in _STATEWIDE:
        return RaceKey("AR", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    match = re.match(r"^U\.S\. CONGRESS(?:IONAL)? DISTRICT\s+(\d{1,2})$", office_name)
    if match:
        return RaceKey("AR", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE SENATE(?:OR)? DISTRICT\s+(\d{1,3})$", office_name)
    if match:
        return RaceKey("AR", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE REPRESENTATIVE DISTRICT\s+(\d{1,3})$", office_name)
    if match:
        return RaceKey("AR", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
    return c.local_key("AR", office_name)


def election_links(html: str) -> list[tuple[str, str]]:
    """(label, tally-enr election id) for every election the SOS page links."""
    links: list[tuple[str, str]] = []
    for anchor in HTMLParser(html or "").css("a"):
        href = htmllib.unescape(anchor.attributes.get("href") or "")
        if "tally-enr.com" not in href or "#election=" not in href:
            continue
        eid = href.split("#election=", 1)[1].split("&", 1)[0].strip("/")
        label = re.sub(r"\s+", " ", anchor.text()).strip()
        if eid and label:
            links.append((label, eid))
    return links


def find_election(html: str, date: str) -> str:
    """The election id for `date`, from the SOS page."""
    prefix = ELECTIONS.get(date)
    if not prefix:
        raise SourceUnavailable(
            f"AR: no SOS label configured for {date}; add it to ELECTIONS "
            f"(the page at {DISCOVERY} lists every election)"
        )
    for label, eid in election_links(html):
        if label.lower().startswith(prefix.lower()):
            return eid
    raise SourceUnavailable(f"AR: the SOS page does not list {prefix!r} yet - the results are not published")


def parse_ar(payload: dict, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Pure parse of a Tally-ENR FullDataFile.json."""
    contests = payload.get("ContestData") or []
    if contests and "ContestName" not in contests[0]:
        raise SourceUnavailable(
            "AR: this election's FullDataFile.json has no contest names (the 2026+ "
            "UUID format resolves them through an authenticated API) - Arkansas "
            "cannot be mapped for this election"
        )
    turnout = payload.get("Turnout") or {}
    registered = c.to_int(turnout.get("RegisteredVoters"))
    turnout_pct = c.to_pct(turnout.get("VotePercent"))

    results: list[RaceResult] = []
    for contest in contests:
        name = (contest.get("ContestName") or "").strip()
        if not name:
            continue
        cands: list[CandidateResult] = []
        for cand in contest.get("Candidates") or []:
            cname = (cand.get("Name") or "").strip()
            if not cname or cname.lower() in _SKIP:
                continue
            cands.append(
                CandidateResult(
                    name=cname,
                    party=_party(cand.get("PartyName")),
                    votes=c.to_int(cand.get("TotalVotes")),
                )
            )
        if not cands:
            continue
        votes = c.to_int(contest.get("TotalVotes")) or sum(candidate.votes for candidate in cands)
        results.append(
            c.make_result(
                race_key(name),
                name,
                cands,
                fetched_at,
                source=ArSos.name,
                url=url,
                total_votes=votes,
                precincts_reporting=c.to_int(contest.get("PrecinctsReporting")),
                precincts_total=c.to_int(contest.get("TotalPrecincts")),
                registered_voters=registered or None,
                turnout_pct=turnout_pct,
            )
        )
    return results


async def _election_id(client: httpx.AsyncClient, date: str) -> str:
    try:
        page = await c.get_text(client, DISCOVERY)
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"AR: {DISCOVERY} failed ({exc})") from exc
    return find_election(page, date)


async def _all_races(client: httpx.AsyncClient, date: str, fetched_at: datetime) -> list[RaceResult]:
    eid = await _election_id(client, date)
    url = FILE.format(eid=eid)
    payload = await c.get_json(client, url)
    if not isinstance(payload, dict):
        raise SourceUnavailable(f"AR: unexpected payload from {url}")
    return parse_ar(payload, fetched_at=fetched_at, url=url)


@register
class ArSos(ResultsSource):
    name = "ar_sos"
    states = frozenset({"AR"})

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ar_keys = [key for key in keys if key.state == "AR"]
        if not ar_keys:
            return []
        return c.select(await _all_races(client, c.election_date(), c.utcnow()), ar_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "AR":
            return []
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in await _all_races(client, c.election_date(), c.utcnow())
        ]
