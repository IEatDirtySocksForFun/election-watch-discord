"""civicAPI (https://civicapi.org): free election results and race calls for every state.

civicAPI publishes a documented JSON API that needs no key and is "free for all personal,
non-commercial, or commercial purposes", with attribution required for non-personal
projects: every result links to the race's civicAPI page and the post footer names the
source. It aggregates official returns down to county level, so it covers the states that
have no reachable official feed (AL, KY, MA, ME, NH, NV, OK, WY, MS, TN, the county-only
states, Texas behind its challenge page).

Lives in ``calls/`` with the other national fallbacks so it registers after the official
state adapters: the poller prefers the first complete answer, so a working official feed
still wins. civicAPI publishes test data before election night ("test_data": true, with
fake winners); those races are dropped, never posted.

    GET /api/v2/race/search?country=US&province=TX&startDate=D&endDate=D&limit=N&offset=M
    GET /api/v2/race/{id}      (full race: test_data, called_at, last_updated, candidates)
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import CandidateResult, Level, RaceCall, RaceKey, RaceResult
from electionwatch.racekeys import ALL_JURISDICTIONS, STATEWIDE, RaceKeyError, parse
from electionwatch.sources.base import CallSource, ResultsSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import normalize_party, parse_iso_ts

API = "https://civicapi.org/api/v2"
PAGE = "https://civicapi.org/results/elections/{id}"
INDEX_TTL = 1800      # race ids never change: re-list a state every 30 minutes
RACE_TTL = 55         # one fetch per race per poll tick (the poller ticks every 60 s)
PAGE_SIZE = 1000
MAX_PAGES = 10
CONCURRENCY = 4

_OFFICES = {title: code for code, title in STATEWIDE.items()}
_OFFICES["Superintendent of Public Instruction"] = "SUPT"
_LEGISLATURE = re.compile(r"[A-Z]{2}-(HD|SD)-(.+)")


def race_key(row: dict) -> RaceKey | None:
    """Map a civicAPI race row to a RaceKey; None for rows the bot does not track
    (chamber-control summaries, other countries)."""
    state = (row.get("province") or "").upper()
    if row.get("country") != "US" or state not in ALL_JURISDICTIONS:
        return None
    kind = row.get("type") or ""
    district = row.get("district")
    name = row.get("election_name") or ""
    try:
        if kind == "US Senate":
            return RaceKey(state, Level.SENATE, "SEN", None, "special" in name.lower())
        if kind == "House of Representatives":
            return parse(district) if district else None
        if kind in _OFFICES and row.get("election_type") == "Statewide" and not district:
            return RaceKey(state, Level.GOVERNOR, _OFFICES[kind])
        if kind in ("State House", "State Senate"):
            match = _LEGISLATURE.fullmatch(district or "")
            # No district = the chamber-control summary (seats per party), not a race.
            return parse(f"{state} {match.group(1)}-{match.group(2)}") if match else None
    except (RaceKeyError, ValueError):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return RaceKey(state, Level.LOCAL, "LOCAL", slug) if slug else None


def parse_race(race: dict, race_id: int, key: RaceKey, fetched_at: datetime) -> RaceResult | None:
    """A full civicAPI race -> RaceResult; None for test data."""
    if race.get("test_data"):
        return None
    opens = race.get("polls_open")
    if opens and fetched_at < parse_iso_ts(opens) and any(c.get("votes") for c in race.get("candidates") or []):
        return None   # votes before the polls open can only be leftover test numbers
    candidates = [
        CandidateResult(
            name=str(c.get("name") or "").strip(),
            party=normalize_party(c.get("party")),
            votes=int(c.get("votes") or 0),
            pct=c.get("percent"),
            incumbent=bool(c.get("incumbent")),
            winner=False,   # civicAPI's winner flag is its own call: published by the call source
        )
        for c in race.get("candidates") or []
        if c.get("name")
    ]
    if not candidates:
        return None
    return RaceResult(
        key=key,
        candidates=candidates,
        source="civicapi",
        fetched_at=fetched_at,
        source_updated_at=parse_iso_ts(race.get("last_updated")) if race.get("last_updated") else None,
        expected_vote_pct=race.get("percent_reporting"),
        total_votes=sum(c.votes for c in candidates),
        registered_voters=race.get("registered_voters"),
        url=PAGE.format(id=race_id),
        title=race.get("election_name"),
    ).finalize()


def parse_call(race: dict, race_id: int, key: RaceKey) -> RaceCall | None:
    """civicAPI's call for a race (winner flag + called_at); None for test data or no call."""
    if race.get("test_data") or not race.get("called_at"):
        return None
    winners = [c for c in race.get("candidates") or [] if c.get("winner")]
    if len(winners) != 1:   # multi-seat races and ties are not single-winner calls
        return None
    return RaceCall(
        key=key,
        outlet="CIVICAPI",
        winner=str(winners[0].get("name")),
        party=normalize_party(winners[0].get("party")),
        called_at=parse_iso_ts(race["called_at"]),
        url=PAGE.format(id=race_id),
    )


class _CivicAPI:
    """Shared, cached access for the results and call sources (one request per race per tick)."""

    def __init__(self) -> None:
        self.index: dict[tuple[str, str], tuple[float, dict[RaceKey, tuple[int, str]]]] = {}
        self.races: dict[int, tuple[float, dict]] = {}

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None):
        try:
            response = await client.get(f"{API}/{path}", params=params, timeout=20)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"civicapi unreachable: {type(exc).__name__}") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise SourceUnavailable(f"civicapi returned {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise SourceUnavailable("civicapi returned invalid JSON") from exc

    async def state_races(self, state: str, client: httpx.AsyncClient) -> dict[RaceKey, tuple[int, str]]:
        """{key: (race id, election name)} for the election date, all pages."""
        date = election_date()
        ident = (state, date)
        cached = self.index.get(ident)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        found: dict[RaceKey, tuple[int, str]] = {}
        for page in range(MAX_PAGES):
            payload = await self._get(client, "race/search", {
                "country": "US", "province": state, "startDate": date, "endDate": date,
                "limit": PAGE_SIZE, "offset": page * PAGE_SIZE,
            }) or {}
            rows = payload.get("races") or []
            for row in rows:
                key = race_key(row)
                if key is not None and row.get("id") is not None:
                    found.setdefault(key, (int(row["id"]), row.get("election_name") or str(key)))
            if len(rows) < PAGE_SIZE:
                break
        self.index[ident] = (time.monotonic() + INDEX_TTL, found)
        return found

    async def race(self, race_id: int, client: httpx.AsyncClient) -> dict | None:
        cached = self.races.get(race_id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        payload = await self._get(client, f"race/{race_id}")
        self.races[race_id] = (time.monotonic() + RACE_TTL, payload)
        return payload

    async def races_for(self, keys: list[RaceKey], client: httpx.AsyncClient):
        """Yield (key, race id, race payload) for the requested keys civicAPI publishes."""
        wanted: list[tuple[RaceKey, int]] = []
        for state in sorted({key.state for key in keys}):
            index = await self.state_races(state, client)
            wanted += [(key, index[key][0]) for key in keys if key.state == state and key in index]
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(race_id: int):
            async with gate:
                return await self.race(race_id, client)

        payloads = await asyncio.gather(*(one(race_id) for _, race_id in wanted))
        return [(key, race_id, race) for (key, race_id), race in zip(wanted, payloads) if race]


_SHARED = _CivicAPI()


@register
class CivicAPIResultsSource(ResultsSource):
    name = "civicapi"
    fallback = True   # ranked after the official state feeds (see __main__.build_sources)
    states = frozenset(ALL_JURISDICTIONS)

    def __init__(self, api: _CivicAPI | None = None) -> None:
        self.api = api or _SHARED

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        now = datetime.now(timezone.utc)
        results = []
        for key, race_id, race in await self.api.races_for(keys, client):
            result = parse_race(race, race_id, key, now)
            if result is not None:
                results.append(result)
        return results

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        return [(key, title) for key, (_, title) in (await self.api.state_races(state, client)).items()]


@register
class CivicAPICallSource(CallSource):
    name = "civicapi"
    outlet: ClassVar[str] = "CIVICAPI"

    def __init__(self, api: _CivicAPI | None = None) -> None:
        self.api = api or _SHARED

    async def fetch_calls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceCall]:
        calls = []
        for key, race_id, race in await self.api.races_for(keys, client):
            call = parse_call(race, race_id, key)
            if call is not None:
                calls.append(call)
        return calls
