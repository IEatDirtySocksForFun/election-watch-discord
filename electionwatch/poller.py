"""Polling orchestration: fetch, persist, apply per-guild rules, and build Posts."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx

from electionwatch.config import load_races
from electionwatch.models import ExitPoll, OddsSnapshot, RaceCall, RaceResult, normalize_party
from electionwatch.poster import Post, Poster
from electionwatch.racekeys import describe
from electionwatch.settings import GuildSettings, post_due
from electionwatch.store.base import Store

logger = logging.getLogger(__name__)

_PARTY_COLOR = {"REP": 0xE03C31, "DEM": 0x2F6FDB}


TOP_CANDIDATES = 5   # results posts show only the leading five candidates


def _party_marker(party: str | None) -> str:
    try:  # full party table (electionwatch/data/parties.yaml) when installed
        from electionwatch.parties import party_emoji
        return party_emoji(party)
    except ImportError:
        return {"REP": "🔴", "DEM": "🔵"}.get(normalize_party(party) or "", "⚪")


def format_result(result: RaceResult, *, turnout: bool = False) -> Post:
    """Render a compact, source-neutral race result."""
    lines = []
    leaders = sorted(result.candidates, key=lambda c: c.votes, reverse=True)[:TOP_CANDIDATES]
    for candidate in leaders:
        pct = f" · {candidate.pct:.1f}%" if candidate.pct is not None else ""
        party = f" ({candidate.party})" if candidate.party else ""
        winner = " ✓" if candidate.winner else ""
        lines.append(
            f"{_party_marker(candidate.party)} {candidate.name}{party} {candidate.votes:,}{pct}{winner}"
        )
    reporting = []
    if result.expected_vote_pct is not None:
        reporting.append(f"{result.expected_vote_pct:.1f}".removesuffix(".0") + "% expected vote")
    if result.precincts_reporting is not None:
        precincts = f"{result.precincts_reporting:,}"
        if result.precincts_total is not None:
            precincts += f"/{result.precincts_total:,}"
        reporting.append(f"{precincts} precincts")
    if reporting:
        lines.append(" · ".join(reporting))
    if turnout:
        numbers = []
        if result.turnout_pct is not None:
            numbers.append(f"{result.turnout_pct:g}%")
        if result.total_votes is not None and result.registered_voters is not None:
            numbers.append(f"{result.total_votes:,}/{result.registered_voters:,} ballots")
        if numbers:
            lines.append("Turnout: " + " · ".join(numbers))
    return Post(
        "results",
        str(result.key),
        result.title or describe(result.key),
        lines,
        url=result.url,
        footer=f"Source: {result.source}",
    )


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


#: How an outlet is named in call posts, where its code alone would mislead.
OUTLET_NAMES = {"CIVICAPI": "civicAPI (its own decision desk)"}


def format_call(call: RaceCall) -> Post:
    outlet = OUTLET_NAMES.get(call.outlet, call.outlet)
    if call.retracted:
        lines = [f"Call retracted: {call.winner}", f"Outlet: {outlet}"]
    else:
        party = f" ({call.party})" if call.party else ""
        lines = [f"{_party_marker(call.party)} Projected winner: {call.winner}{party}", f"Outlet: {outlet}"]
    return Post("call", str(call.key), f"Race call · {describe(call.key)}", lines, url=call.url,
                color=None if call.retracted else _PARTY_COLOR.get(normalize_party(call.party) or ""),
                footer=f"Called {_as_utc(call.called_at):%b %d %H:%M} UTC")


def format_exit_poll(poll: ExitPoll) -> Post:
    lines = poll.summary.splitlines() if poll.summary else ["Exit poll available"]
    lines.append(f"Outlet: {poll.outlet}")
    return Post("exitpoll", str(poll.key), f"Exit poll · {describe(poll.key)}", lines, url=poll.url)


def is_partial(result: RaceResult) -> bool:
    """County-merge adapters flag a partial-state tally in the title: "(partial: N counties)"."""
    return "(partial:" in (result.title or "")


def best_result(answers: list[RaceResult]) -> RaceResult | None:
    """First complete answer in priority order, else the first partial one."""
    return next((r for r in answers if not is_partial(r)), answers[0] if answers else None)


def swing_detail(swing, history: list[OddsSnapshot], fallback: OddsSnapshot):
    """Turn detect_swing()'s answer into (move in points or None, latest snapshot).

    electionwatch/swings.py (a separate package) returns (move, earlier, latest); accept a
    plain truthy value too so a simpler detector still works.
    """
    if isinstance(swing, tuple) and len(swing) == 3:
        move, _earlier, latest = swing
        return float(move), latest
    latest = max(history, key=lambda snapshot: snapshot.ts) if history else fallback
    return None, latest


class Poller:
    def __init__(
        self,
        store: Store,
        sources: dict[str, list],
        poster: Poster,
        clock,
        http: httpx.AsyncClient,
        *,
        fetch_timeout: float = 30.0,
    ) -> None:
        self.store = store
        self.sources = sources
        self.poster = poster
        self.clock = clock
        self.http = http
        self.fetch_timeout = fetch_timeout
        self.default_races = load_races()
        # (guild, kind, race) -> body of the last results post. Only the top candidates are
        # shown, so a vote change further down (multi-seat races) can render an identical
        # post; those are skipped. In memory: at most one repeat after a restart.
        self._last_body: dict[tuple[int, str, str], tuple[str, ...]] = {}
        self._backoff: dict[str, tuple[int, datetime | None]] = {}   # source -> (failures, skip until)

    def _watched(self, guild: GuildSettings) -> set[str]:
        return set(guild.important) if guild.important else {str(r.key) for r in self.default_races}

    @staticmethod
    def _enabled(source) -> bool:
        """The brief asks for every *available* adapter: whether a source is enabled by
        default is decided by whoever built the sources dict (__main__ / the replay sandbox)."""
        try:
            return source.available()
        except Exception:
            logger.warning("Could not check availability of source %s", getattr(source, "name", "?"))
            return False

    async def _fetch(self, source, method: str, keys: list):
        """Fetch with a per-source back-off: after a failure the source is skipped for 1, 2, 4,
        8, then 15 minutes, so a site that starts refusing us (CNN answers 406 to bursts) is not
        hammered every minute. A success resets it."""
        name = getattr(source, "name", repr(source))
        failures, until = self._backoff.get(name, (0, None))
        now = self.clock()
        if until is not None and now < until:
            return []
        try:
            got = await asyncio.wait_for(getattr(source, method)(keys, self.http), timeout=self.fetch_timeout)
        except Exception:
            failures += 1
            self._backoff[name] = (failures, now + timedelta(minutes=min(2 ** (failures - 1), 15)))
            raise
        if failures:
            self._backoff.pop(name, None)
        return got

    async def _post(self, guild: GuildSettings, kind: str, ref: str, post: Post, now: datetime) -> None:
        if guild.channel_id is None or guild.paused:
            return
        ident, body = (guild.guild_id, kind, ref), tuple(post.lines)
        if kind == "results" and self._last_body.get(ident) == body:
            return
        try:
            await self.poster.post(guild.guild_id, guild.channel_id, post)
            await self.store.mark_posted(guild.guild_id, kind, ref, now)
            self._last_body[ident] = body
        except Exception:
            logger.exception("Could not deliver %s post to guild %s", kind, guild.guild_id)

    async def tick(self) -> None:
        now = self.clock()
        guilds = await self.store.all_guilds()
        if not guilds:
            return
        watched = set().union(*(self._watched(guild) for guild in guilds))
        keys = [race.key for race in self.default_races if str(race.key) in watched]
        known = {str(key): key for key in keys}
        # /watch can add races which are not in the YAML defaults.
        from electionwatch.racekeys import parse

        for text in watched - known.keys():
            try:
                key = parse(text)
            except ValueError:
                logger.warning("Ignoring invalid watched race key %s", text)
                continue
            keys.append(key)
            known[text] = key

        await self._results(now, guilds, keys)
        # Outlets and markets don't flag special elections (OH/FL Senate 2026 are specials):
        # ask them for the plain key too and file their answers under the watched special.
        self._aliases = {
            replace(key, special=False): key
            for key in keys
            if key.special and replace(key, special=False) not in keys
        }
        outlet_keys = keys + list(self._aliases)
        await self._calls(now, guilds, outlet_keys)
        await self._exit_polls(now, guilds, outlet_keys)
        await self._odds(now, guilds, outlet_keys)

    def _canonical(self, item):
        """item with its key replaced by the watched special race it stands for, if any."""
        special = getattr(self, "_aliases", {}).get(item.key)
        return replace(item, key=special) if special is not None else item

    async def _results(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        by_race: dict[str, list[RaceResult]] = defaultdict(list)
        selected: dict[str, RaceResult] = {}
        enabled = [source for source in self.sources.get("results", []) if self._enabled(source)]
        # Fallbacks (civicAPI, the outlet feeds) are asked only about races no official feed
        # answered completely: that keeps a big watch list from pulling every race from them
        # every minute. A guild that picked a fallback by name with /settings gets it for all.
        pinned = {name for guild in guilds for name in guild.results_sources}
        primary = [s for s in enabled if not getattr(s, "fallback", False) or s.name in pinned]
        fallback = [s for s in enabled if getattr(s, "fallback", False) and s.name not in pinned]
        await self._gather_results(primary, keys, by_race)
        complete = {race for race, answers in by_race.items() if any(not is_partial(r) for r in answers)}
        missing = [key for key in keys if str(key) not in complete]
        if missing:
            await self._gather_results(fallback, missing, by_race)
        # Adapter order is priority order: the first complete answer is the stored result. A
        # county merge titled "(partial: N counties)" only wins when nothing complete answered,
        # so a reachable state feed (tx_sos) or an enabled national fallback is preferred over
        # a partial-state county tally for the same race.
        for race, answers in by_race.items():
            selected[race] = best_result(answers)
        for race, result in selected.items():
            try:
                # The store's change time is compared with mark_posted() times, which are on
                # the poller's clock; adapters stamp fetched_at with the wall clock mid-tick.
                await self.store.upsert_result(replace(result, fetched_at=now))
            except Exception:
                logger.exception("Could not store results for %s", race)

        for guild in guilds:
            watched = self._watched(guild)
            allowed = set(guild.results_sources)
            for race in watched:
                candidates = by_race.get(race, [])
                result = best_result(
                    [item for item in candidates if not allowed or item.source in allowed]
                )
                if result is None:
                    continue
                try:
                    last = await self.store.last_posted(guild.guild_id, "results", race)
                    changed_at = await self.store.result_changed_at(result.key)
                    updated = changed_at is not None and (last is None or changed_at > last)
                    if post_due(guild, last, now, updated):
                        await self._post(
                            guild, "results", race, format_result(result, turnout=guild.post_turnout), now
                        )
                except Exception:
                    logger.exception("Could not process results for guild %s", guild.guild_id)

    async def _gather_results(self, sources: list, keys: list, by_race: dict) -> None:
        """Fetch results for keys from sources concurrently; append answers to by_race in
        adapter (priority) order. One slow state site must not hold up the others."""
        jobs = []
        for source in sources:
            source_keys = [key for key in keys if key.state in source.states]
            if source_keys:
                jobs.append((source, source_keys))
        answers = await asyncio.gather(
            *(self._fetch(source, "fetch", source_keys) for source, source_keys in jobs),
            return_exceptions=True,
        )
        for (source, source_keys), fetched in zip(jobs, answers):
            if isinstance(fetched, BaseException):
                logger.error("Results source %s failed", source.name, exc_info=fetched)
                continue
            asked = {str(key) for key in source_keys}
            for result in fetched:
                race = str(result.key)
                if race in asked:
                    by_race[race].append(result)

    async def _calls(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        for source in self.sources.get("calls", []):
            if not self._enabled(source):
                continue
            try:
                calls = await self._fetch(source, "fetch_calls", keys)
            except Exception:
                logger.exception("Call source %s failed", source.name)
                continue
            for call in map(self._canonical, calls):
                try:
                    new = await self.store.record_call(call)
                except Exception:
                    logger.exception("Could not store call for %s", call.key)
                    continue
                if not new:
                    continue
                for guild in guilds:
                    if (
                        str(call.key) in self._watched(guild)
                        and guild.post_calls
                        and call.outlet in guild.call_outlets
                    ):
                        await self._post(
                            guild, "call", f"{call.key}:{call.outlet}", format_call(call), now
                        )

    async def _exit_polls(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        for source in self.sources.get("exitpolls", []):
            if not self._enabled(source):
                continue
            try:
                polls = await self._fetch(source, "fetch_exit_polls", keys)
            except Exception:
                logger.exception("Exit-poll source %s failed", source.name)
                continue
            for poll in map(self._canonical, polls):
                ref = f"{poll.key}:{poll.outlet}"
                for guild in guilds:
                    if str(poll.key) not in self._watched(guild) or not guild.post_exit_polls:
                        continue
                    if await self.store.last_posted(guild.guild_id, "exitpoll", ref) is None:
                        await self._post(guild, "exitpoll", ref, format_exit_poll(poll), now)

    async def _odds(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        snapshots: list[OddsSnapshot] = []
        for source in self.sources.get("odds", []):
            if not self._enabled(source):
                continue
            try:
                fetched = await self._fetch(source, "fetch_odds", keys)
            except Exception:
                logger.exception("Odds source %s failed", source.name)
                continue
            for snapshot in map(self._canonical, fetched):
                try:
                    await self.store.add_odds(snapshot)
                    snapshots.append(snapshot)
                except Exception:
                    logger.exception("Could not store odds for %s", snapshot.key)
        if not snapshots or not any(guild.odds_alerts for guild in guilds):
            return
        try:
            from electionwatch.swings import detect_swing
        except ImportError:
            logger.debug("Swing detector is not installed; skipping odds alerts")
            return

        for guild in guilds:
            if not guild.odds_alerts:
                continue
            watched = self._watched(guild)
            window = timedelta(minutes=guild.odds_window_minutes)
            for snapshot in snapshots:
                if str(snapshot.key) not in watched or snapshot.market not in guild.odds_markets:
                    continue
                ref = f"{snapshot.key}:{snapshot.market}:{snapshot.outcome}"
                try:
                    last = await self.store.last_posted(guild.guild_id, "odds_swing", ref)
                    if last is not None and now - last < window:
                        continue
                    history = await self.store.odds_since(
                        snapshot.key, snapshot.market, snapshot.outcome, now - window
                    )
                    # detect_swing takes a timedelta window (see electionwatch/swings.py).
                    swing = detect_swing(history, guild.odds_swing_pts, window)
                    if not swing:
                        continue
                    move, latest = swing_detail(swing, history, snapshot)
                    headline = f"{snapshot.market} · {snapshot.outcome}: {latest.prob:.1%}"
                    if move is not None:
                        headline += f" ({move:+.1f} pts)"
                    post = Post(
                        "odds_swing",
                        str(snapshot.key),
                        f"Odds swing · {describe(snapshot.key)}",
                        [headline, f"Swing within {guild.odds_window_minutes} minutes"],
                        url=snapshot.url,
                    )
                    await self._post(guild, "odds_swing", ref, post, now)
                except Exception:
                    logger.exception("Could not process odds swing for guild %s", guild.guild_id)
