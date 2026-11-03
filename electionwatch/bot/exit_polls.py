"""Exit-poll embeds for /exitpoll: one race, one state, or every released exit poll.

The command reads the exit polls the background refresher stored (CNN's National Election
Pool files, electionwatch/sources/exit_polls_cnn.py). A race shows the headline questions
(gender, age, race, education, party, ideology, top issue); a state shows each of its races
more briefly; "all" shows one line per released race. Every embed names CNN and links its page.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from electionwatch.bot import emoji
from electionwatch.bot.poster import MAX_DESCRIPTION, MAX_EMBED_TOTAL, MAX_FIELD_VALUE, clip
from electionwatch.racekeys import STATES, format_key, parse
from electionwatch.sources.exit_polls_cnn import NATIONAL, stored_index, stored_polls

EXIT_POLL = "🗳️"
SOURCE = "CNN exit poll (National Election Pool: ABC, CBS, CNN, NBC)"
#: Headline questions, in display order; the first question whose text matches is shown.
HEADLINES = (
    "Gender",
    "Age",
    "Race",
    "Education",
    "Party ID",
    "Ideology",
    "Most important issue",
    "Opinion of Trump as president",
)
MAX_EMBEDS = 10  # per message


def _norm(text: str) -> str:
    return " ".join(text.replace("’", "'").split()).casefold()


def headline_questions(poll: dict, limit: int) -> list[dict]:
    """The headline questions this poll has, at most ``limit``; then others to fill up."""
    picked: list[dict] = []
    seen: set[str] = set()
    for wanted in HEADLINES:
        for question in poll.get("questions") or []:
            if _norm(question["q"]) == _norm(wanted) and _norm(question["q"]) not in seen:
                picked.append(question)
                seen.add(_norm(question["q"]))
                break
    return picked[:limit]


def _candidates(poll: dict) -> list[str]:
    return [f"{emoji.party(party)} {name}".strip() for name, party in poll.get("candidates") or []]


def _answer_line(poll: dict, answer: dict) -> str:
    names = [name for name, _ in poll.get("candidates") or []]
    shares = [f"{name} {pct}" for name, pct in zip(names, answer.get("by") or []) if pct is not None]
    share = f" ({answer['share']}%)" if answer.get("share") is not None else ""
    return f"**{answer['a']}**{share}: " + (" · ".join(shares) if shares else "n/a")


def _footer(poll: dict) -> str:
    parts = [SOURCE]
    if poll.get("respondents"):
        parts.append(f"{poll['respondents']:,} respondents")
    if poll.get("updated"):
        try:
            when = datetime.fromisoformat(str(poll["updated"]).replace("Z", "+00:00"))
            parts.append("updated " + when.astimezone(timezone.utc).strftime("%b %d %H:%M UTC"))
        except ValueError:
            pass
    return " · ".join(parts)


def race_embed(poll: dict, questions: int = 8) -> discord.Embed:
    embed = discord.Embed(
        title=clip(f"{EXIT_POLL} Exit poll · {poll['label']}", 256),
        url=poll.get("url"),
        description=clip("Candidates: " + ", ".join(_candidates(poll)), MAX_DESCRIPTION),
        color=discord.Color.dark_teal(),
    )
    for question in headline_questions(poll, questions):
        lines = [_answer_line(poll, answer) for answer in question["answers"]]
        embed.add_field(name=clip(question["q"], 256), value=clip("\n".join(lines), MAX_FIELD_VALUE), inline=False)
    embed.set_footer(text=_footer(poll))
    return embed


def summary_line(poll: dict) -> str:
    """'**Pennsylvania Governor** (2,954): Male: Shapiro 55, Garrity 43 | Female: ...'"""
    gender = next((q for q in poll.get("questions") or [] if _norm(q["q"]) == "gender"), None)
    head = f"[**{poll['label']}**]({poll['url']})" if poll.get("url") else f"**{poll['label']}**"
    if poll.get("respondents"):
        head += f" ({poll['respondents']:,})"
    if gender is None:
        return head
    names = [name for name, _ in poll.get("candidates") or []]
    parts = []
    for answer in gender["answers"][:2]:
        shares = ", ".join(f"{name} {pct}" for name, pct in zip(names[:2], answer.get("by") or []) if pct is not None)
        parts.append(f"{answer['a']}: {shares}" if shares else answer["a"])
    return head + ": " + " | ".join(parts)


def _order(poll: dict) -> tuple:
    return (poll.get("ident") != NATIONAL, poll.get("label", ""))


async def not_out_yet(store, scope: str) -> str:
    """The reply when nothing in the scope is released: the next release times we know."""
    index = await stored_index(store)
    upcoming = sorted((race["release"], race["label"]) for race in index if race.get("release"))
    text = f"No exit polls out yet for {scope}. They are released when each state's polls close."
    if upcoming:
        when = ", ".join(f"{label} <t:{int(release)}:t>" for release, label in upcoming[:5])
        text += f" Next: {when}."
    return text


async def exit_poll_embeds(store, race: str | None = None, state: str | None = None) -> list[discord.Embed] | str:
    """Embeds for /exitpoll, or a message when there is nothing to show yet.
    ``race`` (a stored ident such as 'PA GOV') wins over ``state``; neither = all."""
    if race:
        polls = await stored_polls(store, [race])
        if not polls:
            return await not_out_yet(store, race)
        return [race_embed(polls[0])]
    polls = sorted(await stored_polls(store), key=_order)
    if state:
        polls = [poll for poll in polls if poll.get("state") == state]
        if not polls:
            return await not_out_yet(store, STATES.get(state, state))
        return [race_embed(poll, questions=4) for poll in polls[:MAX_EMBEDS]]
    if not polls:
        return await not_out_yet(store, "any race")
    embeds: list[discord.Embed] = []
    lines: list[str] = []
    for line in (summary_line(poll) for poll in polls):
        if len("\n".join([*lines, line])) > MAX_DESCRIPTION - 100:
            embeds.append(_all_embed(lines, len(embeds)))
            lines = []
        lines.append(line)
    if lines:
        embeds.append(_all_embed(lines, len(embeds)))
    return embeds


def _all_embed(lines: list[str], page: int) -> discord.Embed:
    title = f"{EXIT_POLL} Exit polls released" + (f" (cont. {page + 1})" if page else "")
    embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.dark_teal())
    embed.set_footer(text=SOURCE + " · gender split shown; pick a race or state for more")
    return embed


def batches(embeds: list[discord.Embed]) -> list[list[discord.Embed]]:
    """Group embeds into messages within Discord's 10-embed and 6,000-character limits."""
    out: list[list[discord.Embed]] = []
    size = 0
    for embed in embeds:
        length = len(embed)
        if not out or len(out[-1]) >= MAX_EMBEDS or size + length > MAX_EMBED_TOTAL:
            out.append([])
            size = 0
        out[-1].append(embed)
        size += length
    return out


_NATIONAL_NAMES = {"us house", "u.s. house", "house", "national", "national house", "u.s. house (national)"}


async def resolve_race(store, text: str) -> str:
    """'PA GOV', 'Pennsylvania Governor', 'national house' -> the stored ident.
    ValueError when CNN has no exit poll for that race."""
    needle = _norm(text)
    races = await stored_index(store)
    for race in races:
        if needle in (_norm(race["ident"]), _norm(race["label"])):
            return race["ident"]
    if needle in _NATIONAL_NAMES:
        return NATIONAL
    try:
        ident = format_key(parse(text))
    except Exception:
        ident = None
    if not races:  # the index is not stored yet: take the race as typed
        return ident or text.strip()
    if ident and any(race["ident"] == ident for race in races):
        return ident
    raise ValueError(
        f"No exit poll for {text}: CNN polls the governor and Senate races of most states and the national House vote."
    )


async def race_choices(store, current: str) -> list[tuple[str, str]]:
    """(label, ident) pairs for autocomplete: every race CNN lists, released or not."""
    needle = current.casefold().strip()
    races = await stored_index(store)
    picked = [
        (race["label"], race["ident"])
        for race in races
        if not needle or needle in race["label"].casefold() or needle in race["ident"].casefold()
    ]
    return sorted(picked, key=lambda pair: (pair[1] != NATIONAL, pair[0]))[:25]


async def state_choices(store, current: str) -> list[tuple[str, str]]:
    needle = current.casefold().strip()
    states = sorted({race["state"] for race in await stored_index(store) if race["state"] in STATES})
    picked = [
        (STATES[code], code)
        for code in states
        if not needle or needle in STATES[code].casefold() or needle == code.casefold()
    ]
    return picked[:25]
