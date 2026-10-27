"""Tennessee — Shelby County election-night export (Enhanced Voting).

Tennessee's Secretary of State publishes statewide results through a GoVoteTN app; the results
page answers 403 to the bot User-Agent, so a county feed is the only route a plain HTTP client
has. The two other big counties are not usable either: Davidson draws from a PDF precinct
report and Knox (elections.knoxcounty.org) is a single-page app whose result feed is fetched
with a per-session token we could not reproduce (see docs/results/counties.md).

Shelby County (Memphis, the state's largest county) publishes a machine-readable export:

    https://app.enhancedvoting.com/cdn/results/shelby-county-tn/export-General11052024.json

The document shape and the candidate-name quirks are handled by ``_ev_county``; this module
only maps Shelby's contest names to Tennessee races. Shelby reports the whole county ballot:
President/Vice President, US Senate, the congressional districts the county sits in (8 and 9),
Tennessee Senate and House districts, and the Memphis-area municipal races.

Contest names repeat the municipality at the end ("Bartlett Alderman - Position 4 Bartlett",
"Collierville Mayor Collierville"), so a trailing repeat of the leading word is dropped before
the local race key is built.

Pin another county (or a new election slug) with TN_COUNTY_FEEDS:

    TN_COUNTY_FEEDS='[{"county":"Shelby","slug":"General11032026","date":"2026-11-03"}]'
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace

import httpx

from electionwatch.models import Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _ev_county
from ._county import merge

HUMAN = _ev_county.HUMAN

#: County -> Enhanced Voting jurisdiction slug.
JURISDICTIONS: dict[str, str] = {
    "Shelby": "shelby-county-tn",
}

#: date -> county -> export slug. Slug None means "not published yet".
ELECTIONS: dict[str, dict[str, str | None]] = {
    "2024-11-05": {"Shelby": "General11052024"},
    # Enhanced Voting names the export "General<MMDDYYYY>"; the 2026 general export
    # answered 404 on 2026-10-27 and is expected to appear on election night.
    "2026-11-03": {"Shelby": "General11032026"},
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "COMPTROLLER OF THE TREASURY": "TREAS",
    "TREASURER": "TREAS",
}

_SENATE = {"UNITED STATES SENATE", "US SENATE", "UNITED STATES SENATOR", "US SENATOR"}
_PRESIDENT = {
    "UNITED STATES PRESIDENT AND VICE PRESIDENT", "PRESIDENT AND VICE PRESIDENT",
    "PRESIDENT OF THE UNITED STATES", "US PRESIDENT",
}

_HOUSE = re.compile(
    r"^(?:US|UNITED STATES) HOUSE OF REPRESENTATIVES\s*(?:DISTRICT|DIST\.?|NO\.?)?\s*(\d+)$")
_STATE_SENATE = re.compile(
    r"^(?:TN|TENNESSEE|STATE) SENATE\s*(?:DISTRICT|DIST\.?|NO\.?)?\s*(\d+)$")
_STATE_HOUSE = re.compile(
    r"^(?:TN|TENNESSEE|STATE) HOUSE OF REPRESENTATIVES\s*"
    r"(?:DISTRICT|DIST\.?|NO\.?)?\s*(\d+)$")


def _norm(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()
    n = re.sub(r"\s+-\s+", " ", n)
    return re.sub(r"[,.]", "", n)


def _drop_trailing_municipality(n: str) -> str:
    """'BARTLETT ALDERMAN - POSITION 4 BARTLETT' -> 'BARTLETT ALDERMAN - POSITION 4'."""
    words = n.split()
    while len(words) > 2 and words[-1] == words[0]:
        words.pop()
    return " ".join(words)


def shared_key(name: str) -> RaceKey | None:
    """Key for a contest on every Tennessee ballot, or None for a purely local one."""
    n = _norm(name)
    if n in _PRESIDENT:
        return c.local_key("TN", "president-vice-president")
    if n in _SENATE:
        return RaceKey("TN", Level.SENATE, "SEN")
    m = _HOUSE.match(n)
    if m:
        return RaceKey("TN", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    if n in _STATEWIDE:
        return RaceKey("TN", Level.GOVERNOR, _STATEWIDE[n])
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey("TN", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _STATE_HOUSE.match(n)
    if m:
        return RaceKey("TN", Level.STATE, "HD", f"{int(m.group(1)):02d}")
    return None


def race_key(county_slug: str, name: str) -> RaceKey:
    """Every contest gets a key; unrecognised ones become TN LOCAL <county>-<race>."""
    shared = shared_key(name)
    if shared is not None:
        return shared
    return c.local_key("TN", f"{county_slug}-{_drop_trailing_municipality(_norm(name))}")


def slug_for(county: str) -> str:
    """'Shelby' -> 'shelby'; used to namespace that county's local races."""
    return re.sub(r"[^a-z0-9]+", "-", str(county or "").lower()).strip("-")


def parse_tn_county(payload, *, county: str, fetched_at) -> list[RaceResult]:
    """Pure parse of one Shelby County export."""
    slug = slug_for(county)
    return _ev_county.parse_items(
        payload,
        key_fn=lambda name: race_key(slug, name),
        source=TnCounties.name,
        url=HUMAN,
        fetched_at=fetched_at,
    )


def feeds(date: str) -> list[dict]:
    """Configured counties for a date, with their export slug (TN_COUNTY_FEEDS overrides)."""
    raw = os.environ.get("TN_COUNTY_FEEDS")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise SourceUnavailable("TN_COUNTY_FEEDS is not valid JSON") from exc
        if not isinstance(entries, list):
            raise SourceUnavailable("TN_COUNTY_FEEDS must be a JSON list")
        out = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("county") or not entry.get("slug"):
                raise SourceUnavailable("TN_COUNTY_FEEDS entries need county and slug")
            if entry.get("date") and entry["date"] != date:
                continue
            out.append({"county": str(entry["county"]), "slug": str(entry["slug"])})
        return out
    return [{"county": county, "slug": slug}
            for county, slug in ELECTIONS.get(date, {}).items() if slug]


@register
class TnCounties(ResultsSource):
    name = "tn_counties"
    states = frozenset({"TN"})
    scrape = False

    async def _all(self, client: httpx.AsyncClient, date: str) -> list[RaceResult]:
        collected: list[RaceResult] = []
        entries = feeds(date)
        errors: list[str] = []
        for entry in entries:
            county = entry["county"]
            jurisdiction = JURISDICTIONS.get(county, county.lower())
            url = _ev_county.export_url(jurisdiction, entry["slug"])
            try:
                payload = await c.get_json(client, url)
            except SourceUnavailable as exc:
                errors.append(f"{county}: {exc}")
                continue
            collected.extend(parse_tn_county(payload, county=county, fetched_at=c.utcnow()))
        if not collected:
            raise SourceUnavailable(
                "tn_counties: no Tennessee county export answered for " + date
                + (" (" + "; ".join(errors) + ")" if errors else ""))
        merged = merge(collected, source=self.name, url=HUMAN)
        if len({entry["county"] for entry in entries}) == 1:
            # `_county.merge` always pluralises; a single configured county should not
            # read "(partial: 1 counties)".
            merged = [replace(r, title=(r.title or "").replace(
                "(partial: 1 counties)", "(partial: 1 county)")) for r in merged]
        return merged

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        tn_keys = [k for k in keys if k.state == "TN"]
        if not tn_keys:
            return []
        return c.select(await self._all(client, c.election_date()), tn_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "TN":
            return []
        try:
            results = await self._all(client, c.election_date())
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key)) for r in results]
