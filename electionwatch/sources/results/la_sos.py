"""Louisiana — Secretary of State graphical results (voterportal.sos.la.gov).

The public "Graphical Election Results" app is a plain JSON-over-HTTP feed; no key and no
JS needed:

    https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob=ElectionDates.htm
    ...?blob=<YYYYMMDD>/RacesCandidates_Multiparish.htm      races + candidate names
    ...?blob=<YYYYMMDD>/Votes_Multiparish.htm                votes per choice
    ...?blob=CurrentVersion.htm                              null outside election night

`ElectionDates.htm` lists every election with its date, so the folder for the tracked
election is discovered rather than hard-coded:

    {"Dates": {"DefaultElectionDate": "11/03/2026",
               "Date": [{"PKElectionID": "344", "ElectionDate": "11/03/2026",
                         "ResultsOfficial": "0", ...}]}}

`RacesCandidates_Multiparish.htm`:

    {"Races": {"WriteTime": "2026-10-27T05:00:29.223", "Race": [
        {"ID": "67997", "SpecificTitle": "U. S. Senator", "GeneralTitle": "U.S. Senator",
         "OfficeLevel": "140", "IsClosedParty": "0", "CanHaveRunoff": "0",
         "Choice": [{"Color": "0x..", "ID": "123574", "Desc": "Bill Cassidy (REP)"}]}]}}

`Votes_Multiparish.htm` repeats the races with the counts:

    {"Races": {"VersionDateTime": "2024-11-05T21:30:00", "Race": [
        {"ID": "67997", "PrecinctsReporting": "3805", "PrecinctsExpected": "3805",
         "VoterCountQualified": "3046376", "VoterCountVoted": "2021164",
         "Choice": [{"ID": "123574", "VoteTotal": "766870", "Outcome": "Elected"}]}]}}

Candidate text carries the party either as a suffix in parentheses ("Name (REP)") or, for
presidential electors, as the trailing party name ("Name, VP Name Republican").

The media API (`/api/MediaRequests/...`) needs an access key and is not used.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data"
HUMAN = "https://voterportal.sos.la.gov/graphical"

ELECTIONS: dict[str, str | None] = {
    # date -> results folder (YYYYMMDD); None = discover from ElectionDates.htm.
    "2024-11-05": "20241105",
    "2026-11-03": None,
}

#: OfficeLevel -> RaceKey office for the levels the state's own race taxonomy tags.
_LEVELS = {
    "140": Level.SENATE,  # U.S. Senator
    "145": Level.HOUSE,  # U.S. Representative
    "200": Level.STATE,  # State Senator
    "205": Level.STATE,  # State Representative
}

_OFFICES = (
    ("LIEUTENANT GOVERNOR", "LTGOV"),
    ("ATTORNEY GENERAL", "AG"),
    ("SECRETARY OF STATE", "SOS"),
    ("STATE TREASURER", "TREAS"),
    ("COMMISSIONER OF INSURANCE", "INSCOM"),
    ("COMMISSIONER OF AGRICULTURE", "AGCOM"),
    ("GOVERNOR", "GOV"),
)

#: Trailing party names (presidential electors carry no "(XXX)" suffix).
_PARTY_WORDS = {
    "DEMOCRATIC": "DEM",
    "DEMOCRAT": "DEM",
    "REPUBLICAN": "REP",
    "LIBERTARIAN": "LIB",
    "GREEN": "GRN",
    "INDEPENDENT": "IND",
    "CONSTITUTION PARTY": "CON",
    "AMERICAN SOLIDARITY PARTY": "ASP",
    "SOCIALISM AND LIBERATION": "SAL",
    "SOCIALIST WORKERS PARTY": "SWP",
    "JUSTICE FOR ALL": "JFA",
    "WE THE PEOPLE": "WTP",
    "GODLINESS, TRUTH, JUSTICE": "GTJ",
}
_PARTY_CODE = {
    "DEM": "DEM",
    "REP": "REP",
    "LBT": "LIB",
    "LIB": "LIB",
    "GRN": "GRN",
    "IND": "IND",
    "NOPTY": "NPA",
    "OTH": "OTH",
    "PF": "PF",
    "CON": "CON",
}

_DISTRICT_TAIL = re.compile(r"(\d{1,3})(?:ST|ND|RD|TH)?\s+(?:CONGRESSIONAL|REPRESENTATIVE|SENATE|SENATOR)", re.I)

try:  # tzdata is not always present in slim containers
    _LOUISIANA_TZ = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover
    _LOUISIANA_TZ = timezone.utc


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _district(text: str) -> str | None:
    """'3rd Congressional District' / '92nd Representative District' -> '03' / '92'."""
    match = _DISTRICT_TAIL.search(_norm(text))
    if match:
        return f"{int(match.group(1)):02d}"
    return c.district_from(text)


def race_key(name: str, level_code: str | None = None) -> RaceKey:
    """Map a Louisiana contest title (SpecificTitle) to a RaceKey."""
    contest_name = _norm(name)
    special = c.is_special(contest_name)
    contest_name = re.sub(r"\s*\((?:SPECIAL|UNEXPIRED)\)\s*$", "", contest_name)
    # "U. S. Senator" / "U.S. Senator"
    if re.match(r"^U\.?\s?S\.?\s+SENATOR", contest_name):
        return RaceKey("LA", Level.SENATE, "SEN", None, special)
    if re.match(r"^U\.?\s?S\.?\s+REPRESENTATIVE", contest_name):
        return RaceKey("LA", Level.HOUSE, "HOUSE", _district(contest_name) or "00", special)
    for title, office in _OFFICES:
        if contest_name.startswith(title):
            return RaceKey("LA", Level.GOVERNOR, office, None, special)
    if contest_name.startswith("STATE SENATOR"):
        return RaceKey("LA", Level.STATE, "SD", _district(contest_name) or "00", special)
    if contest_name.startswith("STATE REPRESENTATIVE"):
        return RaceKey("LA", Level.STATE, "HD", _district(contest_name) or "00", special)
    level = _LEVELS.get(str(level_code or ""))
    if level is Level.HOUSE:
        return RaceKey("LA", Level.HOUSE, "HOUSE", _district(contest_name) or "00", special)
    if level is Level.STATE:
        office = "SD" if "SENAT" in contest_name else "HD"
        return RaceKey("LA", Level.STATE, office, _district(contest_name) or "00", special)
    return c.local_key("LA", contest_name)


def split_candidate(desc: str) -> tuple[str, str | None]:
    """'Bill Cassidy (REP)' -> ('Bill Cassidy', 'REP'); electors carry the party as text."""
    text = re.sub(r"\s+", " ", (desc or "").strip())
    match = re.search(r"\(([A-Za-z]{2,8})\)\s*$", text)
    if match:
        code = match.group(1).upper()
        return text[: match.start()].strip(), _PARTY_CODE.get(code, c.norm_party(code))
    upper = text.upper()
    for word, party in _PARTY_WORDS.items():
        if upper.endswith(" " + word):
            return text[: len(text) - len(word)].strip().rstrip(","), party
    return text, None


def _updated(votes: dict) -> datetime | None:
    """VersionDateTime moves on each upload; archived exports carry 1900-01-01 instead."""
    raw = votes.get("VersionDateTime")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).split(".")[0])
    except ValueError:
        return None
    if stamp.year <= 2000:
        return None
    return stamp.replace(tzinfo=_LOUISIANA_TZ).astimezone(timezone.utc)


def _rows(payload, key: str = "Race") -> tuple[dict, list[dict]]:
    if not payload:  # no votes published yet, or a trimmed fixture without the block
        return {}, []
    races = payload.get("Races") if isinstance(payload, dict) else None
    if not isinstance(races, dict):
        raise SourceUnavailable("LA: results blob is not the expected {'Races': {...}} JSON")
    return races, [row for row in (races.get(key) or []) if isinstance(row, dict)]


def parse_la(
    races_payload,
    votes_payload,
    *,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of RacesCandidates_Multiparish + Votes_Multiparish."""
    race_doc, races = _rows(races_payload)
    vote_doc, votes = _rows(votes_payload)
    updated = _updated(vote_doc)
    counts = {str(vote_row.get("ID")): vote_row for vote_row in votes if vote_row.get("ID") is not None}

    results: list[RaceResult] = []
    seen: set[str] = set()
    for race in races:
        name = str(race.get("SpecificTitle") or race.get("GeneralTitle") or "").strip()
        if not name or str(race.get("IsClosedParty")) == "1":
            continue  # party-only primaries are not the contest the bot tracks
        key = race_key(name, race.get("OfficeLevel"))
        if str(key) in seen:
            continue
        tally = counts.get(str(race.get("ID")), {})
        vote_by_choice = {
            str(vote_row.get("ID")): vote_row for vote_row in (tally.get("Choice") or []) if isinstance(vote_row, dict)
        }
        cands: list[CandidateResult] = []
        for choice in race.get("Choice") or []:
            if not isinstance(choice, dict):
                continue
            cand_name, party = split_candidate(str(choice.get("Desc") or ""))
            if not cand_name:
                continue
            vote = vote_by_choice.get(str(choice.get("ID")), {})
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=party,
                    votes=c.to_int(vote.get("VoteTotal")),
                    winner=str(vote.get("Outcome") or "").strip().lower() == "elected",
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                name,
                cands,
                fetched_at,
                source=LaSos.name,
                url=HUMAN,
                total_votes=c.to_int(tally.get("VoterCountVoted")) or None,
                precincts_reporting=c.to_int(tally.get("PrecinctsReporting")) or None,
                precincts_total=c.to_int(tally.get("PrecinctsExpected")) or None,
                registered_voters=c.to_int(tally.get("VoterCountQualified")) or None,
                source_updated_at=updated,
            )
        )
    return results


def _blob_url(name: str) -> str:
    return f"{BASE}?blob={name}"


async def _folder(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    payload = await c.get_json(client, _blob_url("ElectionDates.htm"))
    entries = ((payload or {}).get("Dates") or {}).get("Date") or []
    target = c.us_date(date)
    for entry in entries:
        if str(entry.get("ElectionDate")) == target:
            return target[6:10] + target[0:2] + target[3:5]
    raise SourceUnavailable(
        f"LA: ElectionDates.htm has no election dated {target} (checked {_blob_url('ElectionDates.htm')})"
    )


async def _races(client: httpx.AsyncClient, folder: str):
    return await c.get_json(client, _blob_url(f"{folder}/RacesCandidates_Multiparish.htm"))


async def _votes(client: httpx.AsyncClient, folder: str):
    return await c.get_json(client, _blob_url(f"{folder}/Votes_Multiparish.htm"))


@register
class LaSos(ResultsSource):
    name = "la_sos"
    states = frozenset({"LA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "LA" for key in keys):
            return []
        folder = await _folder(client, c.election_date())
        parsed = parse_la(await _races(client, folder), await _votes(client, folder), fetched_at=c.utcnow())
        return c.select(parsed, [key for key in keys if key.state == "LA"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "LA":
            return []
        folder = await _folder(client, c.election_date())
        _, races = _rows(await _races(client, folder))
        return [
            (
                race_key(str(race_row.get("SpecificTitle") or ""), race_row.get("OfficeLevel")),
                str(race_row.get("SpecificTitle") or ""),
            )
            for race_row in races
            if str(race_row.get("SpecificTitle") or "").strip()
        ]
