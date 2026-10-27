"""Data access, caching, and deterministic embeds for the public odds/polls commands."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from electionwatch.bot.poster import (
    MAX_DESCRIPTION, MAX_EMBED_TOTAL, MAX_FIELD_NAME, MAX_FIELD_VALUE, MAX_FOOTER, MAX_TITLE, clip,
)
from electionwatch.models import OddsSnapshot, Poll, RaceKey, normalize_party
from electionwatch.racekeys import describe

logger = logging.getLogger(__name__)
_MARKET_ORDER = {"kalshi": 0, "polymarket": 1}
_PARTY_MARKERS = {"DEM": "🔵", "REP": "🔴"}
MAX_EMBED_TOTAL = 6000  # Discord's limit on all embed text combined


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def latest_odds(snapshots: list[OddsSnapshot]) -> list[OddsSnapshot]:
    """Keep the newest snapshot for each market/outcome, in display order."""
    latest: dict[tuple[str, str], OddsSnapshot] = {}
    for snapshot in snapshots:
        ident = (snapshot.market, snapshot.outcome)
        if ident not in latest or _utc(snapshot.ts) > _utc(latest[ident].ts):
            latest[ident] = snapshot
    return sorted(
        latest.values(),
        key=lambda item: (
            _MARKET_ORDER.get(item.market.casefold(), 99), item.market, item.outcome,
        ),
    )


def _market_name(market: str) -> str:
    return {"kalshi": "Kalshi", "polymarket": "Polymarket"}.get(market.casefold(), market.title())


def _outcome_text(snapshot: OddsSnapshot, moves: dict[int, float] | None = None) -> str:
    outcome = f"[{snapshot.outcome}]({snapshot.url})" if snapshot.url else snapshot.outcome
    text = f"{outcome} · {snapshot.prob:.1%}"
    for hours, label in ((1, "1h"), (24, "24h")):
        if moves and hours in moves:
            text += f" · {label} {moves[hours]:+.1f} pts"
    return text


def format_odds(
    key: RaceKey,
    snapshots: list[OddsSnapshot],
    moves: dict[tuple[str, str], dict[int, float]] | None = None,
) -> discord.Embed:
    """Render one race, grouping outcome probabilities under each market."""
    snapshots = latest_odds(snapshots)
    embed = discord.Embed(title=clip(f"Odds · {describe(key)}", MAX_TITLE), color=discord.Color.blurple())
    if not snapshots:
        embed.description = "No current prediction-market odds are available for this race."
        return embed
    markets = []
    for snapshot in snapshots:
        if snapshot.market not in markets:
            markets.append(snapshot.market)
    for market in markets:
        lines = [
            _outcome_text(item, (moves or {}).get((item.market, item.outcome)))
            for item in snapshots
            if item.market == market
        ]
        embed.add_field(
            name=clip(_market_name(market), MAX_FIELD_NAME),
            value=clip("\n".join(lines), MAX_FIELD_VALUE),
            inline=False,
        )
    updated = max(_utc(item.ts) for item in snapshots)
    embed.set_footer(text=clip(f"Last update: {updated:%Y-%m-%d %H:%M} UTC", MAX_FOOTER))
    return embed


def format_odds_overview(items: list[tuple[RaceKey, list[OddsSnapshot]]]) -> discord.Embed:
    """Render watched races as one compact line each."""
    lines: list[str] = []
    updates: list[datetime] = []
    for key, values in items:
        snapshots = latest_odds(values)
        if not snapshots:
            lines.append(f"**{key}** · No current odds")
            continue
        markets = []
        for snapshot in snapshots:
            if snapshot.market not in markets:
                markets.append(snapshot.market)
        parts = []
        for market in markets:
            outcomes = " / ".join(
                _outcome_text(item) for item in snapshots if item.market == market
            )
            parts.append(f"{_market_name(market)}: {outcomes}")
        lines.append(f"**{key}** · " + " · ".join(parts))
        updates.extend(_utc(item.ts) for item in snapshots)
    embed = discord.Embed(
        title="Watched race odds",
        description=clip("\n".join(lines) if lines else "No races are being watched.", MAX_DESCRIPTION),
        color=discord.Color.blurple(),
    )
    if updates:
        embed.set_footer(text=clip(f"Last update: {max(updates):%Y-%m-%d %H:%M} UTC", MAX_FOOTER))
    return embed


def poll_completeness(poll: Poll) -> int:
    """Number of populated fields, used to choose between duplicate source records."""
    fields = (
        poll.start_date, poll.sample_size, poll.population, poll.sponsor,
        poll.partisan, poll.source, poll.url, poll.results,
    )
    return sum(value is not None and value != "" and value != [] for value in fields) + sum(
        result.party is not None for result in poll.results
    )


def merge_polls(polls: list[Poll]) -> list[Poll]:
    """De-dupe source copies of a poll and return newest end dates first."""
    selected: dict[tuple[str, object, str], Poll] = {}
    for poll in polls:
        ident = (poll.pollster.strip().casefold(), poll.end_date, str(poll.key))
        if ident not in selected or poll_completeness(poll) > poll_completeness(selected[ident]):
            selected[ident] = poll
    # Sources disagree on who "the pollster" is (sponsor vs. fieldwork firm, e.g. "N+ Univision"
    # vs. "YouGov" sponsored by N+ Univision) and on the date they list. Same race, a shared
    # pollster/sponsor name, identical numbers and end dates within a week = one poll.
    kept: list[Poll] = []
    for poll in sorted(selected.values(), key=poll_completeness, reverse=True):
        if not any(_same_poll(poll, other) for other in kept):
            kept.append(poll)
    return sorted(kept, key=lambda poll: (poll.end_date, poll.pollster), reverse=True)


def _names(poll: Poll) -> set[str]:
    return {n.strip().casefold() for n in (poll.pollster, poll.sponsor) if n and n.strip()}


def _numbers(poll: Poll) -> tuple[float, ...]:
    return tuple(sorted(round(r.pct, 1) for r in poll.results))


def _same_poll(a: Poll, b: Poll) -> bool:
    return (str(a.key) == str(b.key) and bool(_names(a) & _names(b))
            and _numbers(a) == _numbers(b) and abs((a.end_date - b.end_date).days) <= 7)


def _poll_dates(poll: Poll) -> str:
    end = f"{poll.end_date:%b %d, %Y}"
    if poll.start_date is None:
        return end
    if poll.start_date.year == poll.end_date.year:
        return f"{poll.start_date:%b %d}–{end}"
    return f"{poll.start_date:%b %d, %Y}–{end}"


def _internal_tag(poll: Poll) -> str:
    if not poll.partisan:
        return ""
    party = normalize_party(poll.partisan)
    return f" INTERNAL ({ {'DEM': 'D', 'REP': 'R'}.get(party, party) })"


def format_polls(
    key: RaceKey,
    polls: list[Poll],
    *,
    count: int = 5,
    internals: bool = True,
    sources_available: bool = True,
) -> discord.Embed:
    if not 1 <= count <= 10:
        raise ValueError("count must be between 1 and 10")
    embed = discord.Embed(title=clip(f"Polls · {describe(key)}", MAX_TITLE), color=discord.Color.blurple())
    if not sources_available:
        embed.description = "No poll source is installed or available."
        return embed
    visible = [poll for poll in merge_polls(polls) if internals or not poll.internal][:count]
    if not visible:
        embed.description = "No recent polls are available for this race" + (
            " with the selected internals filter." if not internals else "."
        )
        return embed
    fields: list[tuple[str, str]] = []
    for poll in visible:
        details = [_poll_dates(poll)]
        if poll.sample_size is not None:
            details.append(f"n={poll.sample_size:,}" + (f" {poll.population}" if poll.population else ""))
        elif poll.population:
            details.append(poll.population)
        if poll.sponsor:
            details.append(f"Sponsor: {poll.sponsor}")
        results = " · ".join(
            f"{_PARTY_MARKERS.get(normalize_party(result.party) or '', '⚪')} "
            f"{result.name} {result.pct:.1f}%"
            for result in poll.results
        ) or "No candidate results supplied"
        lines = [" · ".join(details), results]
        if poll.url:
            lines.append(f"[Poll link]({poll.url})")
        fields.append((clip(poll.pollster + _internal_tag(poll), MAX_FIELD_NAME),
                       clip("\n".join(lines), MAX_FIELD_VALUE)))
    # Discord rejects the whole message (400) when an embed's text exceeds 6000 characters in
    # total: ten polls with many candidates can. Keep the newest polls that fit.
    budget = MAX_EMBED_TOTAL - len(embed.title) - 100   # room for the description below
    shown = 0
    for name, value in fields:
        if len(name) + len(value) > budget:
            break
        budget -= len(name) + len(value)
        shown += 1
        embed.add_field(name=name, value=value, inline=False)
    embed.description = f"Showing {shown} most recent poll" + ("s" if shown != 1 else "") + (
        "; partisan internals included." if internals else "; partisan internals excluded."
    )
    return embed


@dataclass
class _CacheEntry:
    at: datetime
    value: list
    ttl: timedelta | None = None   # None = the caller's normal TTL


class CommandData:
    """Per-race command cache. Source failures are isolated just like in the poller."""

    def __init__(self, store, sources: dict[str, list], http_client, clock=None) -> None:
        self.store = store
        self.sources = sources
        self.http_client = http_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._odds_cache: dict[str, _CacheEntry] = {}
        self._polls_cache: dict[str, _CacheEntry] = {}

    def _now(self) -> datetime:
        return _utc(self.clock())

    def _available(self, kind: str) -> list:
        enabled = []
        for source in self.sources.get(kind, []):
            try:
                if source.available():
                    enabled.append(source)
            except Exception:
                logger.warning("Could not check availability of %s source %s", kind, getattr(source, "name", "?"))
        return enabled

    async def get_odds(self, key: RaceKey) -> list[OddsSnapshot]:
        cache_key = str(key)
        now = self._now()
        cached = self._odds_cache.get(cache_key)
        if cached and now - cached.at < timedelta(seconds=60):
            return cached.value

        stored: list[OddsSnapshot] = []
        # Stores with native latest-per-outcome lookups avoid a needless source request.
        # Older Store implementations simply take the live-fetch path.
        getter = getattr(self.store, "latest_odds", None)
        if getter is not None:
            try:
                stored = latest_odds(await getter(key))
            except Exception:
                logger.exception("Could not read stored odds for %s", key)
        if stored and max(_utc(item.ts) for item in stored) >= now - timedelta(minutes=2):
            self._odds_cache[cache_key] = _CacheEntry(now, stored)
            return stored

        fetched: list[OddsSnapshot] = []
        for source in self._available("odds"):
            try:
                values = await source.fetch_odds([key], self.http_client)
            except Exception:
                logger.exception("Odds source %s failed", getattr(source, "name", "?"))
                continue
            for snapshot in values:
                if snapshot.key != key:
                    continue
                fetched.append(snapshot)
                try:
                    await self.store.add_odds(snapshot)
                except Exception:
                    logger.exception("Could not store odds for %s", key)
        result = latest_odds(fetched or stored)
        self._odds_cache[cache_key] = _CacheEntry(now, result)
        return result

    async def _moves(self, snapshots: list[OddsSnapshot], now: datetime) -> dict[tuple[str, str], dict[int, float]]:
        moves: dict[tuple[str, str], dict[int, float]] = {}
        for snapshot in snapshots:
            for hours in (1, 24):
                try:
                    history = await self.store.odds_since(
                        snapshot.key, snapshot.market, snapshot.outcome, now - timedelta(hours=hours)
                    )
                except Exception:
                    logger.exception("Could not read odds history for %s", snapshot.key)
                    continue
                earlier = [item for item in history if _utc(item.ts) < _utc(snapshot.ts)]
                if earlier:
                    baseline = min(earlier, key=lambda item: _utc(item.ts))
                    moves.setdefault((snapshot.market, snapshot.outcome), {})[hours] = (
                        snapshot.prob - baseline.prob
                    ) * 100
        return moves

    async def odds_embed(self, key: RaceKey) -> discord.Embed:
        snapshots = await self.get_odds(key)
        return format_odds(key, snapshots, await self._moves(snapshots, self._now()))

    async def odds_overview(self, keys: list[RaceKey]) -> discord.Embed:
        return format_odds_overview([(key, await self.get_odds(key)) for key in keys])

    async def get_polls(self, key: RaceKey) -> list[Poll]:
        cache_key = str(key)
        now = self._now()
        cached = self._polls_cache.get(cache_key)
        if cached and now - cached.at < (cached.ttl or timedelta(minutes=15)):
            return cached.value
        polls: list[Poll] = []
        failed = False
        for source in self._available("polls"):
            try:
                values = await source.fetch_polls([key], self.http_client)
            except Exception:
                logger.exception("Poll source %s failed", getattr(source, "name", "?"))
                failed = True
                continue
            polls.extend(poll for poll in values if poll.key == key)
        result = merge_polls(polls)
        # A failed source is retried after a minute instead of hiding its polls for 15.
        self._polls_cache[cache_key] = _CacheEntry(now, result, timedelta(minutes=1) if failed else None)
        return result

    async def polls_embed(self, key: RaceKey, count: int = 5, internals: bool = True) -> discord.Embed:
        available = bool(self._available("polls"))
        polls = await self.get_polls(key) if available else []
        return format_polls(key, polls, count=count, internals=internals, sources_available=available)
