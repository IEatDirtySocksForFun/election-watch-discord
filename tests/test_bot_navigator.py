from types import SimpleNamespace

import discord
import pytest

from electionwatch.bot.navigator import RaceNavigator
from electionwatch.models import CandidateResult, RaceResult
from electionwatch.racekeys import ALL_JURISDICTIONS, parse
from electionwatch.racelists import RaceLists
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
    client = SimpleNamespace(store=store, sources={"results": [ListSource()]}, http_client=object(), poller=None)
    await RaceLists(store, client.sources, client.http_client).refresh()  # the background refresh
    view = RaceNavigator(client, 9, UserSettings(9))
    await view.refresh()

    assert view.selected_race == "TX SEN"
    state_options = [
        option.value
        for child in view.children
        if isinstance(child, discord.ui.Select) and child.placeholder.startswith("State:")
        for option in child.options
    ]
    assert set(state_options) == set(ALL_JURISDICTIONS)
    assert all(len(child.options) <= 25 for child in view.children if isinstance(child, discord.ui.Select))
    assert all(child.row <= 4 for child in view.children)
    embed = await view.embed()
    assert embed.title == "Texas Senate" and "Doe" in embed.description
    labels = {child.label for child in view.children if isinstance(child, discord.ui.Button)}
    assert {"Betting markets", "Polls", "Choose race"} <= labels
    assert not any(
        isinstance(child, discord.ui.Select) and child.placeholder.startswith("Race") for child in view.children
    )


@pytest.mark.asyncio
async def test_commands_never_call_a_source_and_a_slow_source_cannot_blow_the_deadline():
    import asyncio
    import time

    from electionwatch.bot import navigator

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

    store = MemoryStore()
    client = SimpleNamespace(store=store, sources={"results": [Slow(), Counting()]}, http_client=object(), poller=None)
    # Before any refresh: nothing stored, nothing asked.
    assert await navigator.list_races(client, "TX") == {}
    assert Counting.hits == 0
    # The background refresh times the slow source out and stores the other one's list.
    refresher = RaceLists(store, client.sources, client.http_client, timeout=0.05)
    assert await refresher.refresh() == 1
    assert Counting.hits == 1
    started = time.monotonic()
    for _ in range(5):
        found = await navigator.list_races(client, "TX")
    assert time.monotonic() - started < 1
    assert set(found) == {"TX SEN", "TX GOV"}
    assert Counting.hits == 1


@pytest.mark.asyncio
async def test_navigator_clicks_defer_before_reading_race_lists():
    calls = []

    class Recording(ListSource):
        async def list_races(self, state, client):
            calls.append("list")
            return await super().list_races(state, client)

    class RecordingStore(MemoryStore):
        async def get_cached(self, kind, ident):
            calls.append("read")
            return await super().get_cached(kind, ident)

    async def defer(*args, **kwargs):
        calls.append("defer")

    async def edit_original_response(**kwargs):
        calls.append("edit")

    client = SimpleNamespace(
        store=RecordingStore(), sources={"results": [Recording()]}, http_client=object(), poller=None
    )
    view = RaceNavigator(client, 9, UserSettings(9))
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=9),
        data={"values": ["TX"]},
        response=SimpleNamespace(defer=defer),
        edit_original_response=edit_original_response,
    )
    await view._state_callback(interaction)
    assert calls == ["defer", "read", "edit"]  # no "list": the click never asks the source


async def test_fallback_local_races_are_hidden_where_an_official_feed_lists_local_races():
    from types import SimpleNamespace

    from electionwatch.bot import navigator

    class Lister:
        def __init__(self, name, races, fallback=False):
            self.name, self.races, self.fallback, self.states = name, races, fallback, {"TX", "WY"}

        def available(self):
            return True

        async def list_races(self, state, client):
            return [(parse(race_key), race_key) for race_key in self.races if race_key.startswith(state)]

    official = Lister("tx_counties", ["TX LOCAL harris-county-judge"])
    civic = Lister(
        "civicapi", ["TX LOCAL harris-county-judge-general", "TX SEN", "WY LOCAL laramie-mayor"], fallback=True
    )
    client = SimpleNamespace(store=MemoryStore(), sources={"results": [civic, official]}, http_client=None)
    await RaceLists(client.store, client.sources, None).refresh()
    tx = set(await navigator.list_races(client, "TX"))
    wy = set(await navigator.list_races(client, "WY"))
    assert tx == {"TX LOCAL harris-county-judge", "TX SEN"}  # civicAPI's duplicate local race hidden
    assert wy == {"WY LOCAL laramie-mayor"}  # no official local feed: civicAPI's stay
