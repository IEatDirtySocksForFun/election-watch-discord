"""civicAPI early-vote data (https://civicapi.org/early-vote): ballots cast before Election Day.

civicAPI is keyless and free with attribution: every embed built from this data links the
civicapi.org early-vote page and names civicAPI in its footer. The endpoints are the ones the
civicapi.org web app calls (they are not in the API docs), verified 2026-10-27:

    GET /api/v2/early-vote/US/{category}
        national map: ``regions`` = {USPS code: {party: {votes, color}}}, ``statewide_total``,
        ``snapshot_date`` (a day, no time). One request covers every state.
    GET /api/v2/early-vote/{state}/{category}
        one state: ``regions`` = {county: {party: {votes, color}}}, same totals. 404 = no data.
    GET /api/v2/early-vote/{state}/capabilities
        ``categories`` = {category: {party, gender, race, ethnicity, age: bool}}. 404 = no data.
    GET /api/v2/early-vote/{state}/{category}/demographics?by=age_range|party|gender|race[&region=County]
        ``values`` = {bucket: votes}, ``total``.
    GET /api/v2/early-vote/historical/{year}/{state}/{category}?date=YYYY-MM-DD
        the same shape for a past election on a given day; 404 = none for that state/year.

Categories: ``requested`` (mail ballots requested), ``returned`` (mail ballots returned),
``inperson`` (in-person early votes) and ``grace`` (Illinois grace-period voting). Like the
civicapi.org page, "voted" means returned + in person; requests are not votes. States without
party registration report every ballot as "Unspecified". Data is updated about once a day. The
bot downloads every page the commands can show into MongoDB about once an hour (on civicAPI's
request budget, electionwatch/ratelimit.py), one request at a time at least a second apart;
past elections' pages are kept 12 hours. The commands read only the stored pages.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from electionwatch.racekeys import STATES
from electionwatch.sources.base import SourceUnavailable

API = "https://civicapi.org/api/v2/early-vote"
PAGE = "https://civicapi.org/early-vote"
USER_AGENT = "election-watch-discord/0.1 (+https://github.com/IEatDirtySocksForFun/election-watch-discord)"
TTL = 15 * 60  # early-vote numbers update about once a day
HISTORY_TTL = 12 * 60 * 60  # past elections never change
MIN_INTERVAL = 1.0  # seconds between requests: one at a time, never a burst
TIMEOUT = 20

CATEGORIES = ("returned", "inperson", "requested", "grace")
VOTED = ("returned", "inperson")  # what counts as "ballots cast", as on civicapi.org
CATEGORY_LABELS = {
    "returned": "Mail ballots returned",
    "inperson": "In-person early votes",
    "requested": "Mail ballots requested",
    "grace": "Grace-period votes",
}
UNSPECIFIED = "unspecified"
MAX_COMPARISON_DAYS = 60  # only compare during the early-vote season

_MISS = object()
CACHE_KIND = "earlyvote"  # store cache kind for downloaded pages


class NotCached(SourceUnavailable):
    """A command asked for a page the background refresh has not stored yet."""


def page_url(state: str | None = None) -> str:
    """The civicapi.org early-vote page (national map, or one state)."""
    return f"{PAGE}/{state.lower()}" if state else PAGE


def election_day(year: int) -> date:
    """US general election: the Tuesday after the first Monday in November."""
    first = date(year, 11, 1)
    monday = first + timedelta(days=(0 - first.weekday()) % 7)
    return monday + timedelta(days=1)


def comparison_date(snapshot: date, year: int) -> date | None:
    """The day in ``year`` the same number of days before its election (as civicapi.org compares)."""
    days_left = (election_day(snapshot.year) - snapshot).days
    if not 0 <= days_left <= MAX_COMPARISON_DAYS:
        return None
    return election_day(year) - timedelta(days=days_left)


# --------------------------------------------------------------------------- parsing
@dataclass
class Tally:
    """One category's statewide count. ``by_party`` leaves out "Unspecified" ballots."""

    category: str
    total: int
    by_party: dict[str, int] = field(default_factory=dict)
    label: str = ""


@dataclass
class Comparison:
    """Ballots cast (returned + in person) at the same point before a past election."""

    year: int
    on: date
    total: int
    by_party: dict[str, int] = field(default_factory=dict)
    labels: tuple[str, ...] = ()  # civicAPI's names for what each count includes


@dataclass
class EarlyVote:
    state: str
    tallies: dict[str, Tally]  # by category
    updated_at: date | None
    url: str
    comparison: Comparison | None = None
    counties: dict[str, int] = field(default_factory=dict)  # ballots cast per county
    county_parties: dict[str, dict[str, int]] = field(default_factory=dict)  # county -> party -> ballots
    county_methods: dict[str, dict[str, int]] = field(default_factory=dict)  # county -> category -> ballots
    age: dict[str, int] = field(default_factory=dict)  # ballots cast per age bracket
    age_categories: tuple[str, ...] = ()  # which categories the age numbers cover

    @property
    def name(self) -> str:
        return STATES.get(self.state, self.state)

    @property
    def total_ballots(self) -> int:
        return sum(self.tallies[category].total for category in VOTED if category in self.tallies)

    @property
    def by_party(self) -> dict[str, int]:
        return merge_parties(*(self.tallies[category].by_party for category in VOTED if category in self.tallies))

    @property
    def by_method(self) -> dict[str, int]:
        return {category: self.tallies[category].total for category in CATEGORIES if category in self.tallies}


@dataclass
class National:
    states: dict[str, dict[str, Tally]]  # USPS code -> category -> tally
    updated_at: date | None

    def total(self, state: str) -> int:
        tallies = self.states.get(state, {})
        return sum(tallies[category].total for category in VOTED if category in tallies)


def _int(value) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def merge_parties(*parts: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for part in parts:
        for party, votes in part.items():
            merged[party] = merged.get(party, 0) + votes
    return dict(sorted(merged.items(), key=lambda party_votes: (-party_votes[1], party_votes[0])))


def parse_breakdown(raw) -> tuple[int, dict[str, int]]:
    """``{"Democratic": {"votes": n, "color": ...}, ...}`` -> (all ballots, parties without Unspecified)."""
    if not isinstance(raw, dict):
        return 0, {}
    total = 0
    parties: dict[str, int] = {}
    for party, entry in raw.items():
        votes = _int(entry.get("votes") if isinstance(entry, dict) else entry)
        total += votes
        if str(party).strip().lower() != UNSPECIFIED and votes:
            parties[str(party)] = parties.get(str(party), 0) + votes
    return total, merge_parties(parties)


def parse_snapshot(payload: dict | None) -> date | None:
    try:
        return date.fromisoformat(str((payload or {}).get("snapshot_date"))[:10])
    except ValueError:
        return None


def parse_tally(payload: dict | None, category: str) -> Tally | None:
    """A state (or historical) category response -> its statewide tally; None without data."""
    if not isinstance(payload, dict) or "statewide_total" not in payload:
        return None
    total, parties = parse_breakdown(payload.get("statewide_total"))
    return Tally(category, total, parties, str(payload.get("election_name") or ""))


def parse_national(payloads: dict[str, dict | None]) -> National:
    """The ``US/{category}`` responses -> per-state tallies (states with no ballots dropped)."""
    states: dict[str, dict[str, Tally]] = {}
    dates = []
    for category, payload in payloads.items():
        if not isinstance(payload, dict):
            continue
        if (snapshot := parse_snapshot(payload)) is not None:
            dates.append(snapshot)
        regions = payload.get("regions")
        for code, raw in regions.items() if isinstance(regions, dict) else ():
            code = str(code).upper()
            if code not in STATES:
                continue
            total, parties = parse_breakdown(raw)
            if total:
                states.setdefault(code, {})[category] = Tally(category, total, parties)
    return National(dict(sorted(states.items())), max(dates) if dates else None)


def parse_counties(payload: dict | None) -> dict[str, int]:
    """A state category response -> {county: ballots}."""
    return {county: total for county, (total, _) in parse_county_breakdowns(payload).items()}


def parse_county_breakdowns(payload: dict | None) -> dict[str, tuple[int, dict[str, int]]]:
    """A state category response -> {county: (ballots, parties without Unspecified)}."""
    regions = (payload or {}).get("regions") if isinstance(payload, dict) else None
    if not isinstance(regions, dict):
        return {}
    return {str(name): parse_breakdown(raw) for name, raw in regions.items()}


def parse_demographics(payload: dict | None) -> dict[str, int]:
    """A demographics response -> {bucket: ballots} in the API's order."""
    values = (payload or {}).get("values") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        return {}
    return {str(label): _int(votes) for label, votes in values.items()}


def parse_capabilities(payload: dict | None) -> dict[str, dict[str, bool]]:
    """Capabilities -> {category: {dimension: provided}}; per-category flags win over ``provides``."""
    if not isinstance(payload, dict):
        return {}
    provides = payload.get("provides") if isinstance(payload.get("provides"), dict) else {}
    categories = payload.get("categories")
    if not isinstance(categories, dict):
        return {category: dict(provides) for category in CATEGORIES} if provides else {}
    return {
        str(category): {**provides, **(flags if isinstance(flags, dict) else {})}
        for category, flags in categories.items()
    }


# --------------------------------------------------------------------------- client
class EarlyVoteClient:
    """Cached, rate-limited access to civicAPI's early-vote endpoints.

    Raises SourceUnavailable on network, HTTP (other than 404) and JSON errors; a 404 or an
    ``{"error": ...}`` body means "no data" and is cached like any answer. Failures are not
    cached, so the next command retries.

    With a ``store``, every downloaded page is also saved there. The bot runs two clients:
    the refresher downloads (``refresh()``, on the request budget of ratelimit.py) and the
    commands' client has ``cache_only=True``: it reads saved pages and never calls civicAPI,
    raising NotCached for a page that is not saved yet.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        ttl: float = TTL,
        history_ttl: float = HISTORY_TTL,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        store=None,
        cache_only: bool = False,
    ) -> None:
        self.client = client
        self.store = store
        self.cache_only = cache_only and store is not None
        self.ttl = ttl
        self.history_ttl = history_ttl
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._lock = asyncio.Lock()
        self._last = -math.inf
        self._warming: asyncio.Task | None = None

    def _fresh(self, key: str):
        hit = self._cache.get(key)
        if hit is not None and hit[0] > self._clock():
            return hit[1]
        return _MISS

    async def _get(self, path: str, params: dict | None = None, *, ttl: float | None = None) -> dict | None:
        url = f"{API}/{path}"
        key = url + ("?" + urlencode(sorted(params.items())) if params else "")
        if self.cache_only:
            hit = await self.store.get_cached(CACHE_KIND, key)
            if hit is None:
                raise NotCached(key)
            return json.loads(hit[1])
        if (cached := self._fresh(key)) is not _MISS:
            return cached
        async with self._lock:
            if (cached := self._fresh(key)) is not _MISS:  # filled while we waited
                return cached
            payload = await self._download(url, params)
            self._cache[key] = (self._clock() + (self.ttl if ttl is None else ttl), payload)
        if self.store is not None:
            await self.store.put_cached(CACHE_KIND, key, json.dumps(payload), datetime.now(timezone.utc))
        return payload

    async def _download(self, url: str, params: dict | None) -> dict | None:
        """One request, spaced from the previous one; the caller holds the lock."""
        wait = self._last + self.min_interval - self._clock()
        if wait > 0:
            await self._sleep(wait)
        try:
            response = await self.client.get(
                url,
                params=params,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"civicapi early-vote unreachable: {type(exc).__name__}") from exc
        finally:
            self._last = self._clock()
        if response.status_code == 404:
            payload = None
        elif response.status_code >= 400:
            raise SourceUnavailable(f"civicapi early-vote HTTP {response.status_code}")
        else:
            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceUnavailable("civicapi early-vote sent invalid JSON") from exc
            if not isinstance(payload, dict):
                raise SourceUnavailable("civicapi early-vote sent an unexpected payload")
            if payload.get("error"):
                payload = None
        return payload

    async def refresh(self) -> int:
        """Download every page the commands can show (national totals, then each state with
        data: counties, age, the comparison with four years earlier) into the store. Pages of
        past elections stay cached for 12 hours. Returns the number of states refreshed."""
        self._cache = {url: payload for url, payload in self._cache.items() if "/historical/" in url}
        national = await self.national()
        for code in sorted(national.states):
            await self.state(code, counties=True, age=True)
        return len(national.states)

    async def national(self) -> National:
        """Every state's totals per category (four requests, shared by every command)."""
        payloads = {category: await self._get(f"US/{category}") for category in CATEGORIES}
        return parse_national(payloads)

    def cached_states(self) -> list[str] | None:
        """States with data from a fresh national answer, without any request; None if cold."""
        if self.cache_only:
            return None
        payloads = {}
        for category in CATEGORIES:
            cached = self._fresh(f"{API}/US/{category}")
            if cached is _MISS:
                return None
            payloads[category] = cached
        return list(parse_national(payloads).states)

    async def states_with_data(self, timeout: float) -> list[str] | None:
        """States with data, waiting at most ``timeout`` seconds (autocomplete has 3 s).

        A cold cache keeps filling in the background after the timeout, so the next
        keystroke gets the real list. None on timeout or when civicAPI is down."""
        if (states := self.cached_states()) is not None:
            return states
        if self._warming is None or self._warming.done():
            self._warming = asyncio.ensure_future(self.national())
        try:
            national = await asyncio.wait_for(asyncio.shield(self._warming), timeout)
        except (asyncio.TimeoutError, SourceUnavailable):
            return None
        return list(national.states)

    async def state(
        self,
        code: str,
        *,
        counties: bool = False,
        age: bool = False,
        compare: bool = True,
    ) -> EarlyVote | None:
        """One state's early vote; None when civicAPI has no early-vote data for it."""
        code = code.upper()
        national = await self.national()
        tallies = national.states.get(code)
        if not tallies:
            return None
        early_vote = EarlyVote(code, dict(tallies), national.updated_at, page_url(code))
        voted = [category for category in VOTED if category in tallies]
        if compare and national.updated_at is not None:
            early_vote.comparison = await self._comparison(code, voted, national.updated_at)
        if counties:
            # The same pages give each county's total, its party split and its mail / in-person
            # split: one request per category, whatever is shown.
            merged: dict[str, int] = {}
            for category in voted:
                breakdowns = parse_county_breakdowns(await self._get(f"{code}/{category}"))
                for county, (votes, parties) in breakdowns.items():
                    merged[county] = merged.get(county, 0) + votes
                    early_vote.county_methods.setdefault(county, {})[category] = votes
                    early_vote.county_parties[county] = merge_parties(
                        early_vote.county_parties.get(county, {}), parties
                    )
            early_vote.counties = dict(
                sorted(merged.items(), key=lambda county_votes: (-county_votes[1], county_votes[0]))
            )
        if age:
            caps = parse_capabilities(await self._get(f"{code}/capabilities"))
            covered = tuple(category for category in voted if caps.get(category, {}).get("age"))
            buckets: dict[str, int] = {}
            answered = []
            for category in covered:
                payload = await self._get(f"{code}/{category}/demographics", {"by": "age_range"})
                if values := parse_demographics(payload):
                    answered.append(category)
                for label, votes in values.items():
                    buckets[label] = buckets.get(label, 0) + votes
            early_vote.age = buckets
            early_vote.age_categories = tuple(answered)
        return early_vote

    async def _comparison(self, code: str, categories: list[str], snapshot: date) -> Comparison | None:
        year = snapshot.year - 4  # the same kind of election (midterm / presidential)
        match_day = comparison_date(snapshot, year)
        if match_day is None or not categories:
            return None
        tallies = []
        for category in categories:
            payload = await self._get(
                f"historical/{year}/{code}/{category}",
                {"date": match_day.isoformat()},
                ttl=self.history_ttl,
            )
            if (parsed := parse_tally(payload, category)) is not None and parsed.total:
                tallies.append(parsed)
        if not tallies:
            return None
        return Comparison(
            year,
            match_day,
            sum(tally.total for tally in tallies),
            merge_parties(*(tally.by_party for tally in tallies)),
            tuple(tally.label for tally in tallies if tally.label),
        )
