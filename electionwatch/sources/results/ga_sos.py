"""Georgia — Secretary of State results export (results.sos.ga.gov).

Georgia publishes one JSON export per general election under a CDN-style path:

    https://results.sos.ga.gov/cdn/results/Georgia/export-<YYYY>NovGen.json

e.g. export-2024NovGen.json (33 MB) for 2024-11-05. The 2026 general export was not
published yet on 2026-10-27 (404); the slug follows the 2020/2022/2024 pattern.

Shape (trimmed in tests/fixtures/ga_sos/):

    {"electionDate": "...", "electionName": "...",
     "results": {"ballotItems": [
        {"type": "State", "id": "10000", "name": "US Senate (Perdue)",
         "contestType": "Candidate", "precinctsReporting": null,
         "ballotOptions": [
            {"name": "David A. Perdue (I) (Rep)", "politicalParty": "REP",
             "voteCount": 2462617, "groupResults": [...], "precinctResults": null}, ...]}]},
     "localResults": [...]}

Candidate names carry both the incumbent marker and a party suffix, e.g.
"David A. Perdue (I) (Rep)" or "Jon Ossoff (Dem)". Party also arrives as
`politicalParty`, so the suffix is stripped from the display name.

Every ballot item in the export is type "State" (the export has no county/municipal
races); `localResults` is the county-by-county breakdown and is not parsed here.
Georgia publishes no winner flag and no precinct counts in this file.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://results.sos.ga.gov/cdn/results/Georgia"
HUMAN = "https://results.sos.ga.gov/"

#: date -> export slug.  None = guess from the date (YYYYNovGen).
ELECTIONS: dict[str, str | None] = {
    "2020-11-03": "2020NovGen",
    "2022-11-08": "2022NovGen",
    "2024-11-05": "2024NovGen",
    "2026-11-03": "2026NovGen",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE SCHOOL SUPERINTENDENT": "SUPT",
    "COMMISSIONER OF AGRICULTURE": "AGCOM",
    "COMMISSIONER OF INSURANCE": "INSCOM",
    "COMMISSIONER OF LABOR": "LABCOM",
}

_PARTY_SUFFIX = re.compile(
    r"\s*\((?:REP|DEM|LIB|GRN|GRE|IND|CON|CST|GOP|NPA|REF|WRI|WRITE-?IN)\)\s*$", re.I)


def _english(name: str) -> str:
    """'State Senate Dist 5/Senador Estatal Dist 5' -> 'State Senate Dist 5'."""
    return re.sub(r"\s+", " ", str(name or "").split("/")[0]).strip()


def _norm(name: str) -> str:
    # 2024+ exports write "US House of Representatives - District 1"; drop the dash.
    return re.sub(r"\s+", " ", re.sub(r"\s+-\s+", " ", _english(name))).upper()


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    if n in ("PRESIDENT OF THE UNITED STATES", "PRESIDENT OF THE US", "US PRESIDENT"):
        return c.local_key("GA", "president-of-the-united-states")
    if n.startswith("US SENATE"):
        return RaceKey("GA", Level.SENATE, "SEN", None, special)
    m = re.match(r"^US HOUSE(?: OF REPRESENTATIVES)?\s*[-]?\s*(?:DISTRICT|DIST)\s*[-]?\s*(\d+)$", n)
    if m:
        return RaceKey("GA", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    if n in _STATEWIDE:
        return RaceKey("GA", Level.GOVERNOR, _STATEWIDE[n], None, special)
    m = re.match(r"^STATE SENATE\s*[-]?\s*(?:DISTRICT|DIST)?\s*[-]?\s*(\d+)$", n)
    if m:
        return RaceKey("GA", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^STATE HOUSE(?: OF REPRESENTATIVES)?\s*[-]?\s*(?:DISTRICT|DIST)\s*[-]?\s*(\d+)$", n)
    if m:
        return RaceKey("GA", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    return c.local_key("GA", n)


def _clean_name(raw: str) -> tuple[str, bool]:
    name = str(raw or "").strip()
    # Party suffix and incumbent marker can appear in either order.
    for _ in range(2):
        stripped = _PARTY_SUFFIX.sub("", name).strip()
        stripped, incumbent = c.strip_incumbent(stripped)
        if incumbent:
            return stripped, True
        if stripped == name:
            break
        name = stripped
    return name, False


def parse_ga(payload, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Georgia export document."""
    if not isinstance(payload, dict):
        return []
    items = (payload.get("results") or {}).get("ballotItems") or []
    out: list[RaceResult] = []
    seen: set[str] = set()
    for item in items:
        title = _english(item.get("name"))
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        cands = []
        for option in item.get("ballotOptions") or []:
            name, incumbent = _clean_name(option.get("name"))
            if not name:
                continue
            cands.append(CandidateResult(
                name=name,
                party=c.norm_party(option.get("politicalParty")),
                votes=c.to_int(option.get("voteCount")),
                incumbent=incumbent,
                winner=False,  # GA publishes no winner flag in the export
            ))
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=GaSos.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=c.to_int(item.get("precinctsReporting")) or None,
            precincts_total=c.to_int(item.get("precinctsParticipating")) or None,
        ))
    return out


def export_url(date: str) -> str:
    slug = ELECTIONS.get(date)
    if slug is None:
        slug = f"{date[:4]}NovGen"
    return f"{BASE}/export-{slug}.json"


@register
class GaSos(ResultsSource):
    name = "ga_sos"
    states = frozenset({"GA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ga_keys = [k for k in keys if k.state == "GA"]
        if not ga_keys:
            return []
        url = export_url(c.election_date())
        try:
            payload = await c.get_json(client, url)
        except SourceUnavailable as exc:
            raise SourceUnavailable(
                f"GA: {url} is not published yet or moved ({exc}); Georgia posts the "
                f"general-election export on election night"
            ) from exc
        return c.select(parse_ga(payload, fetched_at=c.utcnow()), ga_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "GA":
            return []
        try:
            payload = await c.get_json(client, export_url(c.election_date()))
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key))
                for r in parse_ga(payload, fetched_at=c.utcnow())]
