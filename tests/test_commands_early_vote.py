"""/earlyvote, its state autocomplete, /earlyvote_posts and the daily summary.

Commands run through ``<command>.callback`` with the recording FakeInteraction, which checks
every message against Discord's limits and the acknowledge-once rule. civicAPI answers come
from tests/fixtures/civicapi_early_vote/ (or synthetic worst cases for the limits).

Checklist
---------
/earlyvote
  [x] no state (guild, DM)      -> national overview, every state with data, attribution
  [x] state by code, lower case, full name; every breakdown
  [x] party breakdown for a state without party data (GA); age without age data (NC)
  [x] invalid state (incl. 6000-char garbage) -> one ephemeral error, no defer
  [x] state without data (NY)   -> "no early-vote data", no crash
  [x] civicAPI down             -> "could not be reached" embed
  [x] breakdown without a state -> overview + hint
  [x] 51 states, 300 counties with 200-char names, 60 parties, huge numbers -> limits held
  [x] flow: defer(ephemeral=True) THEN followup(ephemeral=True)
autocomplete
  [x] only states with data; partial / case-insensitive; names <= 100; <= 25 choices
  [x] cold cache + slow civicAPI -> answers inside Discord's 3 s with every state
daily post
  [x] off by default: no requests; on: one post per snapshot day; paused / no channel skipped
  [x] only the server's watched states; nothing when none of them has data
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

import discord
import httpx
import pytest
from fake_civicapi_early_vote import fixture_client, mock_http_client
from fake_interaction_data import FakeInteraction, check_choices, check_embed, make_client

from electionwatch.bot.discord import (
    early_vote_command,
    early_vote_posts_command,
    early_vote_state_autocomplete,
)
from electionwatch.bot.early_vote import (
    format_overview,
    format_state,
    post_daily_summaries,
    resolve_state,
    state_choices,
)
from electionwatch.racekeys import STATES
from electionwatch.settings import GuildSettings
from electionwatch.sources.early_vote import Comparison, EarlyVote, EarlyVoteClient, National, Tally
from electionwatch.store.memory import MemoryStore


@pytest.fixture
async def client():
    async with mock_http_client() as http:
        bot = make_client(MemoryStore())
        bot.early_vote = fixture_client(http)
        yield bot


def _embed(interaction: FakeInteraction) -> discord.Embed:
    reply = interaction.assert_ephemeral_flow()
    assert interaction.kinds == ["defer", "followup"]
    assert reply.embed is not None
    return reply.embed


def _text(embed: discord.Embed) -> str:
    return "\n".join(
        [embed.title or "", embed.description or "", embed.footer.text or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )


# --------------------------------------------------------------------------- /earlyvote
@pytest.mark.parametrize("guild_id", [1, None])
async def test_overview_lists_every_state_with_data(client, guild_id):
    interaction = FakeInteraction(client, guild_id=guild_id)
    await early_vote_command.callback(interaction)
    embed = _embed(interaction)
    assert embed.title == "Early vote · United States"
    assert embed.url == "https://civicapi.org/early-vote"
    assert embed.footer.text == "Early vote: civicAPI · updated 2026-10-27"
    assert embed.description.index("**TX**") < embed.description.index("**NC**")  # most ballots first
    assert "**IL** 0 (grace period 2,513)" in embed.description


@pytest.mark.parametrize("state", ["NC", "nc", "North Carolina", "  north   carolina "])
@pytest.mark.parametrize("breakdown", [None, "party", "method", "age", "county"])
async def test_state_in_any_spelling_and_every_breakdown(client, state, breakdown):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, state, breakdown)
    embed = _embed(interaction)
    assert embed.title.startswith("Early vote · North Carolina")
    assert embed.url == "https://civicapi.org/early-vote/nc"
    assert "civicAPI" in embed.footer.text and "2026-10-27" in embed.footer.text
    assert "**1,773,889** early ballots cast" in embed.description
    assert "Same point in 2022 (2022-11-01): 1,296,034 (now +36.9%)" in embed.description
    expected = {
        None: [],
        "party": ["Mail ballots returned", "In-person early votes", "All ballots cast", "Same point in 2022"],
        "method": ["Mail ballots returned", "In-person early votes", "Mail ballots requested"],
        "age": ["By age"],
        "county": ["Top counties (6 of 6 reporting)", "One county"],
    }[breakdown]
    assert [field.name for field in embed.fields] == expected


async def test_missing_breakdowns_say_so(client):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "GA", "party")
    assert "no party breakdown for Georgia" in _text(_embed(interaction))
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "NC", "age")
    assert "no age breakdown of ballots cast in North Carolina" in _text(_embed(interaction))
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "GA", "age")
    assert "By age (in-person early votes)" in _text(_embed(interaction))


@pytest.mark.parametrize("state", ["ZZ", "Narnia", "PR", "x" * 6000, "", "N Q"])
async def test_invalid_state_is_one_ephemeral_error_without_defer(client, state):
    interaction = FakeInteraction(client)
    if state == "":
        await early_vote_command.callback(interaction, state)  # empty = overview
        assert interaction.kinds == ["defer", "followup"]
        return
    await early_vote_command.callback(interaction, state)
    reply = interaction.assert_ephemeral_flow()
    assert interaction.kinds == ["send_message"]
    assert "Unknown state" in reply.content and len(reply.content) < 200


async def test_state_without_data(client):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "New York", "county")
    embed = _embed(interaction)
    assert "civicAPI has no early-vote data for New York" in embed.description
    assert "civicAPI" in embed.footer.text and embed.url.startswith("https://civicapi.org/early-vote")


async def test_civicapi_down_is_reported_not_raised():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(502))) as http:
        bot = make_client(MemoryStore())
        bot.early_vote = EarlyVoteClient(http, min_interval=0)
        for state in (None, "NC"):
            interaction = FakeInteraction(bot)
            await early_vote_command.callback(interaction, state)
            assert "could not be reached" in _embed(interaction).description


async def test_breakdown_without_state_hints(client):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, None, "county")
    assert "Pick a state to see the county breakdown." in _embed(interaction).description


async def test_client_is_created_from_the_bot_http_client_when_missing():
    async with mock_http_client() as http:
        bot = make_client(MemoryStore())
        bot.http_client = http
        from electionwatch.bot.early_vote import early_vote_client

        created = early_vote_client(bot)
        assert created is early_vote_client(bot) and created.client is http


def test_resolve_state():
    assert resolve_state("dc") == "DC" and resolve_state("District of Columbia") == "DC"
    assert all(resolve_state(name) == code for code, name in STATES.items())
    with pytest.raises(ValueError):
        resolve_state("GU")


# --------------------------------------------------------------------------- limits
def _huge_state(parties: int = 60, counties: int = 300) -> EarlyVote:
    party = {f"Party {'X' * 150} {i}": 10**12 + i for i in range(parties)}
    tallies = {
        category: Tally(category, 10**15, dict(party), "L" * 300)
        for category in ("returned", "inperson", "requested", "grace")
    }
    return EarlyVote(
        "NC",
        tallies,
        date(2026, 10, 27),
        "https://civicapi.org/early-vote/nc",
        comparison=Comparison(2022, date(2022, 11, 1), 10**14, dict(party), ("L" * 500,) * 5),
        counties={f"{'County ' * 30}{i}": 10**12 - i for i in range(counties)},
        age={f"Age {'9' * 200} {i}": 10**9 for i in range(80)},
        age_categories=("returned", "inperson"),
    )


@pytest.mark.parametrize("breakdown", [None, "party", "method", "age", "county"])
def test_state_embed_fits_discord_limits(breakdown):
    embed = format_state(_huge_state(), breakdown)
    check_embed(embed)
    assert embed.footer.text.startswith("Early vote: civicAPI")


def test_overview_fits_with_every_state_and_huge_numbers():
    tallies = {category: Tally(category, 10**15) for category in ("returned", "inperson", "requested", "grace")}
    national = National({code: dict(tallies) for code in STATES}, date(2026, 10, 27))
    embed = format_overview(national)
    check_embed(embed)
    assert "more" in embed.description or embed.description.count("**") >= 2 * len(STATES)


# --------------------------------------------------------------------------- autocomplete
@pytest.mark.parametrize(
    "current,expected",
    [
        ("", None),
        ("n", "NC"),
        ("NORTH", "NC"),
        ("georgia", "GA"),
        ("carolina", "NC"),
        ("new y", None),
    ],
)
async def test_autocomplete_offers_states_with_data(client, current, expected):
    choices = await early_vote_state_autocomplete(FakeInteraction(client), current)
    check_choices(choices)
    values = [choice.value for choice in choices]
    assert "NY" not in values and "AL" not in values  # no civicAPI data
    if expected:
        assert expected in values
    if current == "new y":
        assert values == []


def test_autocomplete_choices_cap_at_25_and_100_chars():
    choices = state_choices(list(STATES) * 3, "")
    assert len(choices) == 25
    assert all(len(name) <= 100 and len(value) <= 100 for name, value in choices)
    assert state_choices(["NC", "SC", "NV"], "nc")[0] == ("North Carolina (NC)", "NC")


async def test_autocomplete_answers_in_time_with_a_slow_cold_civicapi(monkeypatch):
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return httpx.Response(200, json={})

    monkeypatch.setattr("electionwatch.bot.discord.AUTOCOMPLETE_TIMEOUT", 0.05)
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        bot = make_client(MemoryStore())
        bot.early_vote = EarlyVoteClient(http, min_interval=0)
        started = time.monotonic()
        choices = await early_vote_state_autocomplete(FakeInteraction(bot), "")
        assert time.monotonic() - started < 1
        check_choices(choices)
        assert len(choices) == 25  # fallback: every state, capped
        gate.set()
        await bot.early_vote._warming


# --------------------------------------------------------------------------- daily post
class Channel:
    def __init__(self) -> None:
        self.embeds: list[discord.Embed] = []

    async def send(self, *, embed: discord.Embed) -> None:
        check_embed(embed)
        self.embeds.append(embed)


class PostBot:
    def __init__(self, store, early_vote) -> None:
        self.store, self.early_vote = store, early_vote
        self.channels: dict[int, Channel] = {}

    def get_channel(self, channel_id):
        return self.channels.setdefault(channel_id, Channel())

    async def fetch_channel(self, channel_id):  # pragma: no cover - get_channel always answers
        return self.get_channel(channel_id)


async def test_daily_post_is_off_by_default_and_makes_no_requests():
    assert GuildSettings(1).post_early_vote is False
    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        store = MemoryStore()
        await store.save_guild(GuildSettings(1, channel_id=10, important=["NC SEN"]))
        bot = PostBot(store, EarlyVoteClient(http, min_interval=0))
        assert await post_daily_summaries(bot) == 0
    assert calls == [] and bot.channels == {}


async def test_daily_post_once_per_snapshot_for_watched_states_only():
    async with mock_http_client() as http:
        store = MemoryStore()
        await store.save_guild(
            GuildSettings(1, channel_id=10, important=["NC SEN", "GA-06", "NY-17"], post_early_vote=True)
        )
        await store.save_guild(GuildSettings(2, channel_id=20, important=["NC SEN"], post_early_vote=True, paused=True))
        await store.save_guild(GuildSettings(3, important=["NC SEN"], post_early_vote=True))  # no channel
        await store.save_guild(GuildSettings(4, channel_id=40, important=["NY-17"], post_early_vote=True))
        bot = PostBot(store, fixture_client(http))
        assert await post_daily_summaries(bot) == 1
        assert await post_daily_summaries(bot) == 0  # same snapshot day: no repeat
    assert set(bot.channels) == {10}
    (embed,) = bot.channels[10].embeds
    assert embed.title == "Early vote · 2026-10-27" and embed.url == "https://civicapi.org/early-vote"
    assert "**NC**" in embed.description and "**GA**" in embed.description
    assert "**TX**" not in embed.description and "NY" not in embed.description
    assert "in 2 states with data" in embed.description
    assert await store.last_posted(1, "early_vote", "2026-10-27") is not None


async def test_toggle_command_saves_the_setting():
    store = MemoryStore()
    bot = make_client(store)
    interaction = FakeInteraction(bot)
    interaction.user.guild_permissions = discord.Permissions(manage_guild=True)
    await early_vote_posts_command.callback(interaction, "on")
    assert interaction.assert_ephemeral_flow().content == "✅ early vote on"
    assert (await store.get_guild(1)).post_early_vote is True
