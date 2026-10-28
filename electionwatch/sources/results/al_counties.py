"""Alabama — county election-night feeds (Clarity ENR), the only official machine-readable
Alabama results a plain HTTP client can read.

The Secretary of State's results app (www2.alabamavotes.gov/electionnight/...) serves an empty
shell for every election code (docs/results/states-2.md), so there is no statewide feed.
Madison County (Huntsville; about 8.5% of Alabama's 2022 US Senate vote) still runs Clarity ENR
and lists its elections on the vendor CDN, including the 2026 primary runoff:

    https://results.enr.clarityelections.com/AL/Madison/elections.json
    https://results.enr.clarityelections.com/AL/Madison/<EID>/current_ver.txt
    https://results.enr.clarityelections.com/AL/Madison/<EID>/<ver>/json/en/summary.json

Jefferson, Mobile, Baldwin and Shelby have CDN directories too, but their newest election is
from 2012; they are not listed. Results merge into "(partial: N counties)" results exactly like
`tx_counties` (shared `_county` helpers). The 2026 general id is discovered from
elections.json when the county lists it; pin it (or add a county) with AL_COUNTY_FEEDS:

    AL_COUNTY_FEEDS='[{"county":"Madison","election_id":"127999","date":"2026-11-03"}]'

Madison's contest names are upper case and the vendor truncates long ones
("UNITED STATES REPRESENTATIVE, 5TH CONGRESSIONAL DISTRI"), so the House pattern accepts a
truncated "DISTRICT". The "STRAIGHT PARTY VOTING" contest and the aggregated "WRITE-IN"
rows (an unnamed write-in tally, like the dropped write-in rows in `mn_sos`) are dropped.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

import httpx

from electionwatch.models import Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _county
from .clarity import parse_settings

HUMAN = "https://results.enr.clarityelections.com/AL/Madison/"

#: County name as it appears in the Clarity URL -> slug used for local race keys.
COUNTIES: dict[str, str] = {
    "Madison": "madison",
}

#: date -> county -> election id; anything missing is discovered from elections.json.
ELECTIONS: dict[str, dict[str, str]] = {
    # "2022 General Election", verified while writing the fixture.
    "2022-11-08": {"Madison": "116245"},
    "2026-11-03": {},
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "STATE AUDITOR": "AUD",
    "COMMISSIONER OF AGRICULTURE AND INDUSTRIES": "AGCOM",
}

_SENATE = {"UNITED STATES SENATOR", "US SENATOR", "U.S. SENATOR", "UNITED STATES SENATE"}
_HOUSE = re.compile(
    r"^(?:UNITED STATES|U\.S\.|US) REPRESENTATIVE,?\s*(\d+)(?:ST|ND|RD|TH)\s+CONGRESSIONAL"
    r"(?:\s+DISTR?I?C?T?)?$"
)
_HOUSE_ALT = re.compile(
    r"^(?:UNITED STATES|U\.S\.|US) REPRESENTATIVE,?\s*(?:CONGRESSIONAL\s+)?DISTRICT\s*(?:NO\.?\s*)?(\d+)$"
)
_STATE_SENATE = re.compile(r"^STATE SENATOR,?\s*DISTRICT\s*(?:NO\.?\s*)?(\d+)$")
_STATE_HOUSE = re.compile(r"^STATE REPRESENTATIVE,?\s*DISTRICT\s*(?:NO\.?\s*)?(\d+)$")
_SKIP = {"STRAIGHT PARTY VOTING", "STRAIGHT PARTY"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace(" ", " ").strip()).upper()


def shared_key(name: str) -> RaceKey | None:
    """Key for a federal/statewide/legislative contest, or None."""
    n = _norm(name)
    special = c.is_special(n)
    if n in _SENATE:
        return RaceKey("AL", Level.SENATE, "SEN", None, special)
    m = _HOUSE.match(n) or _HOUSE_ALT.match(n)
    if m:
        return RaceKey("AL", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    if n in _STATEWIDE:
        return RaceKey("AL", Level.GOVERNOR, _STATEWIDE[n], None, special)
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey("AL", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = _STATE_HOUSE.match(n)
    if m:
        return RaceKey("AL", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    return None


def race_key(county_slug: str, name: str) -> RaceKey | None:
    """Every contest gets a key except straight-party voting; the rest are AL LOCAL."""
    n = _norm(name)
    if n in _SKIP:
        return None
    return shared_key(n) or c.local_key("AL", f"{county_slug}-{n}")


def parse_al_county(summary, settings=None, *, county: str, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Alabama county summary.json."""
    slug = COUNTIES.get(county, county.lower())
    results = _county.parse_contests(
        summary,
        key_fn=lambda name: race_key(slug, name),
        source=AlCounties.name,
        url=f"https://results.enr.clarityelections.com/AL/{county}/",
        fetched_at=fetched_at,
        updated=parse_settings(settings),
    )
    out = []
    for r in results:
        cands = [x for x in r.candidates if _norm(x.name) not in ("WRITE-IN", "WRITE IN")]
        if not cands:
            continue
        if len(cands) != len(r.candidates):
            r = c.make_result(
                r.key,
                r.title or "",
                cands,
                r.fetched_at,
                source=r.source,
                url=r.url,
                total_votes=sum(x.votes for x in cands),
                precincts_reporting=r.precincts_reporting,
                precincts_total=r.precincts_total,
                source_updated_at=r.source_updated_at,
            )
        out.append(r)
    return out


def counties(date: str) -> list[dict]:
    """Configured counties for a date (AL_COUNTY_FEEDS overrides)."""
    override = _county.county_feeds_from_env("AL_COUNTY_FEEDS")
    pins = ELECTIONS.get(date, {})
    if override is None:
        return [{"county": name, "election_id": pins.get(name)} for name in COUNTIES]
    return [
        {"county": str(e["county"]), "election_id": e["election_id"]}
        for e in override
        if not e.get("date") or e["date"] == date
    ]


async def _one(client: httpx.AsyncClient, entry: dict, date: str, fetched_at: datetime) -> list[RaceResult]:
    county = entry["county"]
    eid = await _county.election_id(client, "AL", county, date, entry.get("election_id"))
    if not eid:
        raise SourceUnavailable(f"al_counties: {county} lists no election for {date}")
    summary, settings, _ = await _county.fetch_summary(client, "AL", county, eid)
    return parse_al_county(summary, settings, county=county, fetched_at=fetched_at)


def _singular(results: list[RaceResult]) -> list[RaceResult]:
    return [replace(r, title=(r.title or "").replace("(partial: 1 counties)", "(partial: 1 county)")) for r in results]


@register
class AlCounties(ResultsSource):
    name = "al_counties"
    states = frozenset({"AL"})
    scrape = False
    enabled_by_default = True

    async def _all(self, client: httpx.AsyncClient) -> list[RaceResult]:
        date = c.election_date()
        fetched_at = c.utcnow()
        collected: list[RaceResult] = []
        errors: list[str] = []
        for entry in counties(date):
            try:
                collected.extend(await _one(client, entry, date, fetched_at))
            except SourceUnavailable as exc:
                errors.append(str(exc))
        if not collected:
            raise SourceUnavailable(
                "al_counties: no Alabama county feed answered" + (f" ({'; '.join(errors)})" if errors else "")
            )
        return _singular(_county.merge(collected, source=self.name, url=HUMAN))

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        al = [k for k in keys if k.state == "AL"]
        if not al:
            return []
        return c.select(await self._all(client), al)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "AL":
            return []
        try:
            results = await self._all(client)
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key)) for r in results]
