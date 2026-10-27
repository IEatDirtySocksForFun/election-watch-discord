"""Fox News race-call adapter.

Fox publishes per-state result feeds under
``feeds-elections.foxnews.com/archive/politics/elections/<year>/3/<year>_Generals/``.
The office segment is TitleCase (``Senate``/``Governor``/``House``) and the
level file is ``county_level_results`` for statewide races and
``state_level_results`` for House districts. These are scraper endpoints with
no documented API, so ``scrape=True`` and disabled by default.

Senate/Governor payloads carry ``results[].candidate.isWinner``. House payloads
carry ``races[].district.districtNumber`` and ``races[].results[].isWinner``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.sources.results._common import election_date
from electionwatch.models import Level, RaceCall, RaceKey
from electionwatch.racekeys import STATES
from electionwatch.sources.base import CallSource, SourceUnavailable, register

from ._common import district_key, normalize_party, parse_iso_ts

_OFFICE_NAMES: dict[Level, str] = {
    Level.SENATE: "Senate",
    Level.GOVERNOR: "Governor",
    Level.HOUSE: "House",
}


@register
class FOXSource(CallSource):
    name = "fox"
    outlet = "FOX"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False

    BASE = "https://feeds-elections.foxnews.com/archive/politics/elections"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    def _url(self, key: RaceKey) -> str | None:
        office = _OFFICE_NAMES.get(key.level)
        if office is None:
            return None
        level = "state_level_results" if key.level is Level.HOUSE else "county_level_results"
        return (
            f"{self.BASE}/{self.YEAR}/3/{self.YEAR}_Generals/"
            f"{office}/{key.state}/{level}/file.json"
        )

    async def fetch_calls(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[RaceCall]:
        calls: list[RaceCall] = []
        seen: set[str] = set()
        for key in keys:
            url = self._url(key)
            if url is None or url in seen:
                continue
            seen.add(url)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"fox unreachable: {exc}") from exc
            # Fox returns 403 (S3 AccessDenied) for races it has no file for,
            # e.g. a state without a governor race that year.
            if resp.status_code in (403, 404):
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"fox returned {resp.status_code}")
            try:
                payload = resp.json()
            except Exception:
                continue
            calls.extend(parse_fox_state(payload, key.state, key.level, self.YEAR))

        requested = set(keys)
        return [c for c in calls if c.key in requested]


def parse_fox_state(
    payload: dict, state: str, level: Level, year: str | int = ""
) -> list[RaceCall]:
    """Parse one Fox state-level feed into RaceCall rows."""
    called_at = parse_iso_ts(payload.get("last_updated")) or datetime.now(timezone.utc)
    calls: list[RaceCall] = []
    if level is Level.HOUSE:
        for race in payload.get("races") or []:
            try:
                district = district_key((race.get("district") or {}).get("districtNumber"))
                if district is None:
                    continue
                winner = _fox_winner(race.get("results") or [])
                if winner is None:
                    continue
                key = RaceKey(state, Level.HOUSE, "HOUSE", district)
                calls.append(
                    RaceCall(
                        key=key,
                        outlet="FOX",
                        winner=winner[0],
                        party=winner[1],
                        called_at=called_at,
                        url=_results_url(key, year),
                    )
                )
            except Exception:
                continue
        return calls

    winner = _fox_winner(payload.get("results") or [])
    if winner is None:
        return calls
    key = (
        RaceKey(state, Level.SENATE, "SEN")
        if level is Level.SENATE
        else RaceKey(state, Level.GOVERNOR, "GOV")
    )
    calls.append(
        RaceCall(
            key=key,
            outlet="FOX",
            winner=winner[0],
            party=winner[1],
            called_at=called_at,
            url=_results_url(key, year),
        )
    )
    return calls


def _fox_winner(results: list) -> tuple[str, str | None] | None:
    for entry in results:
        # Senate/Governor nest isWinner in candidate; House puts it alongside.
        candidate = entry.get("candidate") or {}
        if entry.get("isWinner") or candidate.get("isWinner"):
            name = " ".join(
                p for p in (candidate.get("firstName"), candidate.get("lastName")) if p
            )
            party = candidate.get("partyCode") or entry.get("partyCode")
            if not party:
                party = candidate.get("partyName") or entry.get("partyName")
            return name.strip(), normalize_party(party)
    return None


def _results_url(key: RaceKey, year: str | int) -> str:
    state = STATES.get(key.state, key.state).lower().replace(" ", "-")
    return (
        f"https://www.foxnews.com/elections/{year or '2024'}/general-results/{state}"
    )
