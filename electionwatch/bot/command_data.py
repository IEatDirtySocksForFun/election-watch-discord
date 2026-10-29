"""Deterministic embeds for the public /bettingmarkets and /polls commands, read from the database.

The poller's background refresh stores odds snapshots and each poll source's polls; nothing
here calls a source unless the bot runs with EW_COMMAND_DATA=live (see CommandData)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from electionwatch.bot import emoji
from electionwatch.bot.poster import (
    MAX_DESCRIPTION,
    MAX_EMBED_TOTAL,
    MAX_FIELD_NAME,
    MAX_FIELD_VALUE,
    MAX_FOOTER,
    MAX_TITLE,
    clip,
)
from electionwatch.models import OddsSnapshot, Poll, RaceKey, normalize_party
from electionwatch.racekeys import describe

logger = logging.getLogger(__name__)
_MARKET_ORDER = {"kalshi": 0, "polymarket": 1}


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
        key=lambda snapshot: (
            _MARKET_ORDER.get(snapshot.market.casefold(), 99),
            snapshot.market,
            snapshot.outcome,
        ),
    )


def _market_name(market: str) -> str:
    return {"kalshi": "Kalshi", "polymarket": "Polymarket"}.get(market.casefold(), market.title())


def _market_title(market: str) -> str:
    """'📈 Kalshi': the market's name with its icon, for headings."""
    icon = emoji.MARKETS.get(market.casefold())
    return f"{icon} {_market_name(market)}" if icon else _market_name(market)


def _outcome_text(snapshot: OddsSnapshot, moves: dict[int, float] | None = None) -> str:
    label = snapshot.outcome
    candidate = getattr(snapshot, "candidate", None)
    if candidate and candidate not in snapshot.outcome:
        label = f"{candidate} ({snapshot.outcome})"  # "Ken Paxton (REP)"
    outcome = f"[{label}]({snapshot.url})" if snapshot.url else label
    if emoji.is_party(snapshot.outcome):  # "REP" gets 🔴; a market priced by candidate name gets none
        outcome = f"{emoji.party(snapshot.outcome)} {outcome}"
    text = f"{outcome} · {snapshot.prob:.1%}"
    for hours, label in ((1, "1h"), (24, "24h")):
        if moves and hours in moves:
            text += f" · {label} {emoji.move(moves[hours])} pts"
    return text


def format_odds(
    key: RaceKey,
    snapshots: list[OddsSnapshot],
    moves: dict[tuple[str, str], dict[int, float]] | None = None,
) -> discord.Embed:
    """Render one race, grouping outcome probabilities under each market."""
    snapshots = latest_odds(snapshots)
    embed = discord.Embed(
        title=clip(f"{emoji.ODDS} Betting markets · {describe(key)}", MAX_TITLE), color=discord.Color.blurple()
    )
    if not snapshots:
        embed.description = "No current prediction-market odds are available for this race."
        return embed
    markets = []
    for snapshot in snapshots:
        if snapshot.market not in markets:
            markets.append(snapshot.market)
    for market in markets:
        lines = [
            _outcome_text(snapshot, (moves or {}).get((snapshot.market, snapshot.outcome)))
            for snapshot in snapshots
            if snapshot.market == market
        ]
        embed.add_field(
            name=clip(_market_title(market), MAX_FIELD_NAME),
            value=clip("\n".join(lines), MAX_FIELD_VALUE),
            inline=False,
        )
    updated = max(_utc(snapshot.ts) for snapshot in snapshots)
    embed.set_footer(text=clip(f"🕒 Last update: {updated:%Y-%m-%d %H:%M} UTC", MAX_FOOTER))
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
            outcomes = " / ".join(_outcome_text(snapshot) for snapshot in snapshots if snapshot.market == market)
            parts.append(f"{_market_title(market)}: {outcomes}")
        lines.append(f"**{key}** · " + " · ".join(parts))
        updates.extend(_utc(snapshot.ts) for snapshot in snapshots)
    embed = discord.Embed(
        title=f"{emoji.ODDS} Betting markets · watched races",
        description=clip("\n".join(lines) if lines else "No races are being watched.", MAX_DESCRIPTION),
        color=discord.Color.blurple(),
    )
    if updates:
        embed.set_footer(text=clip(f"🕒 Last update: {max(updates):%Y-%m-%d %H:%M} UTC", MAX_FOOTER))
    return embed


def poll_completeness(poll: Poll) -> int:
    """Number of populated fields, used to choose between duplicate source records."""
    fields = (
        poll.start_date,
        poll.sample_size,
        poll.population,
        poll.sponsor,
        poll.partisan,
        poll.source,
        poll.url,
        poll.results,
    )
    return sum(value is not None and value != "" and value != [] for value in fields) + sum(
        poll_result.party is not None for poll_result in poll.results
    )


def merge_polls(polls: list[Poll]) -> list[Poll]:
    """De-dupe source copies of a poll and return newest end dates first."""
    selected: dict[tuple[str, object, str], Poll] = {}
    for poll in polls:
        ident = (poll.pollster.strip().casefold(), poll.end_date, str(poll.key))
        if ident not in selected or poll_completeness(poll) > poll_completeness(selected[ident]):
            selected[ident] = poll
    # Sources disagree on who "the pollster" is (sponsor vs. fieldwork firm, e.g. "N+ Univision"
    # vs. "YouGov" sponsored by N+ Univision) and on the date listed. Same race, a shared
    # pollster/sponsor name, identical numbers and end dates within a week = one poll.
    kept: list[Poll] = []
    for poll in sorted(selected.values(), key=poll_completeness, reverse=True):
        if not any(_same_poll(poll, other) for other in kept):
            kept.append(poll)
    return sorted(kept, key=lambda poll: (poll.end_date, poll.pollster), reverse=True)


def _names(poll: Poll) -> set[str]:
    return {name.strip().casefold() for name in (poll.pollster, poll.sponsor) if name and name.strip()}


def _numbers(poll: Poll) -> tuple[float, ...]:
    return tuple(sorted(round(poll_result.pct, 1) for poll_result in poll.results))


def _same_poll(left: Poll, right: Poll) -> bool:
    return (
        str(left.key) == str(right.key)
        and bool(_names(left) & _names(right))
        and _numbers(left) == _numbers(right)
        and abs((left.end_date - right.end_date).days) <= 7
    )


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
    return f" · {emoji.INTERNAL} INTERNAL ({ {'DEM': 'D', 'REP': 'R'}.get(party, party) })"


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
    embed = discord.Embed(
        title=clip(f"{emoji.POLLS} Polls · {describe(key)}", MAX_TITLE), color=discord.Color.blurple()
    )
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
        details = [f"{emoji.DATES} {_poll_dates(poll)}"]
        if poll.sample_size is not None:
            details.append(
                f"{emoji.SAMPLE} n={poll.sample_size:,}" + (f" {poll.population}" if poll.population else "")
            )
        elif poll.population:
            details.append(f"{emoji.SAMPLE} {poll.population}")
        if poll.sponsor:
            details.append(f"Sponsor: {poll.sponsor}")
        results = (
            " · ".join(
                f"{emoji.party(poll_result.party)} {poll_result.name} {poll_result.pct:.1f}%"
                for poll_result in poll.results
            )
            or "No candidate results supplied"
        )
        lines = [" · ".join(details), results]
        if poll.url:
            lines.append(f"{emoji.LINK} [Poll link]({poll.url})")
        fields.append(
            (clip(poll.pollster + _internal_tag(poll), MAX_FIELD_NAME), clip("\n".join(lines), MAX_FIELD_VALUE))
        )
    # Discord rejects the whole message (400) when an embed's text exceeds 6000 characters in
    # total: ten polls with many candidates can. Keep the newest polls that fit.
    budget = MAX_EMBED_TOTAL - len(embed.title) - 100  # room for the description below
    shown = 0
    for name, value in fields:
        if len(name) + len(value) > budget:
            break
        budget -= len(name) + len(value)
        shown += 1
        embed.add_field(name=name, value=value, inline=False)
    embed.description = (
        f"Showing {shown} most recent poll"
        + ("s" if shown != 1 else "")
        + ("; partisan internals included." if internals else "; partisan internals excluded.")
    )
    return embed


QUEUED = (
    "Nothing is stored for this race yet: it is queued for the next {kind} refresh "
    "(about every {every}). Try again then."
)


def _every(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h"


class CommandData:
    """What /bettingmarkets and /polls show. By default both read only the database: the poller's
    background refresh (electionwatch/ratelimit.py budgets) is the only thing that calls the
    odds and poll sources, so users repeating a command never reach those APIs. A race nobody
    watches is queued (electionwatch/demand.py) and fetched by the next refresh.

    With EW_COMMAND_DATA=live, `live` (electionwatch/live.py) first fetches the race from the
    sources, within the same budgets, and the answer is read back from the database."""

    def __init__(
        self, store, sources: dict[str, list], http_client, clock=None, *, rates=None, demand=None, live=None
    ) -> None:
        self.store = store
        self.sources = sources
        self.http_client = http_client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rates = rates
        self.demand = demand
        self.live = live

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

    def _queue(self, kind: str, key: RaceKey) -> None:
        if self.demand is not None:
            self.demand.add(kind, str(key))

    def refresh_every(self, kind: str) -> float:
        if self.rates is not None:
            return self.rates.kind_interval(kind)
        from electionwatch.ratelimit import FLOORS

        return FLOORS.get(kind, 60)

    async def get_odds(self, key: RaceKey) -> list[OddsSnapshot]:
        if self.live is not None:
            await self.live.odds(key)
        try:
            stored = latest_odds(await self.store.latest_odds(key))
        except Exception:
            logger.exception("Could not read stored odds for %s", key)
            return []
        if not stored:
            self._queue("odds", key)
        return stored

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
                older = [old_snapshot for old_snapshot in history if _utc(old_snapshot.ts) < _utc(snapshot.ts)]
                if older:
                    baseline = min(older, key=lambda old_snapshot: _utc(old_snapshot.ts))
                    moves.setdefault((snapshot.market, snapshot.outcome), {})[hours] = (
                        snapshot.prob - baseline.prob
                    ) * 100
        return moves

    async def _odds_checked(self, key: RaceKey) -> bool:
        from electionwatch.poller import ODDS_CHECKED

        try:
            return await self.store.get_cached(ODDS_CHECKED, str(key)) is not None
        except Exception:
            logger.exception("Could not read the odds check for %s", key)
            return False

    async def odds_embed(self, key: RaceKey) -> discord.Embed:
        snapshots = await self.get_odds(key)
        embed = format_odds(key, snapshots, await self._moves(snapshots, self._now()))
        if not snapshots and self._available("odds") and not await self._odds_checked(key):
            embed.description = QUEUED.format(kind="odds", every=_every(self.refresh_every("odds")))
        return embed

    async def odds_overview(self, keys: list[RaceKey]) -> discord.Embed:
        if self.live is not None:
            await asyncio.gather(*(self.live.odds(key) for key in keys))
        return format_odds_overview([(key, await self.get_odds(key)) for key in keys])

    async def get_polls(self, key: RaceKey) -> list[Poll] | None:
        """Stored polls from every poll source; None when no source has stored this race yet."""
        from electionwatch.poller import POLLS_CACHE, load_polls, polls_ident

        if self.live is not None:
            await self.live.polls(key)
        polls: list[Poll] = []
        stored = False
        for source in self._available("polls"):
            try:
                hit = await self.store.get_cached(POLLS_CACHE, polls_ident(source.name, key))
            except Exception:
                logger.exception("Could not read stored polls for %s", key)
                continue
            if hit is not None:
                stored = True
                polls.extend(load_polls(hit[1]))
        if not stored:
            self._queue("polls", key)
            return None
        return merge_polls(polls)

    async def polls_embed(self, key: RaceKey, count: int = 5, internals: bool = True) -> discord.Embed:
        available = bool(self._available("polls"))
        polls = await self.get_polls(key) if available else []
        embed = format_polls(key, polls or [], count=count, internals=internals, sources_available=available)
        if polls is None:
            embed.description = QUEUED.format(kind="polls", every=_every(self.refresh_every("polls")))
        return embed
