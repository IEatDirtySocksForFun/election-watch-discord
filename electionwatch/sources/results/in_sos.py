"""Indiana — Secretary of State / Indianavoters ENR JSON files.

The ENR site is a static Angular app over a folder of JSON documents:

    https://enr.indianavoters.in.gov/archive/2024General/data/settings.json
    https://enr.indianavoters.in.gov/archive/2024General/data/statewideElectionsC_A.json
    https://enr.indianavoters.in.gov/archive/2024General/data/OffCatC_1005_A.json

`settings.json` carries ``VersionType`` ("A"), the suffix every data file uses.
``statewideElectionsC_<v>.json`` lists the office categories ("Federal", "State",
"Local" headings, each with OFFICECATEGORYID). One ``OffCatC_<id>_<v>.json`` per
category then holds ``StatewideSummary.Race`` — a dict for a single-race category
(President, Governor) or a list for district categories (US House, state House):

    {"OFFICE_TITLE": "United States Representative, First District",
     "NumofSeats": "1",
     "Candidates": {"Candidate": [{"NAME_ON_BALLOT": "Frank J. Mrvan",
                                  "PARTY": "D", "TOTAL": 172467,
                                  "isWinner": "T"}]}}

The same file also repeats every county under ``OfficeCategory.Regions``; only the
statewide summary is used here. Indiana does not publish precinct counts in these
files, so ``precincts_*`` stay empty.

Election folders follow ``<year><General|Primary>``; the live site serves the
current election under ``/archive/`` as well, but a brand-new election can appear
at the root path first, so both are tried.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://enr.indianavoters.in.gov"
HUMAN = "https://enr.indianavoters.in.gov/"

#: date -> ENR folder.  2026 general is expected under the same <year>General name.
ELECTIONS: dict[str, str] = {
    "2024-11-05": "2024General",
    "2026-05-05": "2026Primary",
    "2026-11-03": "2026General",
}

#: Office categories worth polling: federal, statewide executive, state legislature.
_WANTED = (
    "PRESIDENTIAL",
    "US SENATOR",
    "UNITED STATES SENATOR",
    "US REPRESENTATIVE",
    "UNITED STATES REPRESENTATIVE",
    "GOVERNOR",
    "LIEUTENANT GOVERNOR",
    "ATTORNEY GENERAL",
    "SECRETARY OF STATE",
    "STATE SENATOR",
    "STATE REPRESENTATIVE",
)

_STATEWIDE = {
    "GOVERNOR & LT. GOVERNOR": "GOV",
    "GOVERNOR AND LT. GOVERNOR": "GOV",
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "STATE AUDITOR": "AUD",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
}

_ORDINALS = {
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "FOURTH": 4,
    "FIFTH": 5,
    "SIXTH": 6,
    "SEVENTH": 7,
    "EIGHTH": 8,
    "NINTH": 9,
}


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def race_key(title: str) -> RaceKey:
    """Map an Indiana OFFICE_TITLE to a RaceKey."""
    office_name = _upper(title)
    special = c.is_special(office_name)
    if office_name.startswith("UNITED STATES SENATOR") or office_name.startswith("US SENATOR"):
        return RaceKey("IN", Level.SENATE, "SEN", None, special)
    if office_name.startswith("PRESIDENTIAL ELECTORS") or office_name.startswith("PRESIDENT"):
        return c.local_key("IN", "us-president")
    if office_name.startswith("UNITED STATES REPRESENTATIVE") or office_name.startswith("US REPRESENTATIVE"):
        return RaceKey("IN", Level.HOUSE, "HOUSE", _house_district(office_name) or "AL", special)
    if office_name in _STATEWIDE:
        return RaceKey("IN", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    match = re.match(r"^STATE SENATOR,\s*DISTRICT\s*0*(\d{1,2})$", office_name)
    if match:
        return RaceKey("IN", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE REPRESENTATIVE,\s*DISTRICT\s*0*(\d{1,3})$", office_name)
    if match:
        return RaceKey("IN", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
    return c.local_key("IN", office_name)


def _house_district(office_name: str) -> str | None:
    match = re.search(r"DISTRICT\s*0*(\d{1,2})", office_name)
    if match:
        return f"{int(match.group(1)):02d}"
    for word, num in _ORDINALS.items():
        if re.search(rf"\b{word}\b", office_name):
            return f"{num:02d}"
    return None


def _as_list(value) -> list:
    """Indiana's JSON uses a bare object instead of a one-element list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse_category(payload: dict, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Pure parse of one OffCatC_<id>_<version>.json document."""
    root = (payload or {}).get("Root") or {}
    races = _as_list(((root.get("StatewideSummary") or {}).get("Race")))
    updated = c.parse_updated_at(re.sub(r"\.\d+$", "", str(root.get("WriteTime") or "")) or None)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for race in races:
        if not isinstance(race, dict):
            continue
        title = (race.get("OFFICE_TITLE") or "").strip()
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        cands = []
        for cand in _as_list((race.get("Candidates") or {}).get("Candidate")):
            if not isinstance(cand, dict):
                continue
            name = (cand.get("NAME_ON_BALLOT") or "").strip()
            if not name:
                continue
            cands.append(
                CandidateResult(
                    name=name,
                    party=c.norm_party(cand.get("PARTY") or cand.get("PARTY_ABBREV")),
                    votes=c.to_int(cand.get("TOTAL") if cand.get("TOTAL") is not None else cand.get("TOTAL_VOTES")),
                    winner=str(cand.get("isWinner") or "").strip().upper() in ("T", "1", "TRUE", "Y"),
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=InSos.name,
                url=url,
                total_votes=sum(candidate.votes for candidate in cands),
                source_updated_at=updated,
            )
        )
    return results


def categories(payload: dict) -> list[tuple[str, str]]:
    """(OFFICECATEGORYID, name) for the categories this adapter cares about."""
    root = (payload or {}).get("Root") or {}
    heads = root.get("List") or []
    if isinstance(heads, dict):
        heads = [heads]
    found: list[tuple[str, str]] = []
    for heading in heads:
        if not isinstance(heading, dict):
            continue
        items = heading.get("Items")
        items = items.get("Item") if isinstance(items, dict) else items
        for entry in _as_list(items):
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("OFFICECATEGORYID") or "").strip()
            name = (entry.get("OFFICE_CATEGORY_NAME") or "").strip()
            if cid and any(name.upper().startswith(prefix) for prefix in _WANTED):
                found.append((cid, name))
    return found


async def _load(client: httpx.AsyncClient, date: str) -> tuple[str, dict, dict]:
    """Locate the data folder; returns (data_base, settings, category index)."""
    folder = ELECTIONS.get(date)
    if not folder:
        raise SourceUnavailable(f"IN: no ENR folder configured for {date}; add '<year>General' to ELECTIONS")
    last: SourceUnavailable | None = None
    for prefix in (f"{BASE}/archive/{folder}", f"{BASE}/{folder}"):
        try:
            settings = await c.get_json(client, f"{prefix}/data/settings.json")
            index = await c.get_json(client, f"{prefix}/data/statewideElectionsC_{_version(settings)}.json")
        except SourceUnavailable as exc:
            last = exc
            continue
        if isinstance(settings, dict) and isinstance(index, dict):
            return f"{prefix}/data", settings, index  # type: ignore[return-value]
    raise SourceUnavailable(f"IN: ENR data for {date} is not published ({last})")


def _version(settings) -> str:
    root = settings.get("Root") if isinstance(settings, dict) else None
    version = str((root or {}).get("VersionType") or "").strip()
    if not version:
        raise SourceUnavailable("IN: settings.json has no VersionType")
    return version


async def _all_races(client: httpx.AsyncClient, date: str, fetched_at: datetime) -> list[RaceResult]:
    data_base, settings, index = await _load(client, date)
    version = _version(settings)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for cid, _name in categories(index):
        url = f"{data_base}/OffCatC_{cid}_{version}.json"
        payload = await c.get_json(client, url)
        for result in parse_category(payload, fetched_at=fetched_at, url=url):
            if str(result.key) in seen:
                continue
            seen.add(str(result.key))
            results.append(result)
    return results


@register
class InSos(ResultsSource):
    name = "in_sos"
    states = frozenset({"IN"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        in_keys = [key for key in keys if key.state == "IN"]
        if not in_keys:
            return []
        return c.select(await _all_races(client, c.election_date(), c.utcnow()), in_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "IN":
            return []
        return [
            (result.key, result.title or str(result.key))
            for result in await _all_races(client, c.election_date(), c.utcnow())
        ]
