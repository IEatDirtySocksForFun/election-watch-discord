"""NBC News race-call adapter.

NBC's results pages are rendered from the public "firecracker" feed at
``nbcnews.com/firecracker/api/v2/national-results/<year>-elections/<office>-results``.
It is the site's own JSON (no documented API), so this counts as a scraper:
``scrape=True`` and disabled by default.

The feed exposes one entry per state (Senate/Governor) or per district (House)
under ``mapData``; ``tooltip.candidates[].isWinner`` is NBC's projection.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.sources.results._common import election_date
from electionwatch.models import Level, RaceCall, RaceKey
from electionwatch.sources.base import CallSource, SourceUnavailable, register

from ._common import (
    district_key,
    normalize_party,
    parse_http_date,
    state_from_name,
)

_OFFICE_SLUGS: dict[Level, str] = {
    Level.SENATE: "senate",
    Level.GOVERNOR: "governor",
    Level.HOUSE: "house",
}
_OFFICE_CODES = {"S": Level.SENATE, "G": Level.GOVERNOR, "H": Level.HOUSE}


@register
class NBCSource(CallSource):
    name = "nbc"
    outlet = "NBC"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False

    BASE = "https://www.nbcnews.com/firecracker/api/v2/national-results"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    async def fetch_calls(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[RaceCall]:
        offices = {_OFFICE_SLUGS[k.level] for k in keys if k.level in _OFFICE_SLUGS}
        if not offices:
            return []

        calls: list[RaceCall] = []
        for office in sorted(offices):
            url = f"{self.BASE}/{self.YEAR}-elections/{office}-results"
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"nbc unreachable: {exc}") from exc
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"nbc returned {resp.status_code} for {office}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"nbc bad JSON: {exc}") from exc
            try:
                calls.extend(parse_nbc_results(payload))
            except Exception:
                continue

        requested = set(keys)
        return [c for c in calls if c.key in requested]


def parse_nbc_results(payload: dict) -> list[RaceCall]:
    """Parse an NBC firecracker results payload into RaceCall rows."""
    observed = parse_http_date(payload.get("lastModified")) or datetime.now(timezone.utc)
    year = str(payload.get("electionSeason") or "")
    calls: list[RaceCall] = []
    for geo, entry in (payload.get("mapData") or {}).items():
        try:
            tooltip = entry.get("tooltip") or {}
            winner = next(
                (c for c in tooltip.get("candidates") or [] if c.get("isWinner")), None
            )
            if winner is None:
                continue
            key = _tooltip_key(geo, tooltip)
            if key is None:
                continue
            name = winner.get("name") or " ".join(
                p for p in (winner.get("firstName"), winner.get("lastName")) if p
            )
            calls.append(
                RaceCall(
                    key=key,
                    outlet="NBC",
                    winner=name.strip(),
                    party=normalize_party(winner.get("party")),
                    called_at=observed,
                    url=_results_url(key, year),
                )
            )
        except Exception:
            # Never raise on one bad race.
            continue
    return calls


def _tooltip_key(geo: str, tooltip: dict) -> RaceKey | None:
    level = _OFFICE_CODES.get((tooltip.get("officeCode") or "").upper())
    if level is None:
        return None
    if level is Level.HOUSE:
        m = re.fullmatch(r"(.+?)\s+District\s+(\d{1,2})", tooltip.get("raceName") or "")
        if not m:
            return None
        state = state_from_name(m.group(1))
        district = district_key(m.group(2))
        if state is None or district is None:
            return None
        return RaceKey(state, Level.HOUSE, "HOUSE", district)
    state = geo.upper() if len(geo) == 2 and geo.isalpha() else None
    if state is None:
        state = state_from_name(tooltip.get("raceName"))
    if state is None:
        return None
    if level is Level.SENATE:
        return RaceKey(state, Level.SENATE, "SEN")
    return RaceKey(state, Level.GOVERNOR, "GOV")


def _results_url(key: RaceKey, year: str) -> str | None:
    year = year or "2024"
    slug = _OFFICE_SLUGS.get(key.level)
    if slug is None:
        return None
    return (
        f"https://www.nbcnews.com/politics/{year}-elections/"
        f"{key.state.lower()}-{slug}-results"
    )
