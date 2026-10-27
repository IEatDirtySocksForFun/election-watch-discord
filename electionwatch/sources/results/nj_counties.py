"""New Jersey — county election-night feeds (Clarity ENR).

The state Division of Elections publishes only per-county PDFs, and the counties themselves
run Clarity ENR under the shared CDN:

    https://results.enr.clarityelections.com/NJ/<County>/elections.json
    https://results.enr.clarityelections.com/NJ/<County>/<EID>/current_ver.txt
    https://results.enr.clarityelections.com/NJ/<County>/<EID>/<ver>/json/en/summary.json

Election ids are per county and per election, so they are discovered per county from
`elections.json` (which already lists "2026 GENERAL ELECTION" in Cumberland) and the
verified 2024 ids are pinned for replay/tests. Counties that do not publish the listing are
skipped until an id is pinned with NJ_COUNTY_FEEDS:

    NJ_COUNTY_FEEDS='[{"county":"Bergen","election_id":"123456","date":"2026-11-03"}]'

Contest names are the county's own: "United States Senator", "House of Representatives CD 6",
"Sheriff", "Board of County Commissioners", "Bridgeton Mayor", the school-board races and the
local referenda. Statewide races are summed across counties into one result flagged
" (partial: N counties)"; everything else is namespaced with the county slug, because every
county has its own Sheriff and its own school boards.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _county
from .clarity import parse_settings

HUMAN = "https://www.nj.gov/state/elections/election-results.shtml"

#: County name in the Clarity URL -> slug for local race keys. Camden and Bergen answer 404
#: on the CDN (they run their own installations), so they are not listed.
COUNTIES: dict[str, str] = {
    "Atlantic": "atlantic",
    "Burlington": "burlington",
    "Cumberland": "cumberland",
    "Essex": "essex",
    "Gloucester": "gloucester",
    "Hudson": "hudson",
    "Mercer": "mercer",
    "Middlesex": "middlesex",
    "Monmouth": "monmouth",
    "Morris": "morris",
    "Ocean": "ocean",
    "Passaic": "passaic",
    "Somerset": "somerset",
    "Union": "union",
}

#: date -> county -> election id (verified against the live feeds on 2026-10-27).
ELECTIONS: dict[str, dict[str, str]] = {
    "2024-11-05": {
        "Cumberland": "122757",
        "Essex": "122756",
        "Hudson": "122754",
        "Mercer": "122845",
        "Middlesex": "122847",
        "Passaic": "122594",
        "Somerset": "122833",
        "Union": "122834",
    },
    "2026-11-03": {"Cumberland": "127562"},
}

_SENATE = {
    "SENATOR", "SENATE", "SENATE RACE", "UNITED STATES SENATE", "UNITED STATES SENATOR",
    "US SENATE", "US SENATOR", "U.S. SENATE", "U.S. SENATOR",
}

_STATE_SENATE = re.compile(r"^(?:NJ )?STATE SENAT(?:E|OR),?\s*(?:DISTRICT|DIST\.?)\s*(\d+)")
_GENERAL_ASSEMBLY = re.compile(
    r"^(?:NJ )?(?:GENERAL ASSEMBLY|STATE ASSEMBLY|ASSEMBLY(?:MAN|WOMAN)?),?"
    r"\s*(?:DISTRICT|DIST\.?)\s*(\d+)")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def _house_district(name: str) -> str | None:
    """'... CD 6' / '3rd Congressional District' / '- 2ND CONGRESSIONAL' -> '06' / '03' / '02'."""
    m = re.search(r"\bCD\s*[-# ]?\s*(\d+)", name)
    if m:
        return f"{int(m.group(1)):02d}"
    return c.district_from(name)


def _is_president(name: str) -> bool:
    return "PRESIDENT" in name and ("VICE PRESIDENT" in name or "ELECTORS" in name)


def shared_key(name: str) -> RaceKey | None:
    """Key for a contest every county publishes, or None for a purely local one."""
    n = _norm(name)
    if n in _SENATE:
        return RaceKey("NJ", Level.SENATE, "SEN")
    if "HOUSE OF REPRESENTATIVES" in n or "CONGRESSIONAL DISTRICT" in n:
        district = _house_district(n)
        # "United States House of Representatives" with no district: the county spans several
        # districts, so the votes cannot be attributed - keep it county-local.
        return RaceKey("NJ", Level.HOUSE, "HOUSE", district) if district else None
    if n == "GOVERNOR" or n.startswith("GOVERNOR "):
        return RaceKey("NJ", Level.GOVERNOR, "GOV")
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey("NJ", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _GENERAL_ASSEMBLY.match(n)
    if m:
        return RaceKey("NJ", Level.STATE, "HD", f"{int(m.group(1)):02d}")
    if _is_president(n):
        return c.local_key("NJ", "president-vice-president")
    return None


def race_key(county_slug: str, name: str) -> RaceKey:
    """Every contest gets a key; unrecognised ones become NJ LOCAL <county>-<race>."""
    shared = shared_key(name)
    if shared is not None:
        return shared
    return c.local_key("NJ", f"{county_slug}-{_norm(name)}")


def parse_nj_county(
    summary,
    settings=None,
    *,
    county: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of one New Jersey county summary.json."""
    slug = COUNTIES.get(county, county.lower())
    updated = parse_settings(settings)
    return _county.parse_contests(
        summary,
        key_fn=lambda name: race_key(slug, name),
        source=NjCounties.name,
        url=HUMAN,
        fetched_at=fetched_at,
        updated=updated,
    )


def counties(date: str) -> list[dict]:
    """Configured counties for an election date, with optional pinned election ids."""
    override = _county.county_feeds_from_env("NJ_COUNTY_FEEDS")
    pins = ELECTIONS.get(date, {})
    configured = {str(k).lower(): v for k, v in COUNTIES.items()}
    if override is None:
        return [{"county": name, "slug": slug, "election_id": pins.get(name)}
                for name, slug in COUNTIES.items()]
    out = []
    for entry in override:
        county = str(entry["county"])
        if entry.get("date") and entry["date"] != date:
            continue
        out.append({
            "county": county,
            "slug": configured.get(county.lower(), county.lower()),
            "election_id": entry["election_id"],
        })
    return out


async def _one(
    client: httpx.AsyncClient,
    entry: dict,
    date: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    county = entry["county"]
    eid = await _county.election_id(client, "NJ", county, date, entry.get("election_id"))
    if not eid:
        return []
    summary, settings, _ = await _county.fetch_summary(client, "NJ", county, eid)
    return parse_nj_county(summary, settings, county=county, fetched_at=fetched_at)


@register
class NjCounties(ResultsSource):
    name = "nj_counties"
    states = frozenset({"NJ"})
    scrape = False
    enabled_by_default = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "NJ" for k in keys):
            return []
        date = c.election_date()
        fetched_at = c.utcnow()
        collected: list[RaceResult] = []
        entries = counties(date)
        failures = 0
        for entry in entries:
            try:
                collected.extend(await _one(client, entry, date, fetched_at))
            except SourceUnavailable:
                failures += 1  # one dead county must not hide the others
        if failures and failures == len(entries):
            raise SourceUnavailable("nj_counties: no New Jersey county feed answered")
        if not collected:
            raise SourceUnavailable(
                f"nj_counties: no county publishes an election for {date}; "
                f"pin ids with NJ_COUNTY_FEEDS")
        return c.select(_county.merge(collected, source=self.name, url=HUMAN), keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "NJ":
            return []
        date = c.election_date()
        fetched_at = c.utcnow()
        out: list[tuple[RaceKey, str]] = []
        seen: set[str] = set()
        for entry in counties(date):
            try:
                results = await _one(client, entry, date, fetched_at)
            except SourceUnavailable:
                continue
            for r in results:
                if str(r.key) not in seen:
                    seen.add(str(r.key))
                    out.append((r.key, r.title or str(r.key)))
        return out
