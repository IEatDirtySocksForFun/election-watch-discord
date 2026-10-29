"""/earlyvote by county: the party split per county, and one county's own numbers."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fake_civicapi_early_vote import fixture_client, mock_http_client
from fake_interaction_data import FakeInteraction, check_choices, check_embed, make_client

from electionwatch.bot.discord import early_vote_command, early_vote_county_autocomplete
from electionwatch.bot.early_vote import county_choices, format_county, format_state, resolve_county
from electionwatch.sources.early_vote import EarlyVote, Tally
from electionwatch.store.memory import MemoryStore


@pytest.fixture
async def client():
    async with mock_http_client() as http:
        bot = make_client(MemoryStore())
        bot.early_vote = fixture_client(http)
        yield bot


def _text(embed) -> str:
    return "\n".join(
        [embed.title or "", embed.description or "", embed.footer.text or ""]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )


async def test_the_county_breakdown_shows_each_countys_party_split(client):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "NC", "county")
    text = _text(interaction.assert_ephemeral_flow().embed)
    # Durham's returned mail ballots: 1,362 Dem, 163 Rep, 1,083 other of 2,608 (plus in person).
    assert "Durham:" in text and "· 🔵 Dem " in text and "· 🔴 Rep " in text and "· ⚪ other " in text
    assert "Add `county:` to /earlyvote" in text


@pytest.mark.parametrize("typed", ["Durham", "durham county", "DURHAM", "durhm"])
async def test_one_county_in_any_spelling(client, typed):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, "north carolina", None, typed)
    embed = interaction.assert_ephemeral_flow().embed
    check_embed(embed)
    assert embed.title == "📬 Early vote · Durham, North Carolina"
    assert "early ballots cast" in embed.description and "of North Carolina's" in embed.description
    assert [field.name for field in embed.fields] == ["🏷️ By party registration"]
    assert "🔵 Democratic:" in embed.fields[0].value and "🔴 Republican:" in embed.fields[0].value


@pytest.mark.parametrize(
    "state, county, message",
    [("NC", "Narnia", "no county called 'Narnia'"), (None, "Durham", "Pick a state too")],
)
async def test_a_county_that_isnt_there_is_one_error(client, state, county, message):
    interaction = FakeInteraction(client)
    await early_vote_command.callback(interaction, state, None, county)
    reply = interaction.assert_ephemeral_flow()
    assert interaction.kinds == ["defer", "followup"]
    assert message in reply.content


async def test_county_autocomplete_follows_the_state_already_picked(client):
    interaction = FakeInteraction(client)
    interaction.namespace = SimpleNamespace(state="nc")
    choices = await early_vote_county_autocomplete(interaction, "dur")
    check_choices(choices)
    assert [choice.value for choice in choices] == ["Durham"]
    interaction.namespace = SimpleNamespace(state="")
    assert await early_vote_county_autocomplete(interaction, "dur") == []


def _big(counties: int = 300) -> EarlyVote:
    names = {f"{'County ' * 30}{index}": 10**12 - index for index in range(counties)}
    parties = {"Democratic": 10**11, "Republican": 10**11, "No Party Affiliation": 10**11}
    return EarlyVote(
        "NC",
        {"returned": Tally("returned", 10**15, dict(parties))},
        date(2026, 10, 27),
        "https://civicapi.org/early-vote/nc",
        counties=names,
        county_parties={name: dict(parties) for name in names},
        county_methods={name: {"returned": votes} for name, votes in names.items()},
    )


def test_county_embeds_fit_discord_limits():
    ev = _big()
    check_embed(format_state(ev, "county"))
    county = next(iter(ev.counties))
    check_embed(format_county(ev, county))
    assert resolve_county(ev, county.upper()) == county
    check_choices([SimpleNamespace(name=name, value=value) for name, value in county_choices(ev, "")])
    assert len(county_choices(ev, "")) == 25
