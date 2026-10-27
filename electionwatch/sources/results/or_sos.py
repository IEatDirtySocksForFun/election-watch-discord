"""Oregon — Secretary of State election-night results (orresultswebservices).

Oregon publishes results through the "Results Ajax" service behind
results.oregonvotes.gov. The service is a plain JSON GET, no key, no cookies, no JS, and
it is served from the state's Azure CDN endpoint:

    https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData
        ?type=FED&category=SW&raceID=0&osn=0&county=0&party=0

`type` selects a group of contests, `category=SW` asks for statewide (not per-county)
totals, and raceID/osn/county/party = 0 mean "everything in that group":

    SWPAR    statewide partisan offices (Governor, Secretary of State, ...)
    FED      US Senate + US House
    HOUSE    state house          SENATE  state senate         MEASURE  statewide measures

One row per candidate and contest area:

    {"RaceID": 300038702, "RaceName": "Governor", "AreaNum": "Statewide Partisan",
     "OfficeSeqNo": "115010008", "PartyCode": "NON", "PartyName": "Republican, Libertarian",
     "calcCandidate": "Christine Drazan", "calcCandidateVotes": 0,
     "calcCandidatePercentage": 0, "TotalPrecincts": 1318, "PrecinctsReporting": 0,
     "PrecinctsPartial": 0, "IsReported": false, "Winner": false, "VoteFor": 1}

Notes that matter for parsing:

* Every party's candidates are in one race; `Winner` is the state's own flag.
* `PartyName` carries Oregon's fusion nominations ("Democrat, Working Families",
  "Republican, Libertarian"); the first name is the party that puts the candidate on the
  ballot, so that is the party recorded.
* `PartyCode` is the *contest* code, not the candidate's (it reads "NON" even in a partisan
  race), so it is not used.
* Write-in rows have an empty `PartyName` and are kept (they count toward the total).
* The service answers only for the election the state is currently publishing; the payload
  carries no election date, so the tracked date comes from configuration.
* `results.oregonvotes.gov` itself redirects to a not-found page out of election season;
  the CDN service above is the data source the site's own front end calls.
"""
from __future__ import annotations

import re

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

SERVICE = "https://orresultswebservices.azureedge.us/ResultsAjax.svc/GetMapData"
URL = SERVICE + "?type={}&category=SW&raceID=0&osn=0&county=0&party=0"
HUMAN = "https://results.oregonvotes.gov/"
TYPES = ("SWPAR", "FED", "HOUSE", "SENATE", "MEASURE")

_ORDINAL_DISTRICT = re.compile(r"(\d{1,3})(?:ST|ND|RD|TH)\s+DISTRICT", re.I)
_MEASURE = re.compile(r"^MEASURE\s+0*(\d{1,4})\b", re.I)

# Oregon's party names (the fusion lists are split before lookup).
_OR_PARTIES = {
    "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "LIBERTARIAN": "LIB",
    "PACIFIC GREEN": "GRN", "GREEN": "GRN",
    "CONSTITUTION": "CON",
    "WORKING FAMILIES": "WFP",
    "PROGRESSIVE": "PRO",
    "INDEPENDENT": "IND",
    "AMERICAN INDEPENDENT": "AIP",
    "NONPARTISAN": "NP", "NON-PARTISAN": "NP",
    "NO PARTY AFFILIATION": "NPA",
    "REFORM": "REF", "PATRIOT": "PAT", "SOCIALIST": "SOC",
    "US TAXPAYERS": "UST", "WE THE PEOPLE": "WTP",
}

_STATEWIDE = {
    "SECRETARY OF STATE": "SOS",
    "ATTORNEY GENERAL": "AG",
    "STATE TREASURER": "TREAS",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "COMMISSIONER OF THE BUREAU OF LABOR AND INDUSTRIES": "BOLI",
}


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip())


def _party(value: str | None) -> str | None:
    name = _norm(value).split(",")[0].strip()
    if not name:
        return None
    if name.upper() in _OR_PARTIES:
        return _OR_PARTIES[name.upper()]
    return c.norm_party(name)


def _district(*values: str | None) -> str | None:
    for value in values:
        text = _norm(value)
        if not text:
            continue
        m = _ORDINAL_DISTRICT.search(text)
        if m:
            return f"{int(m.group(1)):02d}"
        d = c.district_from(text)
        if d:
            return d
    return None


def race_key(race_name: str, area_num: str | None = None) -> RaceKey:
    """Map an Oregon contest name (+ its AreaNum, e.g. 'HD 01') to a RaceKey."""
    n = _norm(race_name).upper()
    special = c.is_special(n)
    if n in ("US SENATOR", "US SENATE", "UNITED STATES SENATOR"):
        return RaceKey("OR", Level.SENATE, "SEN", None, special)
    if n.startswith(("US REPRESENTATIVE", "US HOUSE", "CONGRESSIONAL")):
        return RaceKey("OR", Level.HOUSE, "HOUSE",
                       _district(area_num, race_name) or "00", special)
    if n.startswith("GOVERNOR"):
        return RaceKey("OR", Level.GOVERNOR, "GOV", None, special)
    for prefix, code in _STATEWIDE.items():
        if n.startswith(prefix):
            return RaceKey("OR", Level.GOVERNOR, code, None, special)
    if n.startswith(("STATE REPRESENTATIVE", "STATE HOUSE")):
        return RaceKey("OR", Level.STATE, "HD", _district(area_num, race_name) or "00", special)
    if n.startswith(("STATE SENATOR", "STATE SENATE")):
        return RaceKey("OR", Level.STATE, "SD", _district(area_num, race_name) or "00", special)
    m = _MEASURE.match(n)
    if m:
        return RaceKey("OR", Level.LOCAL, "LOCAL", f"measure-{int(m.group(1))}")
    return c.local_key("OR", n)


def parse_or(payload, *, fetched_at) -> list[RaceResult]:
    """Pure parse of one GetMapData payload ({"d": [row, ...]})."""
    rows = payload.get("d") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise SourceUnavailable("OR: unexpected payload, no 'd' list")
    groups: dict[int, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict) or not _norm(row.get("RaceName")):
            continue
        groups.setdefault(c.to_int(row.get("RaceID")), []).append(row)
    out: list[RaceResult] = []
    for race_id, race_rows in groups.items():
        first = race_rows[0]
        race_name = _norm(first.get("RaceName"))
        key = race_key(race_name, first.get("AreaNum"))
        cands: list[CandidateResult] = []
        for row in race_rows:
            name = _norm(row.get("calcCandidate"))
            if not name:
                continue
            cands.append(CandidateResult(
                name=name,
                party=_party(row.get("PartyName")),
                votes=c.to_int(row.get("calcCandidateVotes")),
                winner=bool(row.get("Winner")),
            ))
        if not cands:
            continue
        reporting = c.to_int(first.get("PrecinctsReporting")) + c.to_int(first.get("PrecinctsPartial"))
        total = c.to_int(first.get("TotalPrecincts")) or None
        out.append(c.make_result(
            key, race_name, cands, fetched_at,
            source=OrSos.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=reporting,
            precincts_total=total,
        ))
    return out


@register
class OrSos(ResultsSource):
    name = "or_sos"
    states = frozenset({"OR"})
    scrape = False  # the state's own JSON service
    enabled_by_default = True

    async def _pages(self, client: httpx.AsyncClient) -> list[list[RaceResult]]:
        pages: list[list[RaceResult]] = []
        errors: list[str] = []
        for kind in TYPES:
            try:
                payload = await c.get_json(client, URL.format(kind))
            except SourceUnavailable as exc:
                errors.append(str(exc))
                continue
            pages.append(parse_or(payload, fetched_at=c.utcnow()))
        if not pages:
            raise SourceUnavailable("OR: " + "; ".join(errors[:2] or ["no feed"]))
        return pages

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "OR" for k in keys):
            return []
        results = [r for page in await self._pages(client) for r in page]
        return c.select(results, [k for k in keys if k.state == "OR"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "OR":
            return []
        seen: dict[str, tuple[RaceKey, str]] = {}
        for page in await self._pages(client):
            for r in page:
                seen.setdefault(str(r.key), (r.key, r.title or str(r.key)))
        return list(seen.values())
