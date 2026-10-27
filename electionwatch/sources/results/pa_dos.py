"""Pennsylvania — Department of State election returns (electionreturns.pa.gov).

The site is an Angular app over ``/api/ElectionReturn/<Method>``. Responses are JSON
*strings* containing JSON, so every payload needs a second `json.loads`.

    GetLatestElection                  current election: {"ElectionID", "ElectionDate", ...}
    GetElectionsList                   full list (all years)
    GetOfficeData?officeId=N&...       results for one office; the body nests:

    {"Election": {"United States Senator": [
        {"Statewide$$1$$": [
            {"DistrictId": "1", "District": "Statewide",
             "Candidates": [{"CandidateName": ..., "PartyName": "DEM", "Votes": "3384180",
                             "Percentage": "48.60", ...}]}]}]}}

Office ids (verified against the 2024 general, electionid 105):

    1 President of the United States        11 Representative in Congress
    2 United States Senator                 12 Senator in the General Assembly
    3 Governor                              13 Representative in the General Assembly
    5 Attorney General                       6 Auditor General
    7 State Treasurer

The API publishes no precinct reporting for statewide races and returns "" for
GetUpdatedTimeStamp on archived elections, so those fields are None. Pennsylvania also
publishes county breakdowns (GetCountyBreak / GetCountyData), which need one request per
(county, office) and are deliberately not polled here.

2026 general election id is already published (117, GetLatestElection 2026-10-27); the
adapter also discovers it, so a new id does not need a code change.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

API = "https://www.electionreturns.pa.gov/api/ElectionReturn"
HUMAN = "https://www.electionreturns.pa.gov/"

#: date -> {"election_id", "election_type"} or None = discover via GetLatestElection.
ELECTIONS: dict[str, dict | None] = {
    "2024-11-05": {"election_id": "105", "election_type": "G"},
    "2026-11-03": {"election_id": "117", "election_type": "G"},
}

#: RaceKey-relevant offices.  Keyed by the PA officeId used by GetOfficeData.
OFFICES: dict[str, int] = {
    "SEN": 2,
    "GOV": 3,
    "AG": 5,
    "AUD": 6,
    "TREAS": 7,
    "HOUSE": 11,
    "SD": 12,
    "HD": 13,
}

#: Office titles as PA prints them -> RaceKey office code.
_TITLES = {
    "UNITED STATES SENATOR": "SEN",
    "GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "AUDITOR GENERAL": "AUD",
    "STATE TREASURER": "TREAS",
}

_ORD = re.compile(r"^(\d+)(?:st|nd|rd|th)\b", re.I)


def _district_number(group: dict) -> str | None:
    m = _ORD.match(str(group.get("District") or ""))
    if m:
        return f"{int(m.group(1)):02d}"
    did = str(group.get("DistrictId") or "")
    return f"{int(did):02d}" if did.isdigit() else None


def race_key(office: str, group: dict | None = None) -> RaceKey:
    """Map a PA office name (+ district group) to a RaceKey."""
    name = re.sub(r"\s+", " ", (office or "").strip()).upper()
    if name in _TITLES:
        return RaceKey("PA", Level.GOVERNOR if _TITLES[name] != "SEN" else Level.SENATE,
                       _TITLES[name])
    dist = _district_number(group) if group else None
    if name == "REPRESENTATIVE IN CONGRESS":
        return RaceKey("PA", Level.HOUSE, "HOUSE", dist or "AL")
    if name == "SENATOR IN THE GENERAL ASSEMBLY":
        return RaceKey("PA", Level.STATE, "SD", dist or "00")
    if name == "REPRESENTATIVE IN THE GENERAL ASSEMBLY":
        return RaceKey("PA", Level.STATE, "HD", dist or "00")
    if name == "PRESIDENT OF THE UNITED STATES":
        return c.local_key("PA", "president-of-the-united-states")
    return c.local_key("PA", name)


def _decode(payload) -> dict:
    """PA returns a JSON string inside JSON; accept either layer."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return {}
    return payload if isinstance(payload, dict) else {}


def parse_pa(payload, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one GetOfficeData response (any office)."""
    doc = _decode(payload)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for office, groups in (doc.get("Election") or {}).items():
        for group in groups if isinstance(groups, list) else [groups]:
            if not isinstance(group, dict):
                continue
            for inner in group.values():  # "1st Congressional District$$2$$": [...]
                for race in inner if isinstance(inner, list) else [inner]:
                    if not isinstance(race, dict):
                        continue
                    key = race_key(office, race)
                    if str(key) in seen:
                        continue
                    cands = []
                    for cand in race.get("Candidates") or []:
                        cand_name, incumbent = c.strip_incumbent(
                            str(cand.get("CandidateName") or "").strip())
                        if not cand_name:
                            continue
                        cands.append(CandidateResult(
                            name=cand_name,
                            party=c.norm_party(cand.get("PartyName")),
                            votes=c.to_int(cand.get("Votes")),
                            pct=c.to_pct(cand.get("Percentage")),
                            incumbent=incumbent,
                            winner=False,  # PA publishes no winner flag in this API
                        ))
                    if not cands:
                        continue
                    seen.add(str(key))
                    district = str(race.get("District") or "")
                    title = office if district.lower() in ("", "statewide") else district
                    results.append(c.make_result(
                        key, title, cands, fetched_at,
                        source=PaDos.name,
                        url=HUMAN,
                        total_votes=sum(x.votes for x in cands),
                    ))
    return results


def latest_rows(payload) -> list[dict]:
    """Rows from GetLatestElection, which is a JSON string of a JSON list."""
    obj = payload
    while isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            return []
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    return [obj] if isinstance(obj, dict) else []


async def _resolve(client: httpx.AsyncClient, date: str) -> dict:
    pinned = ELECTIONS.get(date)
    if pinned:
        return dict(pinned)
    rows = latest_rows(await c.get_json(
        client, f"{API}/GetLatestElection?methodName=GetLatestElection"))
    for row in rows:
        if str(row.get("ElectionDate", ""))[:10] == date:
            return {"election_id": str(row["ElectionID"]),
                    "election_type": str(row.get("ElectionType") or "G")}
    raise SourceUnavailable(f"PA: GetLatestElection does not list {date}")


async def _office(client: httpx.AsyncClient, office_id: int, election: dict) -> dict | None:
    params = (f"officeId={office_id}&methodName=GetOfficeData"
              f"&electionid={election['election_id']}"
              f"&electiontype={election['election_type']}")
    for active in ("1", "0"):
        doc = _decode(await c.get_json(client, f"{API}/GetOfficeData?{params}&isactive={active}"))
        if doc.get("Election"):
            return doc
    return None


@register
class PaDos(ResultsSource):
    name = "pa_dos"
    states = frozenset({"PA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        pa_keys = [k for k in keys if k.state == "PA"]
        if not pa_keys:
            return []
        election = await _resolve(client, c.election_date())
        results: list[RaceResult] = []
        for office_id in OFFICES.values():
            doc = await _office(client, office_id, election)
            if doc:
                results.extend(parse_pa(doc, fetched_at=c.utcnow()))
        return c.select(results, pa_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "PA":
            return []
        election = await _resolve(client, c.election_date())
        out: list[tuple[RaceKey, str]] = []
        for office_id in OFFICES.values():
            doc = await _office(client, office_id, election)
            for r in parse_pa(doc, fetched_at=c.utcnow()) if doc else []:
                out.append((r.key, r.title or str(r.key)))
        return out
