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
    ordered = sorted(
        parties.items(), key=lambda party_votes: (_PARTY_ORDER.get(party_votes[0], 2), -party_votes[1], party_votes[0])
    )
    return [f"{party}: {_n(votes)} ({_pct(votes, whole)})" for party, votes in ordered]


def _party_split(parties: dict[str, int], whole: int) -> str:
    """'Dem 42% · Rep 31% · other 27%' for one county; '' without party data."""
    if not parties or not whole:
        return ""
    short = {"Democratic": "Dem", "Republican": "Rep"}
    shown = [f"{short[party]} {votes / whole:.0%}" for party, votes in parties.items() if party in short]
    others = whole - sum(votes for party, votes in parties.items() if party in short)
    if others > 0 and shown:
        shown.append(f"other {others / whole:.0%}")
    return " · ".join(sorted(shown, key=lambda part: {"Dem": 0, "Rep": 1}.get(part[:3], 2)))


def _county_key(name: str) -> str:
    cleaned = name.casefold().replace(".", "").replace("'", "").replace("-", " ")
    for suffix in (" county", " parish", " borough", " census area", " city and borough", " municipality"):
        cleaned = cleaned.removesuffix(suffix)
    words = ["st" if word == "saint" else word for word in cleaned.split()]
    return " ".join(words)


def resolve_county(ev: EarlyVote, text: str) -> str:
    """'wake', 'Wake County', 'St. Louis', 'wkae' -> the county's name in civicAPI's data.
    ValueError when the state lists no such county (or a misspelling fits several)."""
    import difflib

    names = {_county_key(county): county for county in ev.counties}
    wanted = _county_key(text)
    if wanted in names:
        return names[wanted]
    scored = sorted(((difflib.SequenceMatcher(None, wanted, key).ratio(), key) for key in names), reverse=True)
    if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
        return names[scored[0][1]]
    raise ValueError(f"civicAPI lists no county called {clip(text.strip(), 60)!r} in {ev.name}'s early vote.")


def county_choices(ev: EarlyVote | None, current: str) -> list[tuple[str, str]]:
    """Autocomplete (name, value) pairs for a state's counties, most ballots first."""
    if ev is None:
        return []
    needle = _county_key(current)
    counties = list(ev.counties)
    starts = [county for county in counties if _county_key(county).startswith(needle)]
    contains = [county for county in counties if needle and needle in _county_key(county) and county not in starts]
    if not starts and not contains and needle:
        try:
            starts = [resolve_county(ev, current)]
        except ValueError:
            pass
    return [(clip(county, 100), clip(county, 100)) for county in (starts + contains)[:MAX_CHOICES]]


def format_county(ev: EarlyVote, county: str) -> discord.Embed:
    """One county's early vote: ballots, mail / in person and party registration."""
    total = ev.counties.get(county, 0)
    embed = _embed(f"Early vote · {county}, {ev.name}", ev.url, ev.updated_at)
    lines = [f"**{_n(total)}** early ballots cast (mail returned + in person)"]
    if ev.total_ballots:
        lines[0] += f", {_pct(total, ev.total_ballots)} of {ev.name}'s"
    methods = ev.county_methods.get(county, {})
    cast = [
        f"{label}: {_n(methods[category])}"
        for category, label in (("returned", "Mail"), ("inperson", "In person"), ("grace", "Grace period"))
        if category in methods
    ]
    if cast:
        lines.append(" · ".join(cast))
    if ev.counties:
        rank = list(ev.counties).index(county) + 1 if county in ev.counties else None
        if rank:
            lines.append(f"#{rank} of {len(ev.counties)} counties by ballots cast")
    embed.description = _fit(lines, MAX_DESCRIPTION)
    parties = ev.county_parties.get(county, {})
    if parties:
        _add_field(embed, "By party registration", _party_lines(parties, total))
    else:
        _add_field(embed, "By party registration", [f"civicAPI has no party breakdown for {ev.name}."])
    return _guard_total(embed)


def _footer(updated: date | None) -> str:
    return f"Early vote: civicAPI · updated {updated.isoformat() if updated else 'date unknown'}"


def _fit(lines: list[str], limit: int, more: str = "… and {n} more") -> str:
    """Join whole lines up to ``limit`` characters, ending with a count of what was left out."""
    kept_lines: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        tail = len(more.format(n=len(lines) - index)) + 1
        cost = len(line) + (1 if kept_lines else 0)
        if used + cost + (tail if index < len(lines) - 1 else 0) > limit:
            kept_lines.append(more.format(n=len(lines) - index))
            break
        kept_lines.append(line)
        used += cost
    return clip("\n".join(kept_lines), limit)


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
        f"{label}: {_n(method[category])}"
        for category, label in (("returned", "Mail"), ("inperson", "In person"))
        if category in method
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
            f"{party} {_pct(votes, ev.total_ballots)}"
            for party, votes in sorted(
                parties.items(), key=lambda party_votes: (_PARTY_ORDER.get(party_votes[0], 2), -party_votes[1])
            )
        )
        lines.append(f"By party registration: {shares}")
    if ev.comparison:
        comparison = ev.comparison
        change = f" (now {(ev.total_ballots - comparison.total) / comparison.total:+.1%})" if comparison.total else ""
        lines.append(f"Same point in {comparison.year} ({comparison.on.isoformat()}): {_n(comparison.total)}{change}")
        if comparison.labels:
            # Past years can count differently (NC 2022 "returned" includes one-stop voting).
            lines.append(clip(f"{comparison.year} counts: " + "; ".join(comparison.labels), 300))
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
        if ev.by_party and sum(category in ev.tallies for category in ("returned", "inperson")) > 1:
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
            covered = " + ".join(CATEGORY_LABELS[category].lower() for category in ev.age_categories)
            lines = [
                f"{label.removeprefix('Age ').strip()}: {_n(ballots)} ({_pct(ballots, whole)})"
                for label, ballots in ev.age.items()
            ]
            _add_field(embed, f"By age ({covered})", lines)
        else:
            _add_field(embed, "By age", [f"civicAPI has no age breakdown of ballots cast in {ev.name}."])
    elif breakdown == "county":
        if ev.counties:
            reporting = sum(1 for count in ev.counties.values() if count)
            top = list(ev.counties.items())[:MAX_COUNTIES]
            lines = []
            for index, (county, ballots) in enumerate(top, 1):
                line = f"{index}. {clip(county, 60)}: {_n(ballots)} ({_pct(ballots, ev.total_ballots)})"
                split = _party_split(ev.county_parties.get(county, {}), ballots)
                lines.append(f"{line} · {split}" if split else line)
            # A field holds 1024 characters: with the party split, 25 counties need two.
            half = (len(lines) + 1) // 2 if len(lines) > 12 else len(lines)
            name = f"Top counties ({reporting} of {len(ev.counties)} reporting)"
            _add_field(embed, name, lines[:half])
            if lines[half:]:
                _add_field(embed, "\u200b", lines[half:])  # no heading on the second half
            _add_field(embed, "One county", ["Add `county:` to /earlyvote for a county's own numbers."])
        else:
            _add_field(embed, "By county", ["civicAPI lists no county numbers for this state."])
    return _guard_total(embed)


def format_overview(national: National, states: list[str] | None = None, title: str | None = None) -> discord.Embed:
    """One line per state, most ballots first. ``states`` limits it (the daily post)."""
    codes = [code for code in (states if states is not None else national.states) if code in national.states]
    codes.sort(key=lambda code: (-national.total(code), code))
    embed = _embed(title or "Early vote · United States", page_url(), national.updated_at)
    if not codes:
        embed.description = "civicAPI has no early-vote data for these states yet."
        return embed
    grand = sum(national.total(code) for code in codes)
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
        detail = " · ".join(piece for piece in parts if piece)
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
            + sum(len(field.name) + len(field.value) for field in embed.fields)
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


async def early_vote_embed(
    client: EarlyVoteClient, state: str | None, breakdown: str | None, county: str | None = None
) -> discord.Embed:
    """The /earlyvote answer. Never raises for a civicAPI failure: says so instead. A county
    the state doesn't list raises ValueError (the command shows it as an error)."""
    if county and state is None:
        raise ValueError("Pick a state too: county numbers are per state.")
    try:
        if state is None:
            embed = format_overview(await client.national())
            if breakdown and embed.description:
                embed.description = clip(
                    f"{embed.description}\n\nPick a state to see the {breakdown} breakdown.", MAX_DESCRIPTION
                )
            return embed
        ev = await client.state(state, counties=breakdown == "county" or bool(county), age=breakdown == "age")
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
    if county:
        return format_county(ev, resolve_county(ev, county))
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
    return sorted(state for state in states if state in STATES)


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
    snapshot_day = national.updated_at.isoformat()
    sent = 0
    for gs in guilds:
        states = [state for state in watched_states(gs) if state in national.states]
        if not states or await bot.store.last_posted(gs.guild_id, POST_KIND, snapshot_day):
            continue
        embed = format_overview(national, states, title=f"Early vote · {snapshot_day}")
        try:
            channel = bot.get_channel(gs.channel_id) or await bot.fetch_channel(gs.channel_id)
            await channel.send(embed=embed)
        except discord.DiscordException:
            logger.exception("Could not post the early-vote summary to guild %s", gs.guild_id)
            continue
        await bot.store.mark_posted(gs.guild_id, POST_KIND, snapshot_day, datetime.now(timezone.utc))
        sent += 1
    return sent
