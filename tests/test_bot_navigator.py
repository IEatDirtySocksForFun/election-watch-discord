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
    labels = {child.label for child in view.children if isinstance(child, discord.ui.Button)}
    assert {"Odds", "Polls", "Choose race"} <= labels
    assert not any(
        isinstance(child, discord.ui.Select) and child.placeholder == "Race" for child in view.children
    )


@pytest.mark.asyncio
async def test_race_listing_is_cached_and_a_slow_source_cannot_blow_the_deadline(monkeypatch):
    import asyncio
    import time

    from electionwatch.bot import navigator

    navigator._list_cache.clear()
    monkeypatch.setattr(navigator, "LIST_TIMEOUT_SECONDS", 0.05)

    class Counting(ListSource):
        name = "counting"
        hits = 0

        async def list_races(self, state, client):
            Counting.hits += 1
            return await super().list_races(state, client)

    class Slow(ListSource):
        name = "slow"

        async def list_races(self, state, client):
            await asyncio.sleep(5)
            return []

    client = SimpleNamespace(
        store=MemoryStore(), sources={"results": [Slow(), Counting()]}, http_client=object(), poller=None
    )
    started = time.monotonic()
    found = await navigator.list_races(client, "TX")
    found = await navigator.list_races(client, "TX")
    assert time.monotonic() - started < 1
    assert set(found) == {"TX SEN", "TX GOV"}
    assert Counting.hits == 1
    navigator._list_cache.clear()


@pytest.mark.asyncio
async def test_navigator_clicks_defer_before_listing_races():
    from electionwatch.bot import navigator

    navigator._list_cache.clear()
    calls = []

    class Recording(ListSource):
        async def list_races(self, state, client):
            calls.append("list")
            return await super().list_races(state, client)

    async def defer(*args, **kwargs):
        calls.append("defer")

    async def edit_original_response(**kwargs):
        calls.append("edit")

    client = SimpleNamespace(
        store=MemoryStore(), sources={"results": [Recording()]}, http_client=object(), poller=None
    )
    view = RaceNavigator(client, 9, UserSettings(9))
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=9),
        data={"values": ["TX"]},
        response=SimpleNamespace(defer=defer),
        edit_original_response=edit_original_response,
    )
    await view._state_callback(interaction)
    assert calls == ["defer", "list", "edit"]
    navigator._list_cache.clear()
