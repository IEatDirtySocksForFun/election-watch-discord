"""State race lists for the /races browser and race autocomplete.

Neither of them calls a data source. A background refresh (RaceLists.refresh, run every minute
by the bot) asks each results source that can list races for a state's races and stores the
answer in the database, one entry per (state, source). The browser and autocomplete read only
those entries (RaceLists.stored).

Which states a source is asked about, in this order:

  1. states users opened in /races or typed in autocomplete (electionwatch/demand.py, kind
     "racelists"), so someone looking at a state that is still loading gets it first;
  2. the states of the races any server watches;
  3. every other state the source covers, never-listed states first, then the oldest list,
     so browsing works out of the box.

A list is fetched again once it is older than its refresh interval: the "racelists" floor in
electionwatch/ratelimit.py (1 hour; race lists rarely change), stretched by the host's budget
like every other source. Each (source, state) list is its own budget entry, named
"<source>:racelists:<state>", so its requests count against the host's hourly allowance and
the hourly check can hold it back. On top of that a refresh lists at most STATES_PER_TICK
states per source: a new install fills in a national source's 56 states over about twenty
minutes instead of sending them all at once, and a restart skips lists the database still has
fresh.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import ALL_JURISDICTIONS, parse

logger = logging.getLogger(__name__)

CACHE = "racelist"        # store cache kind; ident "<state>|<source>"
KIND = "racelists"        # budget kind (ratelimit.FLOORS), demand kind and /refresh kind
STATES_PER_TICK = 3       # states listed per source per refresh
#: An empty answer does not replace a non-empty list younger than this: a feed that is briefly
#: down often answers "no races" rather than failing, and race lists don't vanish overnight.
KEEP_ON_EMPTY = timedelta(days=1)
READ_TIMEOUT = 2.0        # autocomplete must answer within Discord's 3 s deadline


def budget_name(source: str, state: str) -> str:
    return f"{source}:{KIND}:{state}"


def cache_ident(state: str, source: str) -> str:
    return f"{state}|{source}"


def lists_races(source) -> bool:
    """Whether a results source can list races (the base class's list_races returns nothing)."""
    from electionwatch.sources.base import ResultsSource

    method = getattr(type(source), "list_races", None)
    return callable(method) and method is not ResultsSource.list_races


def dump_list(races: Iterable[tuple[RaceKey, str]]) -> str:
    """Store the key's fields, not its text: reading thousands of local races back must be
    cheap, and a slug that the key parser would spell differently survives the round trip."""
    return json.dumps([[key.state, key.level.value, key.office, key.district, key.special, label]
                       for key, label in races])


def load_list(text: str) -> list[tuple[RaceKey, str]]:
    races = []
    for item in json.loads(text):
        # One unreadable entry (written by another version) shouldn't hide the rest.
        try:
            state, level, office, district, special, label = item
            races.append((RaceKey(state, Level(level), office, district, bool(special)), label))
        except (TypeError, ValueError):
            continue
    return races


def typed_state(text: str) -> str | None:
    """The state someone is typing in a race field: "PA", "pa sen", "TX-07", "Texas gov"."""
    cleaned = " ".join(text.replace("-", " ").split()).casefold()
    if not cleaned:
        return None
    first = cleaned.split(" ", 1)[0].upper()
    if len(first) == 2 and first in ALL_JURISDICTIONS:
        return first
    for code, name in sorted(ALL_JURISDICTIONS.items(), key=lambda item: -len(item[1])):
        name = name.casefold()
        if cleaned == name or cleaned.startswith(name + " "):
            return code
    return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class RaceLists:
    """Refreshes the stored race lists (background) and reads them (commands)."""

    def __init__(self, store, sources: dict[str, list], http, clock: Callable[[], datetime] | None = None,
                 *, rates=None, demand=None, default_races: Iterable = (), timeout: float = 30.0) -> None:
        self.store = store
        self.sources = sources
        self.http = http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rates = rates
        self.demand = demand
        self.default_races = list(default_races)
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._backoff: dict[str, tuple[int, datetime]] = {}   # source -> (failures, skip until)
        self._stale_before: datetime | None = None             # set by force()

    def _now(self) -> datetime:
        return _utc(self.clock())

    def listers(self, state: str | None = None) -> list:
        """Available results sources that can list races (for `state`, if given)."""
        found = []
        for source in self.sources.get("results", []):
            if not lists_races(source) or (state is not None and state not in source.states):
                continue
            try:
                if source.available():
                    found.append(source)
            except Exception:
                logger.warning("Could not check availability of source %s", getattr(source, "name", "?"))
        return found

    # ------------------------------------------------------------------ commands
    def want(self, state: str) -> None:
        """Someone is looking at this state: list it before the states nobody looks at."""
        if self.demand is not None and state in ALL_JURISDICTIONS:
            self.demand.add(KIND, state)

    async def stored(self, state: str) -> tuple[dict[str, tuple[RaceKey, str]], bool]:
        """The stored races of a state as ({key text: (key, label)}, loading). `loading` is True
        when sources could list the state but none has stored a list yet."""
        sources = self.listers(state)

        async def one(source) -> list | None:
            name = getattr(source, "name", "?")
            try:
                hit = await self.store.get_cached(CACHE, cache_ident(state, name))
            except Exception:
                logger.exception("Could not read the stored %s race list from %s", state, name)
                return None
            if hit is None:
                return None
            try:
                return load_list(hit[1])
            except ValueError:
                logger.warning("Ignoring an unreadable stored %s race list from %s", state, name)
                return None

        answers = await asyncio.gather(*(one(source) for source in sources))
        listed = [(source, races) for source, races in zip(sources, answers) if races is not None]
        # Local race keys are slugs of each source's own contest name, so civicAPI's and a county
        # feed's key for the same race differ: where an official feed lists local races, the
        # fallbacks' local races would be duplicates under other names.
        official_local = any(
            key.level is Level.LOCAL
            for source, races in listed if not getattr(source, "fallback", False)
            for key, _ in races
        )
        found: dict[str, tuple[RaceKey, str]] = {}
        for source, races in listed:
            for key, label in races:
                if official_local and key.level is Level.LOCAL and getattr(source, "fallback", False):
                    continue
                found.setdefault(str(key), (key, label))
        return found, bool(sources) and not listed

    # ------------------------------------------------------------------ background refresh
    def force(self) -> None:
        """/refresh: treat every stored list as stale (still a few states per source a minute,
        and still within each host's hourly allowance)."""
        self._stale_before = self._now()
        self._backoff.clear()
        if self.rates is not None:
            self.rates.force({KIND})

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def refresh(self) -> int:
        """List the states that are due; returns how many lists were fetched. A refresh still
        running (the minute loop and /refresh) makes this one a no-op."""
        if self._lock.locked():
            return 0
        async with self._lock:
            sources = self.listers()
            if not sources:
                return 0
            now = self._now()
            stored = {ident: _utc(at) for ident, at in (await self.store.cached_idents(CACHE)).items()}
            demanded = [state for state in self.demand.items(KIND) if state in ALL_JURISDICTIONS] \
                if self.demand is not None else []
            watched = await self._watched_states()
            counts = await asyncio.gather(
                *(self._refresh_source(source, now, stored, demanded, watched) for source in sources),
                return_exceptions=True,
            )
            for source, count in zip(sources, counts):
                if isinstance(count, BaseException):
                    logger.error("Race lists from %s failed", getattr(source, "name", "?"), exc_info=count)
            return sum(count for count in counts if isinstance(count, int))

    async def _watched_states(self) -> list[str]:
        """States of every server's watched races (the default races for servers without a list)."""
        states: list[str] = []
        guilds = await self.store.all_guilds()
        texts: list[str] = []
        if not guilds or any(not guild.important for guild in guilds):
            texts += [str(race.key) for race in self.default_races]
        for guild in guilds:
            texts += list(guild.important)
        for text in texts:
            try:
                state = parse(text).state
            except ValueError:
                continue
            if state not in states:
                states.append(state)
        return states

    def _max_age(self, name: str) -> float:
        if self.rates is not None:
            return self.rates.interval(name, KIND)
        from electionwatch.ratelimit import FLOORS
        return FLOORS[KIND]

    def _stale(self, at: datetime | None, name: str, now: datetime) -> bool:
        if at is None:
            return True
        if self._stale_before is not None and at <= self._stale_before:
            return True
        return (now - at).total_seconds() >= self._max_age(name)

    async def _refresh_source(self, source, now: datetime, stored: dict[str, datetime],
                              demanded: list[str], watched: list[str]) -> int:
        name = getattr(source, "name", repr(source))
        failures, until = self._backoff.get(name, (0, None))
        if until is not None and now < until:
            return 0
        covered = set(source.states)
        first = [state for state in demanded if state in covered]
        first += [state for state in watched if state in covered and state not in first]

        def age(state: str) -> tuple:
            at = stored.get(cache_ident(state, name))
            return (0, 0.0, state) if at is None else (1, at.timestamp(), state)

        order = first + sorted(covered - set(first), key=age)
        done = 0
        for state in order:
            if done >= STATES_PER_TICK:
                break
            entry = budget_name(name, state)
            at = stored.get(cache_ident(state, name))
            if not self._stale(at, entry, now):
                continue
            # due() and track() with no await in between: see RateBook.track.
            if self.rates is not None and not self.rates.due(entry, KIND):
                continue
            tracking = self.rates.track(entry, KIND) if self.rates is not None else nullcontext()
            try:
                with tracking:
                    races = await asyncio.wait_for(source.list_races(state, self.http), self.timeout)
                text = dump_list(races)
            except Exception as exc:
                # Back off like the poller does (1, 2, 4, 8, then 15 minutes), then try this
                # state again; the stored list stays as it was.
                failures += 1
                wait = timedelta(minutes=min(2 ** (failures - 1), 15))
                self._backoff[name] = (failures, now + wait)
                if self.rates is not None:
                    # Not a whole interval: a state still loading should not wait an hour.
                    self.rates.retry_in(entry, wait.total_seconds())
                logger.warning("Could not list %s races from %s: %s", state, name, type(exc).__name__)
                return done
            if failures:
                self._backoff.pop(name, None)
                failures = 0
            done += 1
            if not races and at is not None and now - at < KEEP_ON_EMPTY and await self._has_races(state, name):
                logger.info("%s listed no %s races; keeping the stored list", name, state)
                continue
            try:
                await self.store.put_cached(CACHE, cache_ident(state, name), text, now)
            except Exception:
                logger.exception("Could not store the %s race list from %s", state, name)
                return done
        return done

    async def _has_races(self, state: str, name: str) -> bool:
        try:
            hit = await self.store.get_cached(CACHE, cache_ident(state, name))
            return bool(hit and load_list(hit[1]))
        except Exception:
            return False
