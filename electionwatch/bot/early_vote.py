"""Early-vote embeds for /earlyvote and the optional daily summary post (civicAPI data).

The command and the daily post read the pages the bot's hourly refresher stored in MongoDB
(``bot.early_vote`` is a cache-only EarlyVoteClient); they never call civicAPI themselves.

Every embed links the civicapi.org early-vote page and names civicAPI in the footer: that is
civicAPI's attribution requirement. Rendering is deterministic and fits Discord's limits.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import discord

from electionwatch.bot.poster import (
    MAX_DESCRIPTION,
    MAX_EMBED_TOTAL,
    MAX_FIELD_NAME,
    MAX_FIELD_VALUE,
    MAX_FOOTER,
    MAX_TITLE,
    clip,
)
from electionwatch.config import load_races
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import GuildSettings
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.early_vote import (
    CATEGORY_LABELS,
    EarlyVote,
    EarlyVoteClient,
    National,
    NotCached,
    page_url,
)

logger = logging.getLogger(__name__)
BREAKDOWNS = ("party", "method", "age", "county")
MAX_COUNTIES = 25
MAX_CHOICES = 25
AUTOCOMPLETE_TIMEOUT = 2.0  # Discord drops autocomplete answers after 3 s
POST_KIND = "early_vote"  # store.mark_posted kind for the daily summary
_NAMES = {name.casefold(): code for code, name in STATES.items()}
_NAMES["washington dc"] = _NAMES["washington d.c."] = _NAMES["d.c."] = "DC"
_PARTY_ORDER = {"Democratic": 0, "Republican": 1}
COLOR = discord.Color.teal()


def early_vote_client(bot) -> EarlyVoteClient:
    """The bot's cache-only client (reads stored pages), or a plain client when a bot stand-in
    has none (tests)."""
    client = getattr(bot, "early_vote", None)
    if client is None:
        client = EarlyVoteClient(bot.http_client)
        bot.early_vote = client
    return client


def resolve_state(text: str) -> str:
    """'nc', 'NC', 'North Carolina' -> 'NC'. ValueError for anything else (50 states + DC)."""
    cleaned = " ".join(text.split())
    if cleaned.upper() in STATES:
        return cleaned.upper()
    code = _NAMES.get(cleaned.casefold())
    if code:
        return code
    shown = clip(cleaned, 60) or "(empty)"
    raise ValueError(f"Unknown state {shown!r}: use a two-letter code like NC or a name like North Carolina.")


def state_choices(states: list[str], current: str) -> list[tuple[str, str]]:
    """Autocomplete (name, value) pairs: codes first-match, then names, at most 25."""
    needle = current.strip().casefold()
    starts, contains = [], []
    for code in states:
        name = STATES.get(code, code)
        label = f"{name} ({code})"
        if not needle or code.casefold().startswith(needle) or name.casefold().startswith(needle):
            starts.append((label[:100], code))
        elif needle in name.casefold():
            contains.append((label[:100], code))
    return (starts + contains)[:MAX_CHOICES]


def _n(value: int) -> str:
    return f"{value:,}"


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "–"


def _party_lines(parties: dict[str, int], whole: int) -> list[str]:
    ordered = sorted(parties.items(), key=lambda item: (_PARTY_ORDER.get(item[0], 2), -item[1], item[0]))
    return [f"{party}: {_n(votes)} ({_pct(votes, whole)})" for party, votes in ordered]


def _footer(updated: date | None) -> str:
    return f"Early vote: civicAPI · updated {updated.isoformat() if updated else 'date unknown'}"


def _fit(lines: list[str], limit: int, more: str = "… and {n} more") -> str:
    """Join whole lines up to ``limit`` characters, ending with a count of what was left out."""
    out: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        tail = len(more.format(n=len(lines) - index)) + 1
        cost = len(line) + (1 if out else 0)
        if used + cost + (tail if index < len(lines) - 1 else 0) > limit:
            out.append(more.format(n=len(lines) - index))
            break
        out.append(line)
        used += cost
    return clip("\n".join(out), limit)


def _embed(title: str, url: str, updated: date | None) -> discord.Embed:
    embed = discord.Embed(title=clip(title, MAX_TITLE), url=url, color=COLOR)
    embed.set_footer(text=clip(_footer(updated), MAX_FOOTER))
    return embed


def _add_field(embed: discord.Embed, name: str, lines: list[str]) -> None:
    if len(embed.fields) >= 25 or not lines:
        return
    embed.add_field(name=clip(name, MAX_FIELD_NAME), value=_fit(lines, MAX_FIELD_VALUE), inline=False)


def _summary_lines(ev: EarlyVote) -> list[str]:
    method = ev.by_method
    lines = [f"**{_n(ev.total_ballots)}** early ballots cast (mail returned + in person)"]
    cast = [
        f"{label}: {_n(method[c])}" for c, label in (("returned", "Mail"), ("inperson", "In person")) if c in method
    ]
    if cast:
        lines.append(" · ".join(cast))
    if "requested" in method:
        rate = f" ({_pct(method.get('returned', 0), method['requested'])} returned)" if "returned" in method else ""
        lines.append(f"Mail ballots requested: {_n(method['requested'])}{rate}")
    if "grace" in method:
        lines.append(f"Grace-period votes (not in the total): {_n(method['grace'])}")
    parties = ev.by_party
    if parties:
        shares = " · ".join(
            f"{p} {_pct(v, ev.total_ballots)}"
            for p, v in sorted(parties.items(), key=lambda i: (_PARTY_ORDER.get(i[0], 2), -i[1]))
        )
        lines.append(f"By party registration: {shares}")
    if ev.comparison:
        c = ev.comparison
        change = f" (now {(ev.total_ballots - c.total) / c.total:+.1%})" if c.total else ""
        lines.append(f"Same point in {c.year} ({c.on.isoformat()}): {_n(c.total)}{change}")
        if c.labels:
            # Past years can count differently (NC 2022 "returned" includes one-stop voting).
            lines.append(clip(f"{c.year} counts: " + "; ".join(c.labels), 300))
    return lines


def format_state(ev: EarlyVote, breakdown: str | None = None) -> discord.Embed:
    title = f"Early vote · {ev.name}"
    if breakdown:
        title += f" · by {breakdown}"
    embed = _embed(title, ev.url, ev.updated_at)
    embed.description = _fit(_summary_lines(ev), MAX_DESCRIPTION)
    if breakdown == "party":
        if not ev.by_party:
            _add_field(embed, "By party", [f"civicAPI has no party breakdown for {ev.name}."])
        for category in ("returned", "inperson"):
            tally = ev.tallies.get(category)
            if tally and tally.by_party:
                _add_field(embed, CATEGORY_LABELS[category], _party_lines(tally.by_party, tally.total))
        if ev.by_party and len([c for c in ("returned", "inperson") if c in ev.tallies]) > 1:
            _add_field(embed, "All ballots cast", _party_lines(ev.by_party, ev.total_ballots))
        if ev.comparison and ev.comparison.by_party:
            _add_field(
                embed, f"Same point in {ev.comparison.year}", _party_lines(ev.comparison.by_party, ev.comparison.total)
            )
    elif breakdown == "method":
        for category, total in ev.by_method.items():
            lines = [f"**{_n(total)}**"]
            if category in ("returned", "inperson"):
                lines[0] += f" ({_pct(total, ev.total_ballots)} of ballots cast)"
            if category == "returned" and "requested" in ev.tallies:
                lines.append(f"{_pct(total, ev.tallies['requested'].total)} of requested mail ballots")
            _add_field(embed, CATEGORY_LABELS[category], lines)
    elif breakdown == "age":
        if ev.age:
            whole = sum(ev.age.values())
            covered = " + ".join(CATEGORY_LABELS[c].lower() for c in ev.age_categories)
            lines = [f"{label.removeprefix('Age ').strip()}: {_n(v)} ({_pct(v, whole)})" for label, v in ev.age.items()]
            _add_field(embed, f"By age ({covered})", lines)
        else:
            _add_field(embed, "By age", [f"civicAPI has no age breakdown of ballots cast in {ev.name}."])
    elif breakdown == "county":
        if ev.counties:
            reporting = sum(1 for v in ev.counties.values() if v)
            top = list(ev.counties.items())[:MAX_COUNTIES]
            lines = [
                f"{index}. {clip(county, 60)}: {_n(v)} ({_pct(v, ev.total_ballots)})"
                for index, (county, v) in enumerate(top, 1)
            ]
            _add_field(embed, f"Top counties ({reporting} of {len(ev.counties)} reporting)", lines)
        else:
            _add_field(embed, "By county", ["civicAPI lists no county numbers for this state."])
    return _guard_total(embed)


def format_overview(national: National, states: list[str] | None = None, title: str | None = None) -> discord.Embed:
    """One line per state, most ballots first. ``states`` limits it (the daily post)."""
    codes = [s for s in (states if states is not None else national.states) if s in national.states]
    codes.sort(key=lambda s: (-national.total(s), s))
    embed = _embed(title or "Early vote · United States", page_url(), national.updated_at)
    if not codes:
        embed.description = "civicAPI has no early-vote data for these states yet."
        return embed
    grand = sum(national.total(s) for s in codes)
    header = f"**{_n(grand)}** early ballots cast in {len(codes)} state{'s' if len(codes) != 1 else ''} with data"
    lines = []
    for code in codes:
        tallies = national.states[code]
        parts = [
            f"mail {_n(tallies['returned'].total)}" if "returned" in tallies else "",
            f"in person {_n(tallies['inperson'].total)}" if "inperson" in tallies else "",
        ]
        if not any(parts) and "requested" in tallies:
            parts.append(f"{_n(tallies['requested'].total)} mail ballots requested")
        if "grace" in tallies:
            parts.append(f"grace period {_n(tallies['grace'].total)}")
        detail = " · ".join(p for p in parts if p)
        lines.append(f"**{code}** {_n(national.total(code))}" + (f" ({detail})" if detail else ""))
    body = _fit(lines, MAX_DESCRIPTION - len(header) - 2)
    embed.description = clip(f"{header}\n\n{body}", MAX_DESCRIPTION)
    return _guard_total(embed)


def _guard_total(embed: discord.Embed) -> discord.Embed:
    """Drop trailing fields until the whole embed fits Discord's 6000-character total."""

    def total() -> int:
        return (
            len(embed.title or "")
            + len(embed.description or "")
            + len(embed.footer.text or "")
            + sum(len(f.name) + len(f.value) for f in embed.fields)
        )

    while total() > MAX_EMBED_TOTAL and embed.fields:
        embed.remove_field(len(embed.fields) - 1)
    return embed


def unavailable_embed(state: str | None = None) -> discord.Embed:
    embed = _embed(
        f"Early vote · {STATES.get(state, 'United States') if state else 'United States'}", page_url(state), None
    )
    embed.description = "civicAPI's early-vote data could not be reached. Try again in a few minutes."
    return embed


async def early_vote_embed(client: EarlyVoteClient, state: str | None, breakdown: str | None) -> discord.Embed:
    """The /earlyvote answer. Never raises for a civicAPI failure: says so instead."""
    try:
        if state is None:
            embed = format_overview(await client.national())
            if breakdown and embed.description:
                embed.description = clip(
                    f"{embed.description}\n\nPick a state to see the {breakdown} breakdown.", MAX_DESCRIPTION
                )
            return embed
        ev = await client.state(state, counties=breakdown == "county", age=breakdown == "age")
    except NotCached:
        embed = _embed(
            f"Early vote · {STATES.get(state, 'United States') if state else 'United States'}", page_url(state), None
        )
        embed.description = (
            "Early-vote numbers are still being loaded by the background refresh "
            "(civicAPI pages download one a second). Try again in a few minutes."
        )
        embed.set_footer(text="Early vote: civicAPI")
        return embed
    except SourceUnavailable:
        logger.warning("civicAPI early-vote unavailable", exc_info=True)
        return unavailable_embed(state)
    if ev is None:
        embed = _embed(f"Early vote · {STATES.get(state, state)}", page_url(), None)
        embed.description = (
            f"civicAPI has no early-vote data for {STATES.get(state, state)}. "
            "Some states do not publish early-vote counts."
        )
        embed.set_footer(text="Early vote: civicAPI")
        return embed
    return format_state(ev, breakdown)


# --------------------------------------------------------------------------- daily post
def watched_states(gs: GuildSettings) -> list[str]:
    """States of the server's watched races (or the default races.yaml)."""
    texts = gs.important
    if not texts:
        try:
            texts = [str(race.key) for race in load_races()]
        except (OSError, ValueError):
            logger.exception("Could not load the default races for the early-vote post")
            return []
    states = set()
    for text in texts:
        try:
            states.add(parse(text).state)
        except ValueError:
            continue
    return sorted(s for s in states if s in STATES)


async def post_daily_summaries(bot) -> int:
    """Post one early-vote summary per opted-in server per civicAPI snapshot day.

    Off unless an admin ran ``/earlyvote_posts on``. Runs hourly; ``mark_posted`` keyed by the
    snapshot date makes it post once per day of data, also across restarts. Returns posts sent."""
    guilds = [gs for gs in await bot.store.all_guilds() if gs.post_early_vote and gs.channel_id and not gs.paused]
    if not guilds:
        return 0
    national = await early_vote_client(bot).national()
    if not national.states or national.updated_at is None:
        return 0
    ref = national.updated_at.isoformat()
    sent = 0
    for gs in guilds:
        states = [s for s in watched_states(gs) if s in national.states]
        if not states or await bot.store.last_posted(gs.guild_id, POST_KIND, ref):
            continue
        embed = format_overview(national, states, title=f"Early vote · {ref}")
        try:
            channel = bot.get_channel(gs.channel_id) or await bot.fetch_channel(gs.channel_id)
            await channel.send(embed=embed)
        except discord.DiscordException:
            logger.exception("Could not post the early-vote summary to guild %s", gs.guild_id)
            continue
        await bot.store.mark_posted(gs.guild_id, POST_KIND, ref, datetime.now(timezone.utc))
        sent += 1
    return sent
