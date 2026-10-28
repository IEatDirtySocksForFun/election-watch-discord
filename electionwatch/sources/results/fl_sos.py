"""Florida — Department of State election results extract.

Florida publishes a tab-delimited extract of statewide results:

    POST https://results.elections.myflorida.com/ResultsExtract.Asp
    ElectionDate=11/5/2024&OfficialResults=Y&PartyRaces=N&DataMode=

The response is a tab-delimited file with one row per candidate per county.
Statewide totals are obtained by aggregating county rows. Race codes:

    PRE  President of the United States (mapped to LOCAL)
    USS  United States Senator
    USR  United States Representative (district in Juris1num)
    STS  State Senator (district in Juris1num)
    STR  State Representative (district in Juris1num)
    Axx  Constitutional amendment / ballot measure
    Dxx  District Court of Appeals retention, CTJ circuit judge, etc. -> LOCAL

The 2026 general election date is known (2026-11-03); the POST body is the
same format. Discovery is not needed.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

API = "https://results.elections.myflorida.com/ResultsExtract.Asp"
HUMAN = "https://results.elections.myflorida.com/"

#: date -> ElectionDate value for the POST form.
ELECTIONS: dict[str, str] = {
    "2024-11-05": "11/5/2024",
    "2026-11-03": "11/3/2026",
}


_PARTY_RE = re.compile(r"\s*\((?:REP|DEM|LIB|GRN|GRE|IND|NPA|NP)\)\s*$", re.I)


def _candidate_name(last: str, first: str, middle: str, race_code: str) -> tuple[str, bool]:
    """Build a display name from Florida's Last/First/Middle fields.

    President rows store the ticket as Last=VP-last, First=Pres-last, Middle='/',
    e.g. ('Vance', 'Trump', '/'). Everything else is a normal person row.
    """
    last = (last or "").strip()
    first = (first or "").strip()
    middle = (middle or "").strip()
    if race_code == "PRE" and middle == "/":
        return f"{first.upper()} / {last.upper()}", False
    parts = [part for part in (first, middle, last) if part and part != "/"]
    name = " ".join(parts).upper()
    name, incumbent = c.strip_incumbent(name)
    return name, incumbent


def _district(juris1: str) -> str:
    digits = re.sub(r"\D", "", juris1 or "")
    return f"{int(digits):02d}" if digits else "00"


def race_key(race_code: str, race_name: str, juris1: str) -> RaceKey:
    """Map a Florida race code + district to a RaceKey."""
    code = (race_code or "").upper().strip()
    special = c.is_special(race_name)
    if code == "USS":
        return RaceKey("FL", Level.SENATE, "SEN", None, special)
    if code == "USR":
        return RaceKey("FL", Level.HOUSE, "HOUSE", _district(juris1), special)
    if code == "STS":
        return RaceKey("FL", Level.STATE, "SD", _district(juris1), special)
    if code == "STR":
        return RaceKey("FL", Level.STATE, "HD", _district(juris1), special)
    if code == "PRE":
        return c.local_key("FL", "president-of-the-united-states")
    if re.fullmatch(r"A\d+", code):
        return c.local_key("FL", race_name or f"amendment-{code[1:]}")
    return c.local_key("FL", race_name or code)


def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def parse_fl(text: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the Florida tab-delimited extract."""
    rows = _rows(text)
    if not rows:
        return []

    # Group by race and candidate.
    races: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        code = (row.get("RaceCode") or "").strip().upper()
        name = (row.get("OfficeDesc") or "").strip()
        j1 = (row.get("Juris1num") or "").strip()
        j2 = (row.get("Juris2num") or "").strip()
        key = (code, j1, j2, name)
        race = races.setdefault(
            key,
            {
                "code": code,
                "name": name,
                "j1": j1,
                "j2": j2,
                "candidates": {},
                "precincts": 0,
                "precincts_reporting": 0,
                "counties": set(),
            },
        )
        cand_key = (row.get("CanNameLast"), row.get("CanNameFirst"), row.get("CanNameMiddle"))
        cand = race["candidates"].setdefault(
            cand_key,
            {
                "last": row.get("CanNameLast"),
                "first": row.get("CanNameFirst"),
                "middle": row.get("CanNameMiddle"),
                "party": row.get("PartyCode"),
                "votes": 0,
            },
        )
        cand["votes"] += c.to_int(row.get("CanVotes"))
        # Precinct counts repeat on every candidate row of a county: count each county once.
        county = (row.get("CountyCode") or "").strip()
        if county not in race["counties"]:
            race["counties"].add(county)
            race["precincts"] += c.to_int(row.get("Precincts"))
            race["precincts_reporting"] += c.to_int(row.get("PrecinctsReporting"))

    results: list[RaceResult] = []
    seen: set[str] = set()
    for race in races.values():
        key = race_key(race["code"], race["name"], race["j1"])
        ident = str(key)
        if ident in seen:
            continue
        cands = []
        for cand in race["candidates"].values():
            name, incumbent = _candidate_name(cand["last"], cand["first"], cand["middle"], race["code"])
            if not name:
                continue
            cands.append(
                CandidateResult(
                    name=name,
                    party=c.norm_party(cand["party"]),
                    votes=cand["votes"],
                    incumbent=incumbent,
                    winner=False,
                )
            )
        if not cands:
            continue
        seen.add(ident)
        title = race["name"]
        if race["code"] in ("USR", "STS", "STR"):
            title = f"{title} District {int(race['j1']):d}"
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=FlSos.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=race["precincts_reporting"] or None,
                precincts_total=race["precincts"] or None,
            )
        )
    return results


async def _fetch(client: httpx.AsyncClient, date: str) -> str:
    election_date = ELECTIONS.get(date)
    if not election_date:
        raise SourceUnavailable(f"FL: no extract configured for {date}")
    form_data = {
        "ElectionDate": election_date,
        "OfficialResults": "Y",
        "PartyRaces": "N",
        "DataMode": "",
        "FormsButton2": "Download",
    }
    try:
        resp = await client.post(API, data=form_data)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"POST {API} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceUnavailable(f"POST {API} -> HTTP {resp.status_code}")
    return resp.text


@register
class FlSos(ResultsSource):
    name = "fl_sos"
    states = frozenset({"FL"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        fl_keys = [key for key in keys if key.state == "FL"]
        if not fl_keys:
            return []
        text = await _fetch(client, c.election_date())
        results = parse_fl(text, fetched_at=c.utcnow())
        return c.select(results, fl_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "FL":
            return []
        text = await _fetch(client, c.election_date())
        return [(result.key, result.title or str(result.key)) for result in parse_fl(text, fetched_at=c.utcnow())]
