"""Polling orchestration: fetch, persist, apply per-guild rules, and build Posts."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable

import httpx

from electionwatch.config import load_races
from electionwatch.models import ExitPoll, OddsSnapshot, RaceCall, RaceResult
from electionwatch.poster import Post, Poster
from electionwatch.racekeys import describe
from electionwatch.settings import GuildSettings, post_due
from electionwatch.store.base import Store

logger = logging.getLogger(__name__)


def _party_marker(party: str | None) -> str:
    return {"REP": "🔴", "DEM": "🔵"}.get((party or "").upper(), "⚪")


def format_result(result: RaceResult, *, turnout: bool = False) -> Post:
    """Render a compact, source-neutral race result."""
    lines = []
    for candidate in result.candidates:
        pct = f" · {candidate.pct:.1f}%" if candidate.pct is not None else ""
        party = f" ({candidate.party})" if candidate.party else ""
        winner = " ✓" if candidate.winner else ""
        lines.append(
            f"{_party_marker(candidate.party)} {candidate.name}{party} {candidate.votes:,}{pct}{winner}"
        )
    reporting = []
    if result.expected_vote_pct is not None:
        reporting.append(f"{result.expected_vote_pct:g}% expected vote")
    if result.precincts_reporting is not None:
        precincts = f"{result.precincts_reporting:,}"
        if result.precincts_total is not None:
            precincts += f"/{result.precincts_total:,}"
        reporting.append(f"{precincts} precincts")
    if reporting:
        lines.append(" · ".join(reporting))
    if turnout and (result.turnout_pct is not None or result.registered_voters is not None):
        text = "Turnout"
        if result.turnout_pct is not None:
            text += f": {result.turnout_pct:g}%"
        if result.registered_voters is not None and result.total_votes is not None:
            text += f" · {result.total_votes:,}/{result.registered_voters:,} registered"
        lines.append(text)
    return Post(
        "results",
        str(result.key),
        result.title or describe(result.key),
        lines,
        url=result.url,
        footer=f"Source: {result.source}",
    )


def format_call(call: RaceCall) -> Post:
    if call.retracted:
        lines = [f"Call retracted: {call.winner}", f"Outlet: {call.outlet}"]
    else:
        party = f" ({call.party})" if call.party else ""
        lines = [f"Projected winner: {call.winner}{party}", f"Outlet: {call.outlet}"]
    return Post("call", str(call.key), describe(call.key), lines, url=call.url)


def format_exit_poll(poll: ExitPoll) -> Post:
    lines = poll.summary.splitlines() if poll.summary else ["Exit poll available"]
    lines.append(f"Outlet: {poll.outlet}")
    return Post("exitpoll", str(poll.key), f"Exit poll · {describe(poll.key)}", lines, url=poll.url)


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

    def _watched(self, guild: GuildSettings) -> set[str]:
        return set(guild.important) if guild.important else {str(r.key) for r in self.default_races}

    @staticmethod
    def _enabled(source) -> bool:
        try:
            return source.available() and source.enabled_by_default
        except Exception:
            logger.warning("Could not check availability of source %s", getattr(source, "name", "?"))
            return False

    async def _fetch(self, source, method: str, keys: list):
        call = getattr(source, method)
        return await asyncio.wait_for(call(keys, self.http), timeout=self.fetch_timeout)

    async def _post(self, guild: GuildSettings, kind: str, ref: str, post: Post, now: datetime) -> None:
        if guild.channel_id is None or guild.paused:
            return
        try:
            await self.poster.post(guild.guild_id, guild.channel_id, post)
            await self.store.mark_posted(guild.guild_id, kind, ref, now)
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
        await self._calls(now, guilds, keys)
        await self._exit_polls(now, guilds, keys)
        await self._odds(now, guilds, keys)

    async def _results(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        by_race: dict[str, list[RaceResult]] = defaultdict(list)
        selected: dict[str, RaceResult] = {}
        for source in self.sources.get("results", []):
            if not self._enabled(source):
                continue
            source_keys = [key for key in keys if key.state in source.states]
            if not source_keys:
                continue
            try:
                fetched = await self._fetch(source, "fetch", source_keys)
            except Exception:
                logger.exception("Results source %s failed", source.name)
                continue
            for result in fetched:
                race = str(result.key)
                if race not in {str(key) for key in source_keys}:
                    continue
                by_race[race].append(result)
                # Adapter order is priority order; the first answer is the stored result.
                if race not in selected:
                    selected[race] = result
        for race, result in selected.items():
            try:
                await self.store.upsert_result(result)
            except Exception:
                logger.exception("Could not store results for %s", race)

        for guild in guilds:
            watched = self._watched(guild)
            allowed = set(guild.results_sources)
            for race in watched:
                candidates = by_race.get(race, [])
                result = next(
                    (item for item in candidates if not allowed or item.source in allowed), None
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

    async def _calls(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        for source in self.sources.get("calls", []):
            if not self._enabled(source):
                continue
            try:
                calls = await self._fetch(source, "fetch_calls", keys)
            except Exception:
                logger.exception("Call source %s failed", source.name)
                continue
            for call in calls:
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
            for poll in polls:
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
            for snapshot in fetched:
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
                    swing = detect_swing(history, guild.odds_swing_pts, guild.odds_window_minutes)
                    if not swing:
                        continue
                    latest = history[-1] if history else snapshot
                    post = Post(
                        "odds_swing",
                        str(snapshot.key),
                        f"Odds swing · {describe(snapshot.key)}",
                        [
                            f"{snapshot.market} · {snapshot.outcome}: {latest.prob:.1%}",
                            f"Move detected within {guild.odds_window_minutes} minutes",
                        ],
                        url=snapshot.url,
                    )
                    await self._post(guild, "odds_swing", ref, post, now)
                except Exception:
                    logger.exception("Could not process odds swing for guild %s", guild.guild_id)
