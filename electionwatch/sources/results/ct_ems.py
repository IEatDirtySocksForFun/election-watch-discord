"""Connecticut — Secretary of the State's Election Management System (EMS).

The Secretary of the State publishes live results through the vendor's public
reporting app. The app is an Angular front end over static JSON files:

    https://ctemspublic.pcctg.net/ng-app/data/Elections.json
    https://ctemspublic.pcctg.net/ng-app/data/election/<id>/Version.json
    https://ctemspublic.pcctg.net/ng-app/data/election/<id>/<version>/Lookupdata.json
    https://ctemspublic.pcctg.net/ng-app/data/election/<id>/<version>/Electiondata.json

`Elections.json` lists every election the system holds (``{"ID": "108", "Name":
"11/03/2026 -- November 2026 State Election", "DefaultElection": "Y"}``).
`Lookupdata.json` carries `officeList` (id -> `NM`/`OT`/`DT`), `candidateIds`
(id -> `NM`, `P`) and `partyIds` (id -> `CD`). `Electiondata.json` carries
`stateVotes` (office id -> one row per party line, `{candidate id: {"V": "1234"}}`)
and the header counts (`PR` = precincts reporting, `T` = towns reporting).

Notes that matter for parsing:

* A candidate nominated by several parties appears once per party line
  (a Democrat also on the Working Families line, a Republican also on the
  Independent line). Rows are merged by candidate name, votes summed, and the
  party of the largest line is kept.
* Write-in rows use party code ``WI``; they are kept as candidates, matching
  the ElectionStats adapter.
* `OT` groups offices: ``SW`` statewide, ``C`` congressional, ``S`` state
  Senate, ``A`` state House, ``PD`` probate, ``SM`` town offices and town
  meeting districts. Only the first four get statewide keys; the rest become
  LOCAL races.
* The entry point answers a 301 to the vendor's canonical host, so every request
  follows redirects.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://ctemspublic.pcctg.net"
HUMAN = BASE + "/"
ELECTIONS = BASE + "/ng-app/data/Elections.json"

_NUMBER = re.compile(r"\d+")
_PRECINCTS = re.compile(r"(\d+)\s*of\s*(\d+)")

#: Party codes the EMS uses that the shared table does not spell the same way.
_CT_PARTIES = {"G": "GRN", "WF": "WFP", "C": "CON", "LI": "LIB", "RE": "REF"}


def election_id(elections: list[dict], date: str) -> str | None:
    """Pick the election whose name starts with the tracked date (``MM/DD/YYYY``)."""
    prefix = datetime.strptime(date, "%Y-%m-%d").strftime("%m/%d/%Y")
    for entry in elections or []:
        if str(entry.get("Name") or "").startswith(prefix):
            return str(entry.get("ID") or "") or None
    return None


def _district(name: str) -> str | None:
    numbers = _NUMBER.findall(name or "")
    return f"{int(numbers[-1]):02d}" if numbers else None


def race_key(office: dict) -> RaceKey:
    name = re.sub(r"\s+", " ", (office.get("NM") or "").strip())
    upper = name.upper()
    special = c.is_special(upper) or "VACANCY" in upper
    if upper == "GOVERNOR AND LIEUTENANT GOVERNOR":
        return RaceKey("CT", Level.GOVERNOR, "GOV", None, special)
    if upper in ("UNITED STATES SENATOR", "U.S. SENATOR"):
        return RaceKey("CT", Level.SENATE, "SEN", None, special)
    if upper in ("SECRETARY OF THE STATE", "SECRETARY OF STATE"):
        return RaceKey("CT", Level.GOVERNOR, "SOS", None, special)
    if upper == "TREASURER":
        return RaceKey("CT", Level.GOVERNOR, "TREAS", None, special)
    if upper == "COMPTROLLER":
        return RaceKey("CT", Level.GOVERNOR, "COMP", None, special)
    if upper == "ATTORNEY GENERAL":
        return RaceKey("CT", Level.GOVERNOR, "AG", None, special)
    if upper.startswith("REPRESENTATIVE IN CONGRESS"):
        return RaceKey("CT", Level.HOUSE, "HOUSE", _district(name), special)
    if upper.startswith("STATE SENATOR"):
        return RaceKey("CT", Level.STATE, "SD", _district(name), special)
    if upper.startswith("STATE REPRESENTATIVE"):
        return RaceKey("CT", Level.STATE, "HD", _district(name), special)
    if upper.startswith("PRESIDENTIAL ELECTORS"):
        return RaceKey("CT", Level.LOCAL, "LOCAL", c.slug("us-president"), special)
    return RaceKey("CT", Level.LOCAL, "LOCAL", c.slug(name), special)


def _offices(lookup: dict) -> dict[str, dict]:
    offices: dict[str, dict] = {}
    for item in lookup.get("officeList") or []:
        for office_id, office in (item or {}).items():
            offices[str(office_id)] = office or {}
    return offices


def parse_ct_ems(lookup: dict, electiondata: dict, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of CT EMS ``Lookupdata.json`` + ``Electiondata.json``."""
    offices = _offices(lookup)
    candidates = {str(key): value for key, value in (lookup.get("candidateIds") or {}).items()}
    parties = {str(key): value for key, value in (lookup.get("partyIds") or {}).items()}
    votes_by_office = electiondata.get("stateVotes") or {}
    reporting, total = _counting(electiondata.get("election") or {})

    results: list[RaceResult] = []
    for office_id, office in offices.items():
        rows = votes_by_office.get(office_id) or []
        if not rows:
            continue
        tally: dict[str, dict] = {}
        for row in rows:
            for candidate_id, statistic in (row or {}).items():
                candidate = candidates.get(str(candidate_id)) or {}
                name = (candidate.get("NM") or "").strip()
                if not name:
                    continue
                party_id = str(candidate.get("P") or "")
                code = (parties.get(party_id) or {}).get("CD") or ""
                party = c.norm_party(_CT_PARTIES.get(code, code))
                votes = c.to_int((statistic or {}).get("V"))
                entry = tally.get(name)
                if entry is None:
                    tally[name] = {"votes": votes, "party": party, "best": votes}
                else:
                    entry["votes"] += votes
                    if votes > entry["best"]:  # the biggest party line names the party
                        entry["best"], entry["party"] = votes, party
        if not tally:
            continue
        cands = [
            CandidateResult(name=name, party=entry["party"], votes=entry["votes"]) for name, entry in tally.items()
        ]
        title = (office.get("NM") or "").strip() or None
        results.append(
            c.make_result(
                race_key(office),
                title or "",
                cands,
                fetched_at,
                source=CtEms.name,
                url=HUMAN,
                precincts_reporting=reporting,
                precincts_total=total,
            )
        )
    return results


def _counting(header: dict) -> tuple[int | None, int | None]:
    match = _PRECINCTS.search(str(header.get("PR") or ""))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


async def _get_json(client: httpx.AsyncClient, url: str) -> object:
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"CT EMS unreachable: {exc}") from exc
    if resp.status_code == 404:
        raise SourceUnavailable(f"CT EMS: {url} is 404")
    if resp.status_code >= 400:
        raise SourceUnavailable(f"CT EMS returned {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceUnavailable(f"CT EMS: {url} did not return JSON") from exc


@register
class CtEms(ResultsSource):
    name = "ct_ems"
    states = frozenset({"CT"})
    scrape = True  # the vendor's app data paths, not a documented public API
    enabled_by_default = False

    async def _results(self, client: httpx.AsyncClient) -> list[RaceResult]:
        date = c.election_date()
        elections = await _get_json(client, ELECTIONS)
        found = election_id(elections if isinstance(elections, list) else [], date)
        if not found:
            raise SourceUnavailable(f"CT EMS: no election named for {date}")
        base = f"{BASE}/ng-app/data/election/{found}"
        version = await _get_json(client, f"{base}/Version.json")
        number = str((version or {}).get("Version") or "").strip()
        if not number:
            raise SourceUnavailable(f"CT EMS: no Version for election {found}")
        lookup = await _get_json(client, f"{base}/{number}/Lookupdata.json")
        data = await _get_json(client, f"{base}/{number}/Electiondata.json")
        if not isinstance(lookup, dict) or not isinstance(data, dict):
            raise SourceUnavailable("CT EMS: unexpected payload shape")
        return parse_ct_ems(lookup, data, fetched_at=c.utcnow())

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ct_keys = [key for key in keys if key.state == "CT"]
        if not ct_keys:
            return []
        return c.select(await self._results(client), ct_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "CT":
            return []
        return [
            (race_result.key, race_result.title or str(race_result.key)) for race_result in await self._results(client)
        ]
