from types import SimpleNamespace

import discord
import pytest

from electionwatch.bot.navigator import RaceNavigator
from electionwatch.models import CandidateResult, RaceResult
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import UserSettings
from electionwatch.sources.base import ResultsSource
from electionwatch.store.memory import MemoryStore


class ListSource(ResultsSource):
    name = "list"
    states = frozenset({"TX"})

    async def list_races(self, state, client):
        return [(parse("TX SEN"), "Texas Senate"), (parse("TX GOV"), "Texas Governor")]


@pytest.mark.asyncio
async def test_navigator_lists_races_and_all_states_within_discord_limits():
    from datetime import datetime, timezone

    store = MemoryStore()
    key = parse("TX SEN")
    await store.upsert_result(
        RaceResult(key, [CandidateResult("Doe", "DEM", 10, 100.0)], "test", datetime.now(timezone.utc))
    )
    client = SimpleNamespace(
        store=store, sources={"results": [ListSource()]}, http_client=object(), poller=None
    )
    view = RaceNavigator(client, 9, UserSettings(9))
    await view.refresh()

    assert view.selected_race == "TX SEN"
    state_options = [
        option.value
        for child in view.children
        if isinstance(child, discord.ui.Select) and child.placeholder.startswith("State:")
        for option in child.options
    ]
    assert set(state_options) == set(STATES)
    assert all(len(child.options) <= 25 for child in view.children if isinstance(child, discord.ui.Select))
    assert all(child.row <= 4 for child in view.children)
    embed = await view.embed()
    assert embed.title == "Texas Senate" and "Doe" in embed.description
