"""Matrix tests for the /races ephemeral navigator (electionwatch/bot/navigator.py).

Every click goes through the component's own ``callback`` with a recording fake interaction
(tests/fake_interaction_data.py) that validates each embed/view against Discord's hard limits
and enforces one acknowledgement per interaction.

Checklist
---------
[x] /races for a default user: defer(ephemeral) -> followup(embed, view, ephemeral), TX/Senate
[x] /races for a user with home_state/default_level saved; bad saved level falls back to Senate
[x] every tab x every one of the 51 STATES codes: state select + tab click, saved to UserSettings,
    first race of that tab auto-selected, defer -> edit_original_response
[x] territories (PR GU VI AS MP) are NOT offered by the state selects -> reported UX gap
[x] race select with 0 / 1 / 25 / 60 races; only 25 offered -> races 26+ unreachable (UX gap)
[x] choosing a race shows its result embed; a race without a result shows the placeholder
[x] Odds / Polls buttons -> defer then ephemeral followup with the odds / polls embed
[x] Choose race button -> race select replaces the race tools (edit_message)
[x] another user clicking ANY control -> ephemeral refusal, view state and stored settings unchanged
[x] navigation round-trips: a new navigator opens where the last one was left
[x] list_races raising / SourceUnavailable / timing out (LIST_TIMEOUT_SECONDS) still renders,
    and watched (poller default) races stay navigable
[x] list cache: TTL (LIST_TTL_SECONDS) and failure retry (LIST_RETRY_SECONDS); cache reset per test
[x] view timeout is 600 s
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from electionwatch.bot import navigator
from electionwatch.bot.discord import races_command
from electionwatch.bot.navigator import RaceNavigator
from electionwatch.config import load_races
from electionwatch.models import CandidateResult, Level, OddsSnapshot, Poll, PollResult, RaceKey, RaceResult
from electionwatch.racekeys import STATES, TERRITORIES, parse
from electionwatch.settings import UserSettings
from electionwatch.sources.base import OddsSource, PollSource, ResultsSource, SourceUnavailable
from electionwatch.store.memory import MemoryStore
from fake_interaction_data import FakeInteraction, check_embed, check_view, make_client

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TABS = {"congressional": "Congressional", "senate": "Senate", "state": "State", "local": "Local"}
OWNER, OTHER = 9, 10


@pytest.fixture(autouse=True)
def _clean_list_cache():
    navigator._list_cache.clear()
    yield
    navigator._list_cache.clear()


def races_for(state: str) -> list[tuple[RaceKey, str]]:
    return [
        (RaceKey(state, Level.SENATE, "SEN"), f"{state} Senate"),
        (RaceKey(state, Level.GOVERNOR, "GOV"), f"{state} Governor"),
        (RaceKey(state, Level.HOUSE, "HOUSE", "01"), f"{state} District 1"),
        (RaceKey(state, Level.STATE, "HD", "01"), f"{state} House District 1"),
        (RaceKey(state, Level.LOCAL, "LOCAL", "mayor"), f"{state} Mayor"),
    ]


FIRST = {  # race auto-selected per tab (sorted by key string, first visible)
    "senate": lambda s: f"{s} SEN",
    "congressional": lambda s: str(RaceKey(s, Level.HOUSE, "HOUSE", "01")),
    "state": lambda s: f"{s} GOV",
    "local": lambda s: f"{s} LOCAL mayor",
}


class Results(ResultsSource):
    name = "nav_results"
    states = frozenset(STATES)

    def __init__(self, races=None, error: BaseException | None = None, delay=0.0):
        self.races = races
        self.error = error
        self.delay = delay
        self.calls = 0

    async def list_races(self, state, client):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if self.races is not None:
            return [item for item in self.races if item[0].state == state]
        return races_for(state)


class Odds(OddsSource):
    name = "nav_odds"

    async def fetch_odds(self, keys, client):
        return [OddsSnapshot(key, "kalshi", "DEM", 0.6, NOW) for key in keys]


class Polls(PollSource):
    name = "nav_polls"

    async def fetch_polls(self, keys, client):
        from datetime import date
        return [Poll(key, "Pollster", [PollResult("Doe", "DEM", 50.0)], date(2026, 10, 20)) for key in keys]


def client_with(source=None, *, store=None, poller=None):
    sources = {"results": [source or Results()], "odds": [Odds()], "polls": [Polls()]}
    return make_client(store or MemoryStore(), sources, clock=lambda: NOW, poller=poller)


async def navigator_for(client, settings=None) -> RaceNavigator:
    view = RaceNavigator(client, OWNER, settings or UserSettings(OWNER))
    await view.refresh()
    check_view(view)
    check_embed(await view.embed())
    return view


# ----------------------------------------------------------------- component lookup
def buttons(view) -> dict[str, discord.ui.Button]:
    return {c.label: c for c in view.children if isinstance(c, discord.ui.Button)}


def state_selects(view) -> list[discord.ui.Select]:
    return [c for c in view.children if isinstance(c, discord.ui.Select) and c.placeholder.startswith("State:")]


def state_select_for(view, code) -> discord.ui.Select:
    return next(s for s in state_selects(view) if code in {o.value for o in s.options})


def race_select(view) -> discord.ui.Select | None:
    return next((c for c in view.children if isinstance(c, discord.ui.Select) and c.placeholder == "Race"), None)


def snapshot(view):
    return (
        view.state, view.level, view.selected_race, list(view.races),
        [(type(c).__name__, getattr(c, "label", None) or c.placeholder,
          getattr(c, "style", None),
          [(o.value, o.default) for o in getattr(c, "options", [])]) for c in view.children],
    )


async def click(view, item, *, user=OWNER, values=None) -> FakeInteraction:
    interaction = FakeInteraction(view.client, user_id=user, kind="component", values=values)
    await item.callback(interaction)
    return interaction


def assert_edited(interaction, kinds):
    assert interaction.kinds == kinds, interaction.kinds
    reply = interaction.assert_ephemeral_flow()
    assert reply.embed is not None and reply.view is not None
    return reply


# ----------------------------------------------------------------- /races initial build
async def test_races_command_for_a_default_user():
    client = client_with()
    interaction = FakeInteraction(client, user_id=OWNER)
    await races_command.callback(interaction)
    assert interaction.kinds == ["defer", "followup"]
    reply = interaction.assert_ephemeral_flow()
    view = reply.view
    assert (view.state, view.level, view.selected_race) == ("TX", "senate", "TX SEN")
    assert reply.embed.title == "Texas · Senate"
    assert {"Odds", "Polls", "Choose race"} <= set(buttons(view))
    assert buttons(view)["Senate"].style is discord.ButtonStyle.primary
    assert view.timeout == 600


@pytest.mark.parametrize("home,level,expected_level,expected_race", [
    ("NC", "congressional", "congressional", "NC-01"),
    ("AK", "congressional", "congressional", "AK-AL"),
    ("DC", "local", "local", "DC LOCAL mayor"),
    ("WY", "state", "state", "WY GOV"),
    ("CA", "bogus", "senate", "CA SEN"),
])
async def test_races_command_for_a_user_with_saved_navigation(home, level, expected_level, expected_race):
    store = MemoryStore()
    await store.save_user(UserSettings(OWNER, home_state=home, default_level=level))
    interaction = FakeInteraction(client_with(store=store), user_id=OWNER)
    await races_command.callback(interaction)
    view = interaction.assert_ephemeral_flow().view
    assert (view.state, view.level, view.selected_race) == (home, expected_level, expected_race)
    defaults = [o.value for s in state_selects(view) for o in s.options if o.default]
    assert defaults == [home]


# ----------------------------------------------------------------- every tab x every state
@pytest.mark.parametrize("code", sorted(STATES))
@pytest.mark.parametrize("tab", list(TABS))
async def test_every_tab_in_every_state(code, tab):
    client = client_with()
    view = await navigator_for(client)
    interaction = await click(view, state_select_for(view, code), values=[code])
    assert_edited(interaction, ["defer", "edit_original"])
    assert view.state == code and view.level == "senate"

    interaction = await click(view, buttons(view)[TABS[tab]])
    reply = assert_edited(interaction, ["defer", "edit_original"])
    assert (view.state, view.level) == (code, tab)
    assert view.selected_race == FIRST[tab](code)
    assert reply.embed.title == f"{STATES[code]} · {TABS[tab]}"
    assert buttons(view)[TABS[tab]].style is discord.ButtonStyle.primary
    saved = await client.store.get_user(OWNER)
    assert (saved.home_state, saved.default_level) == (code, tab)


async def test_territories_are_not_reachable_from_the_navigator():
    """UX GAP: the state selects are built from STATES only, so PR/GU/VI/AS/MP races cannot
    be browsed even though race keys, /odds and /polls accept them."""
    view = await navigator_for(client_with())
    offered = {o.value for s in state_selects(view) for o in s.options}
    assert offered == set(STATES)
    assert not offered & set(TERRITORIES)
    # 51 codes in selects of 25: the third select carries Wyoming alone.
    assert [len(s.options) for s in state_selects(view)] == [25, 25, 1]


# ----------------------------------------------------------------- race select sizes
@pytest.mark.parametrize("count", [0, 1, 25, 60])
async def test_race_select_sizes(count):
    races = [(parse(f"TX HD-{n}"), f"Texas House District {n}") for n in range(1, count + 1)]
    store = MemoryStore()
    view = await navigator_for(client_with(Results(races), store=store),
                               UserSettings(OWNER, home_state="TX", default_level="state"))
    if count == 0:
        assert view.selected_race is None
        assert race_select(view) is None and "Choose race" not in buttons(view)
        assert (await view.embed()).description.startswith("No current result")
        return
    assert view.selected_race == "TX HD-01"  # first by key string
    interaction = await click(view, buttons(view)["Choose race"])
    assert_edited(interaction, ["edit_message"])
    select = race_select(view)
    assert select is not None and "Odds" not in buttons(view)
    offered = [o.value for o in select.options]
    assert len(offered) == min(count, 25)
    if count > 25:
        # UX GAP: only the first 25 races (by key) are offered; the rest cannot be chosen.
        unreachable = {str(k) for k, _ in races} - set(offered)
        assert len(unreachable) == count - 25


async def test_choosing_a_race_shows_its_result_and_brings_back_the_tools():
    store = MemoryStore()
    key = parse("TX HD-05")
    await store.upsert_result(RaceResult(key, [CandidateResult("Doe", "DEM", 120), CandidateResult("Roe", "REP", 80)],
                                         "test", NOW).finalize())
    races = [(parse(f"TX HD-{n}"), f"Texas House District {n}") for n in range(1, 8)]
    view = await navigator_for(client_with(Results(races), store=store),
                               UserSettings(OWNER, home_state="TX", default_level="state"))
    await click(view, buttons(view)["Choose race"])
    interaction = await click(view, race_select(view), values=["TX HD-05"])
    reply = assert_edited(interaction, ["edit_message"])
    assert view.selected_race == "TX HD-05"
    assert "Doe" in reply.embed.description
    assert {"Odds", "Polls", "Choose race"} <= set(buttons(view)) and race_select(view) is None

    interaction = await click(view, buttons(view)["Choose race"])
    await click(view, race_select(view), values=["TX HD-06"])
    assert (await view.embed()).description.startswith("No current result")


@pytest.mark.parametrize("tool,title", [("Odds", "Odds · Texas Senate"), ("Polls", "Polls · Texas Senate")])
async def test_race_tool_buttons_send_an_ephemeral_followup(tool, title):
    view = await navigator_for(client_with())
    before = snapshot(view)
    interaction = await click(view, buttons(view)[tool])
    assert interaction.kinds == ["defer", "followup"]
    reply = interaction.assert_ephemeral_flow()
    assert reply.embed.title == title and reply.view is None
    assert snapshot(view) == before


# ----------------------------------------------------------------- ownership
def every_control(view):
    items = [(label, b, None) for label, b in buttons(view).items()]
    items += [(f"state:{s.options[0].value}", s, [s.options[-1].value]) for s in state_selects(view)]
    return items


async def test_other_users_are_refused_on_every_control_and_nothing_changes():
    store = MemoryStore()
    view = await navigator_for(client_with(store=store))
    controls = every_control(view)
    await click(view, buttons(view)["Choose race"])
    controls.append(("race", race_select(view), ["TX SEN"]))
    before = snapshot(view)
    assert len(controls) >= 9
    for name, item, values in controls:
        interaction = await click(view, item, user=OTHER, values=values)
        assert interaction.kinds == ["send_message"], name
        reply = interaction.assert_ephemeral_flow()
        assert reply.content == "This navigator belongs to another user."
        assert reply.embed is None and reply.view is None
        assert snapshot(view) == before, name
    assert store.users == {}


async def test_navigation_round_trips_through_user_settings():
    store = MemoryStore()
    client = client_with(store=store)
    view = await navigator_for(client)
    await click(view, state_select_for(view, "GA"), values=["GA"])
    await click(view, buttons(view)["State"])
    reopened = await navigator_for(client, await store.get_user(OWNER))
    assert (reopened.state, reopened.level, reopened.selected_race) == ("GA", "state", "GA GOV")


# ----------------------------------------------------------------- failing / slow sources
@pytest.mark.parametrize("source", [
    Results(error=RuntimeError("boom")),
    Results(error=SourceUnavailable("gone")),
    Results(delay=10),
], ids=["raises", "unavailable", "times-out"])
async def test_failing_list_races_still_renders(source, monkeypatch):
    monkeypatch.setattr(navigator, "LIST_TIMEOUT_SECONDS", 0.05)
    poller = SimpleNamespace(default_races=load_races())
    client = client_with(source, poller=poller)
    started = time.monotonic()
    interaction = FakeInteraction(client, user_id=OWNER)
    await races_command.callback(interaction)
    assert time.monotonic() - started < 1
    view = interaction.assert_ephemeral_flow().view
    # The watched default race (TX SEN) stays navigable without any source listing.
    assert view.selected_race == "TX SEN"
    interaction = await click(view, state_select_for(view, "OH"), values=["OH"])
    reply = assert_edited(interaction, ["defer", "edit_original"])
    assert view.selected_race is None and race_select(view) is None
    assert reply.embed.description.startswith("No current result")


async def test_list_cache_ttl_and_failure_retry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(navigator.time, "monotonic", lambda: now[0])
    good = Results()
    client = client_with(good)
    for delta in (0, navigator.LIST_TTL_SECONDS - 1):
        now[0] = 1000.0 + delta
        assert "TX SEN" in await navigator.list_races(client, "TX")
    assert good.calls == 1
    now[0] = 1000.0 + navigator.LIST_TTL_SECONDS + 1
    await navigator.list_races(client, "TX")
    assert good.calls == 2

    navigator._list_cache.clear()
    bad = Results(error=RuntimeError("down"))
    client = client_with(bad)
    now[0] = 5000.0
    assert await navigator.list_races(client, "TX") == {}
    now[0] = 5000.0 + navigator.LIST_RETRY_SECONDS - 1
    assert await navigator.list_races(client, "TX") == {}
    assert bad.calls == 1
    bad.error = None
    now[0] = 5000.0 + navigator.LIST_RETRY_SECONDS + 1
    assert "TX SEN" in await navigator.list_races(client, "TX")
    assert bad.calls == 2


async def test_unavailable_and_out_of_state_sources_are_not_asked():
    class Keyed(Results):
        name = "keyed"
        env = ("EW_TEST_NEVER_SET_KEY",)

    class Elsewhere(Results):
        name = "elsewhere"
        states = frozenset({"CA"})

    keyed, elsewhere = Keyed(), Elsewhere()
    client = make_client(MemoryStore(), {"results": [keyed, elsewhere]})
    assert await navigator.list_races(client, "TX") == {}
    assert keyed.calls == elsewhere.calls == 0
