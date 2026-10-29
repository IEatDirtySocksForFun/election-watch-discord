"""State race lists for the /races browser and race autocomplete.

Neither of them calls a data source. A background refresh (RaceLists.refresh, run every minute
by the bot) asks each results source that can list races for a state's races and stores the
answer in the database, one entry per (state, source). The browser and autocomplete read only
those entries (RaceLists.stored).

Which states a source is asked about, in this order:

  1. states someone opened in /races or typed in autocomplete since their list was stored
     (or that have no list yet), newest request first: that person is looking at it now;
  2. the other requested states (electionwatch/demand.py, kind "racelists", kept for a day)
     and the states of the races any server watches, oldest list first;
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

A state that fails backs off on its own (1, 2, 4, 8, then 15 minutes) and the source moves on
to the next state; only a source failing on every state it tries backs off as a whole. A
first answer of "no races" is not stored straight away: several adapters answer that when
their feed is down or not live yet, so it is asked again in 5 and then 15 minutes, and only
the third empty answer is stored (the state keeps showing "loading" until then).
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

CACHE = "racelist"  # store cache kind; ident "<state>|<source>"
KIND = "racelists"  # budget kind (ratelimit.FLOORS), demand kind and /refresh kind
STATES_PER_TICK = 3  # states listed per source per refresh
#: An empty answer does not replace a non-empty list younger than this: a feed that is briefly
#: down often answers "no races" rather than failing, and race lists don't vanish overnight.
KEEP_ON_EMPTY = timedelta(days=1)
READ_TIMEOUT = 2.0  # autocomplete must answer within Discord's 3 s deadline
FAILURES_PER_TICK = 2  # a source stops for this refresh after this many failed states
EMPTY_RETRIES = (timedelta(minutes=5), timedelta(minutes=15))  # before an empty answer is stored


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
    return json.dumps(
        [[key.state, key.level.value, key.office, key.district, key.special, label] for key, label in races]
    )


def load_list(text: str) -> list[tuple[RaceKey, str]]:
    races = []
    for row in json.loads(text):
        # One unreadable entry (written by another version) shouldn't hide the rest.
        try:
            state, level, office, district, special, label = row
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
    for code, name in sorted(ALL_JURISDICTIONS.items(), key=lambda jurisdiction: -len(jurisdiction[1])):
        name = name.casefold()
        if cleaned == name or cleaned.startswith(name + " "):
            return code
    # Abbreviations and misspellings: "Penn. 8", "N.C. sen", "pensylvania 08".
    from electionwatch.racekeys import _leading_state

    leading = _leading_state(text.strip())
    return leading[0] if leading is not None else None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class RaceLists:
    """Refreshes the stored race lists (background) and reads them (commands)."""

    def __init__(
        self,
        store,
        sources: dict[str, list],
        http,
        clock: Callable[[], datetime] | None = None,
        *,
        rates=None,
        demand=None,
        default_races: Iterable = (),
        timeout: float = 30.0,
    ) -> None:
        self.store = store
        self.sources = sources
        self.http = http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rates = rates
        self.demand = demand
        self.default_races = list(default_races)
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._backoff: dict[str, tuple[int, datetime]] = {}  # source -> (failures, skip until)
        self._failures: dict[str, int] = {}  # budget entry (source and state) -> failures in a row
        self._empties: dict[str, int] = {}  # budget entry -> empty answers with nothing stored
        self._hold: dict[str, datetime] = {}  # budget entry -> not before
        self._wanted: dict[str, datetime] = {}  # state -> when someone last looked at it
        self._stale_before: datetime | None = None  # set by force()

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
        if state not in ALL_JURISDICTIONS:
            return
        self._wanted[state] = self._now()
        if self.demand is not None:
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
            for source, races in listed
            if not getattr(source, "fallback", False)
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
        for held in (self._backoff, self._failures, self._empties, self._hold):
            held.clear()
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
            asked = self.demand.items(KIND) if self.demand is not None else list(self._wanted)
            demanded = [state for state in asked if state in ALL_JURISDICTIONS]
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

    def _order(
        self, source, name: str, stored: dict[str, datetime], demanded: list[str], watched: list[str]
    ) -> list[str]:
        covered = set(source.states)

        def at(state: str) -> datetime | None:
            return stored.get(cache_ident(state, name))

        def age(state: str) -> tuple:
            when = at(state)
            return (0, 0.0, state) if when is None else (1, when.timestamp(), state)

        # Someone is looking at these now: newest request first.
        waiting = [
            state
            for state in demanded
            if state in covered and state in self._wanted and (at(state) is None or self._wanted[state] > at(state))
        ]
        waiting.sort(key=lambda state: self._wanted[state], reverse=True)
        known = {state for state in [*demanded, *watched] if state in covered} - set(waiting)
        rest = covered - known - set(waiting)
        return waiting + sorted(known, key=lambda state: age(state)[1:]) + sorted(rest, key=age)

    async def _refresh_source(
        self, source, now: datetime, stored: dict[str, datetime], demanded: list[str], watched: list[str]
    ) -> int:
        name = getattr(source, "name", repr(source))
        failures, until = self._backoff.get(name, (0, None))
        if until is not None and now < until:
            return 0
        done = failed = 0
        for state in self._order(source, name, stored, demanded, watched):
            if done + failed >= STATES_PER_TICK or failed >= FAILURES_PER_TICK:
                break
            entry = budget_name(name, state)
            at = stored.get(cache_ident(state, name))
            if not self._stale(at, entry, now):
                continue
            hold = self._hold.get(entry)
            if hold is not None and now < hold:
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
                # This state backs off on its own (1, 2, 4, 8, then 15 minutes) and the source
                # goes on with the next one: one jurisdiction the source can't answer must not
                # keep every other state unlisted. The stored list stays as it was.
                failed += 1
                count = self._failures[entry] = self._failures.get(entry, 0) + 1
                self._retry(entry, now, timedelta(minutes=min(2 ** (count - 1), 15)))
                logger.warning("Could not list %s races from %s: %s", state, name, type(exc).__name__)
                continue
            self._failures.pop(entry, None)
            self._hold.pop(entry, None)
            done += 1
            if not races and at is None:
                # Nothing stored yet and "no races": often a feed that is down or not live yet
                # (some adapters answer [] instead of failing). Ask again soon; store it only
                # when it keeps saying so.
                count = self._empties[entry] = self._empties.get(entry, 0) + 1
                if count <= len(EMPTY_RETRIES):
                    self._retry(entry, now, EMPTY_RETRIES[count - 1])
                    continue
            if races:
                self._empties.pop(entry, None)
            if not races and at is not None and now - at < KEEP_ON_EMPTY and await self._has_races(state, name):
                logger.info("%s listed no %s races; keeping the stored list", name, state)
                continue
            try:
                await self.store.put_cached(CACHE, cache_ident(state, name), text, now)
            except Exception:
                logger.exception("Could not store the %s race list from %s", state, name)
                return done
            self._empties.pop(entry, None)
        if failed and not done:
            # Every state it tried failed: the host is probably down. Back off the whole
            # source like the poller does, so it isn't asked about two states every minute.
            failures += 1
            self._backoff[name] = (failures, now + timedelta(minutes=min(2 ** (failures - 1), 15)))
        elif done:
            self._backoff.pop(name, None)
        return done

    def _retry(self, entry: str, now: datetime, wait: timedelta) -> None:
        """Try this (source, state) again after `wait`, not a whole refresh interval."""
        self._hold[entry] = now + wait
        if self.rates is not None:
            self.rates.retry_in(entry, wait.total_seconds())

    async def _has_races(self, state: str, name: str) -> bool:
        try:
            hit = await self.store.get_cached(CACHE, cache_ident(state, name))
            return bool(hit and load_list(hit[1]))
        except Exception:
            return False
