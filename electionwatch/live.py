"""Commands that go to the source themselves (EW_COMMAND_DATA=live).

By default (EW_COMMAND_DATA=db) commands only read what the background refresh stored in the
database. A bot owner who'd rather have commands fetch fresh numbers can set
EW_COMMAND_DATA=live: /bettingmarkets, /polls, /earlyvote and the /races browser then ask the source
when someone runs one, and save each answer to the database just like a refresh would.
It stays polite:

- An answer younger than EW_LIVE_MAX_AGE seconds (default 60) is reused, so a crowd asking
  about the same race shares one fetch, and people asking at the same moment wait for it.
- Every request counts against the same hourly allowance as the background refresh
  (electionwatch/ratelimit.py). When a site has no room left this hour, is backing off after
  failures, or is slow, the command answers from the database instead.
- Fetches to one source go one at a time, spaced by an even share of its site's allowance,
  and nobody waits in line longer than EW_LIVE_TIMEOUT seconds (default 10).
- Race autocomplete always reads the database: Discord gives it less than 3 seconds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MODES = ("db", "live")
EARLY_VOTE_BUDGET = "civicapi:earlyvote"  # the early-vote refresher's budget entry


def command_mode(environ: Mapping[str, str]) -> str:
    """ "db" (commands read the database) or "live" (commands may fetch); unknown values are "db"."""
    mode = (environ.get("EW_COMMAND_DATA", "") or "db").strip().lower()
    return mode if mode in MODES else "db"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _number(environ: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value >= 0 else default


class LiveFetch:
    """Fetches for commands in live mode, saved to the store the background refresh uses."""

    def __init__(
        self,
        store,
        sources: dict[str, list],
        http,
        rates=None,
        clock: Callable[[], datetime] | None = None,
        *,
        max_age: float = 60.0,
        timeout: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.store = store
        self.sources = sources
        self.http = http
        self.rates = rates
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_age = max_age
        self.timeout = timeout
        self._monotonic = monotonic
        self._sleep = sleep
        self._fetched: dict[str, datetime] = {}  # what was fetched -> when
        self._running: dict[str, asyncio.Task] = {}  # what is being fetched -> the fetch
        self._lines: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # budget entry -> one at a time
        self._next: dict[str, float] = {}  # budget entry -> earliest next fetch (monotonic)
        self._backoff: dict[str, tuple[int, float]] = {}  # budget entry -> (failures, skip until)

    @classmethod
    def from_env(cls, environ: Mapping[str, str], store, sources, http, rates=None, clock=None) -> "LiveFetch":
        return cls(
            store,
            sources,
            http,
            rates,
            clock,
            max_age=_number(environ, "EW_LIVE_MAX_AGE", 60.0),
            timeout=_number(environ, "EW_LIVE_TIMEOUT", 10.0) or 10.0,
        )

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _available(self, kind: str) -> list:
        found = []
        for source in self.sources.get(kind, []):
            try:
                if source.available():
                    found.append(source)
            except Exception:
                logger.warning("Could not check availability of %s source %s", kind, getattr(source, "name", "?"))
        return found

    def fresh(self, what: str, stored_at: datetime | None = None) -> bool:
        """Whether `what` was fetched (live, or stored by a refresh) within EW_LIVE_MAX_AGE."""
        times = [_utc(at) for at in (self._fetched.get(what), stored_at) if at is not None]
        return bool(times) and self._now() - max(times) < timedelta(seconds=self.max_age)

    async def _once(self, what: str, fetch: Callable[[], Awaitable[bool]]) -> None:
        """Run `fetch` unless the same fetch is already running; then wait for that one."""
        task = self._running.get(what)
        if task is None:
            task = asyncio.ensure_future(fetch())
            self._running[what] = task

            def done(finished: asyncio.Task) -> None:
                if self._running.get(what) is finished:
                    del self._running[what]
                if not finished.cancelled() and finished.exception() is None and finished.result():
                    self._fetched[what] = self._now()

            task.add_done_callback(done)
        try:
            await asyncio.shield(task)
        except Exception:
            logger.exception("Live fetch of %s failed", what)

    async def call(self, budget: str, make: Callable[[], Awaitable]):
        """One request to a source for a command, or None when it must answer from the
        database: no room left in the hour, backing off, too long a wait, or a failure."""
        failures, until = self._backoff.get(budget, (0, 0.0))
        if self._monotonic() < until or (self.rates is not None and not self.rates.has_room(budget)):
            return None
        line = self._lines[budget]
        try:
            await asyncio.wait_for(line.acquire(), self.timeout)
        except asyncio.TimeoutError:
            return None
        try:
            wait = self._next.get(budget, 0.0) - self._monotonic()
            if wait > self.timeout:
                return None
            if wait > 0:
                await self._sleep(wait)
            if self.rates is not None and not self.rates.has_room(budget):
                return None
            try:
                got = await asyncio.wait_for(make(), self.timeout)
            except Exception as exc:
                failures += 1
                self._backoff[budget] = (failures, self._monotonic() + 60 * min(2 ** (failures - 1), 15))
                logger.warning("Live fetch for %s failed: %s", budget, type(exc).__name__)
                return None
            finally:
                spacing = self.rates.spacing(budget) if self.rates is not None else 0.0
                self._next[budget] = self._monotonic() + spacing
            self._backoff.pop(budget, None)
            return got
        finally:
            line.release()

    # ------------------------------------------------------------------ odds
    async def odds(self, key) -> None:
        """Fresh odds for one race into the store (add_odds), unless fetched recently."""
        from electionwatch.poller import ODDS_CHECKED

        what = f"odds|{key}"
        if self.fresh(what):
            return
        # Markets don't flag special elections: ask for the plain key too (like the poller).
        asked = [key, replace(key, special=False)] if getattr(key, "special", False) else [key]

        async def fetch() -> bool:
            sources = self._available("odds")
            answers = await asyncio.gather(
                *(
                    self.call(f"{source.name}:odds", lambda source=source: source.fetch_odds(asked, self.http))
                    for source in sources
                )
            )
            now, priced, reached = self._now(), False, False
            for fetched in answers:
                if fetched is None:
                    continue
                reached = True
                for snapshot in _file_under(key, fetched):
                    try:
                        await self.store.add_odds(snapshot)
                        priced = True
                    except Exception:
                        logger.exception("Could not store odds for %s", key)
            if reached and not priced:
                await self.store.put_cached(ODDS_CHECKED, str(key), "", now)
            return reached

        await self._once(what, fetch)

    # ------------------------------------------------------------------ polls
    async def polls(self, key) -> None:
        """Fresh polls for one race from each poll source into the store's polls cache."""
        from electionwatch.poller import POLLS_CACHE, dump_polls, polls_ident

        async def one(source) -> None:
            ident = polls_ident(source.name, key)
            what = f"polls|{ident}"
            try:
                hit = await self.store.get_cached(POLLS_CACHE, ident)
            except Exception:
                hit = None
            if self.fresh(what, hit[0] if hit else None):
                return

            async def fetch() -> bool:
                polls = await self.call(f"{source.name}:polls", lambda: source.fetch_polls([key], self.http))
                if polls is None:
                    return False
                mine = [poll for poll in polls if str(poll.key) == str(key)]
                await self.store.put_cached(POLLS_CACHE, ident, dump_polls(mine), self._now())
                return True

            await self._once(what, fetch)

        await asyncio.gather(*(one(source) for source in self._available("polls")))

    # ------------------------------------------------------------------ results
    async def results(self, key) -> None:
        """A fresh result for one race into the store: official feeds first, the fallbacks
        only when no official feed answered completely (the poller's order)."""
        from electionwatch.poller import best_result, is_partial

        what = f"results|{key}"
        try:
            stored = await self.store.latest_result(key)
        except Exception:
            stored = None
        if self.fresh(what, getattr(stored, "fetched_at", None) if stored else None):
            return

        async def ask(sources: list) -> tuple[list, bool]:
            answers = await asyncio.gather(
                *(
                    self.call(f"{source.name}:results", lambda source=source: source.fetch([key], self.http))
                    for source in sources
                )
            )
            found = [record for fetched in answers if fetched for record in fetched if str(record.key) == str(key)]
            return found, any(fetched is not None for fetched in answers)

        async def fetch() -> bool:
            covering = [source for source in self._available("results") if key.state in source.states]
            primary = [source for source in covering if not getattr(source, "fallback", False)]
            fallback = [source for source in covering if getattr(source, "fallback", False)]
            found, reached = await ask(primary)
            if fallback and not any(not is_partial(record) for record in found):
                more, reached_more = await ask(fallback)
                found, reached = found + more, reached or reached_more
            best = best_result(found)
            if best is not None:
                await self.store.upsert_result(replace(best, fetched_at=self._now()))
            return reached

        await self._once(what, fetch)

    # ------------------------------------------------------------------ race lists
    async def race_list(self, lists, state: str) -> None:
        """Fresh race lists of one state from each source that lists races, into the store.
        An empty answer isn't stored: the background refresh decides when "no races" is real."""
        from electionwatch.racelists import CACHE, budget_name, cache_ident, dump_list

        async def one(source) -> None:
            ident = cache_ident(state, source.name)
            what = f"racelist|{ident}"
            try:
                hit = await self.store.get_cached(CACHE, ident)
            except Exception:
                hit = None
            if self.fresh(what, hit[0] if hit else None):
                return

            async def fetch() -> bool:
                races = await self.call(budget_name(source.name, state), lambda: source.list_races(state, self.http))
                if races is None:
                    return False
                if races:
                    await self.store.put_cached(CACHE, ident, dump_list(races), self._now())
                return True

            await self._once(what, fetch)

        await asyncio.gather(*(one(source) for source in lists.listers(state)))

    # ------------------------------------------------------------------ early vote
    def early_vote_allowed(self) -> bool:
        """Whether the commands' early-vote client may download now (civicAPI has room)."""
        return self.rates is None or self.rates.has_room(EARLY_VOTE_BUDGET)


def _file_under(key, answers: list) -> list:
    """The answers for `key`; for a special election, a source's plain-key answers stand for
    it unless the source answered for the special key itself (see Poller._canonical_batch)."""
    mine = [answer for answer in answers if str(answer.key) == str(key)]
    if mine or not getattr(key, "special", False):
        return mine
    plain = str(replace(key, special=False))
    return [replace(answer, key=key) for answer in answers if str(answer.key) == plain]
