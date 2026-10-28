"""Virginia — Dept. of Elections ENR (enr.elections.virginia.gov).

The ENR site is Enhanced Voting's public results app and serves plain JSON:

    https://enr.elections.virginia.gov/results/public/api/elections/virginia
        -> {"elections": [{"publicElectionId": "2026-November-General",
                           "electionDate": "2026-11-03", ...}, ...]}

    .../api/elections/virginia/<publicElectionId>/data
        -> {"ballotItems": [...], "statistics": [...], "voterRegistration": [...]}

Every ballot item is one contest:

    {"name": [{"languageId": "en", "text": "Member, House of Representatives (1st District)"}],
     "contestType": "Candidate",                 # or "BallotMeasure"
     "voteTotal": 12345,
     "reportingStatus": {"reportingUnits": 18, "totalUnits": 18,
                         "asOf": "2026-10-26T22:53:33.7465541Z"},
     "summaryResults": {"ballotOptions": [
         {"name": [{"languageId": "en", "text": "..."}],
          "party": {"abbreviation": "D"}, "voteCount": 1234,
          "isWriteIn": false, "isWinner": true}]}}

Election ids are irregular (`2024NovemberGeneral`, `2026-November-General`), so the
slug is discovered from the jurisdiction listing rather than hard-coded; ELECTIONS only
pins the ones we have checked. `reportingStatus.totalUnits` counts the precincts inside
the contest (133 statewide, 18 for a congressional district), which is what
expected_vote_pct is computed from.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://enr.elections.virginia.gov/results/public/api"
JURISDICTION = "virginia"
HUMAN = "https://enr.elections.virginia.gov/results/public/virginia/"

ELECTIONS: dict[str, str] = {
    # date -> publicElectionId (None = discover from the jurisdiction listing). The listing
    # has answered 404 since late October 2026, so an election must be pinned here to work.
    "2025-11-04": "2025-November-General",
    "2026-11-03": "2026-November-General",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
}

_SENATE = re.compile(r"^MEMBER,? (?:UNITED STATES|U\.S\.) SENATE$")
_HOUSE = re.compile(r"^MEMBER,? (?:UNITED STATES )?HOUSE OF REPRESENTATIVES\b.*$")
_HOUSE_DIST = re.compile(r"\((\d{1,3})(?:ST|ND|RD|TH) DISTRICT\)")
_DELEGATES = re.compile(r"^MEMBER,? HOUSE OF DELEGATES\b")


def _text(value) -> str:
    """Enhanced Voting sends every label as a list of {languageId, text}."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and part.get("languageId") == "en":
                return str(part.get("text") or "").strip()
        for part in value:
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"]).strip()
    return ""


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str) -> RaceKey:
    office_name = _norm(name)
    special = c.is_special(office_name)
    if office_name in ("MEMBER, UNITED STATES SENATE", "MEMBER, U.S. SENATE", "U.S. SENATE") or _SENATE.match(
        office_name
    ):
        return RaceKey("VA", Level.SENATE, "SEN", None, special)
    if office_name in _STATEWIDE:
        return RaceKey("VA", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    if _HOUSE.match(office_name):
        match = _HOUSE_DIST.search(office_name)
        return RaceKey("VA", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}" if match else "AL", special)
    if _DELEGATES.match(office_name):
        match = _HOUSE_DIST.search(office_name)
        return RaceKey("VA", Level.STATE, "HD", f"{int(match.group(1)):02d}" if match else "00", special)
    return c.local_key("VA", office_name)


def _iso(value) -> datetime | None:
    """'2026-10-26T22:53:33.7465541Z' -> aware UTC datetime."""
    raw = str(value or "").strip()
    if not raw:
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", raw)
    if not match:
        return c.parse_updated_at(raw)
    stamp = match.group(1) + ("+00:00" if (match.group(2) or "Z") == "Z" else match.group(2))
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def parse_va(payload, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one ENR `.../data` document."""
    if not isinstance(payload, dict):
        return []
    results: list[RaceResult] = []
    seen: set[str] = set()
    for ballot_item in payload.get("ballotItems") or []:
        if not isinstance(ballot_item, dict):
            continue
        title = _text(ballot_item.get("name"))
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        options = ((ballot_item.get("summaryResults") or {}).get("ballotOptions")) or []
        cands: list[CandidateResult] = []
        for option in options:
            if not isinstance(option, dict):
                continue
            cand_name, incumbent = c.strip_incumbent(_text(option.get("name")))
            if not cand_name or cand_name.upper() == "NO CANDIDATE FILED":
                continue
            party = (option.get("party") or {}).get("abbreviation") if option.get("party") else None
            if option.get("isWriteIn"):
                party = party or "WRI"
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=c.norm_party(party),
                    votes=c.to_int(option.get("voteCount")),
                    incumbent=incumbent,
                    winner=bool(option.get("isWinner")),
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        status = ballot_item.get("reportingStatus") or {}
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=VaElect.name,
                url=HUMAN,
                total_votes=c.to_int(ballot_item.get("voteTotal")) or sum(candidate.votes for candidate in cands),
                precincts_reporting=c.to_int(status.get("reportingUnits")) or None,
                precincts_total=c.to_int(status.get("totalUnits")) or None,
                source_updated_at=_iso(status.get("asOf")),
            )
        )
    return results


def _data_url(election_id: str) -> str:
    return f"{BASE}/elections/{JURISDICTION}/{election_id}/data"


async def _election_id(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    payload = await c.get_json(client, f"{BASE}/elections/{JURISDICTION}")
    for entry in (payload or {}).get("elections") or []:
        if isinstance(entry, dict) and entry.get("electionDate") == date:
            election_id = entry.get("publicElectionId")
            if election_id:
                return str(election_id)
    raise SourceUnavailable(f"VA: no ENR election dated {date}")


async def _payload(client: httpx.AsyncClient, date: str) -> dict:
    election_id = await _election_id(client, date)
    payload = await c.get_json(client, _data_url(election_id))
    if not isinstance(payload, dict):
        raise SourceUnavailable("VA: ENR data is not the expected JSON object")
    return payload


@register
class VaElect(ResultsSource):
    name = "va_elect"
    states = frozenset({"VA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "VA" for key in keys):
            return []
        payload = await _payload(client, c.election_date())
        return c.select(parse_va(payload, fetched_at=c.utcnow()), keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "VA":
            return []
        payload = await _payload(client, c.election_date())
        return [(result.key, result.title or str(result.key)) for result in parse_va(payload, fetched_at=c.utcnow())]
