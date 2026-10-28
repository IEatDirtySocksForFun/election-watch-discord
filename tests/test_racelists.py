"""The stored race lists behind /races and race autocomplete (electionwatch/racelists.py): the
background refresh lists states per source on the request budget, gradually and in priority
order; the commands only read what it stored."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from electionwatch.bot.cooldown import Cooldowns
from electionwatch.bot.logic import limits_text
from electionwatch.bot.navigator import RaceNavigator
from electionwatch.demand import Demand
from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import ALL_JURISDICTIONS, STATES, parse
from electionwatch.racelists import (
    CACHE,
    KIND,
    STATES_PER_TICK,
    RaceLists,
    budget_name,
    cache_ident,
    dump_list,
    lists_races,
    load_list,
    typed_state,
)
from electionwatch.ratelimit import FLOORS, WINDOW, Limit, RateBook
from electionwatch.settings import GuildSettings, UserSettings
from electionwatch.sources.base import ResultsSource, SourceUnavailable
from electionwatch.store.memory import MemoryStore

START = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)
HOST = "politics.api.cnn.io"  # cnn.io: 10 requests a minute published, 300 an hour for the bot


class Clock:
    """One clock for the store timestamps (datetime) and the budgets (seconds)."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> datetime:
        return START + timedelta(seconds=self.t)

    def seconds(self) -> float:
        return self.t


class National(ResultsSource):
    """A fallback covering every jurisdiction; each list costs `cost` requests to HOST."""

    name = "national"
    states = frozenset(ALL_JURISDICTIONS)
    fallback = True

    def __init__(self, book: RateBook | None = None, cost: int = 2) -> None:
        self.book = book
        self.cost = cost
        self.asked: list[str] = []
        self.fail: set[str] = set()
        self.empty: set[str] = set()
        self.log: list[float] = []  # request times, for the hourly checks

    async def list_races(self, state, client):
        self.asked.append(state)
        for _ in range(self.cost):
            if self.book is not None:
                self.book.record(HOST)
                self.log.append(self.book.clock())
        if state in self.fail:
            raise SourceUnavailable("blocked")
        if state in self.empty:
            return []
        return [
            (RaceKey(state, Level.SENATE, "SEN"), f"{ALL_JURISDICTIONS[state]} Senate"),
            (RaceKey(state, Level.LOCAL, "LOCAL", "mayor"), "Mayor"),
        ]


class Official(ResultsSource):
    name = "official_tx"
    states = frozenset({"TX"})

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.fail = False

    async def list_races(self, state, client):
        self.asked.append(state)
        if self.fail:
            raise RuntimeError("down")
        return [
            (RaceKey("TX", Level.GOVERNOR, "GOV"), "Texas Governor"),
            (RaceKey("TX", Level.LOCAL, "LOCAL", "Harris-County-Judge"), "Harris County Judge"),
        ]


class NoLists(ResultsSource):
    """Inherits the base list_races (no races): never asked, never stored."""

    name = "no_lists"
    states = frozenset(STATES)


async def setup(*sources, limits=None, guilds=(), demand=True, clock=None):
    clock = clock or Clock()
    store = MemoryStore()
    for guild in guilds:
        await store.save_guild(guild)
    book = RateBook(limits, clock=clock.seconds)
    queue = Demand(clock=clock.seconds) if demand else None
    for source in sources:
        if hasattr(source, "book") and source.book is None:
            source.book = book
    lists = RaceLists(
        store,
        {"results": list(sources)},
        object(),
        clock.now,
        rates=book,
        demand=queue,
        default_races=[SimpleNamespace(key=parse("TX SEN"))],
    )
    return clock, store, book, lists


# --------------------------------------------------------------------------- storing and reading
async def test_refresh_stores_one_list_per_state_and_source_on_the_budget():
    official, national = Official(), National()
    clock, store, book, lists = await setup(official, national, NoLists())
    assert await lists.refresh() == 1 + STATES_PER_TICK
    stored = await store.cached_idents(CACHE)
    assert cache_ident("TX", "official_tx") in stored and cache_ident("TX", "national") in stored
    assert len(stored) == 1 + STATES_PER_TICK
    assert not any(ident.endswith("|no_lists") for ident in stored)
    rows = {row["source"]: row for row in book.status()}
    assert rows[budget_name("national", "TX")]["kind"] == KIND
    assert rows[budget_name("national", "TX")]["requests_per_refresh"] == 2
    assert rows[budget_name("national", "TX")]["interval"] == FLOORS[KIND]
    found, loading = await lists.stored("TX")
    assert not loading
    # The official feed lists local races, so the fallback's local race is a duplicate: hidden.
    assert set(found) == {"TX SEN", "TX GOV", "TX LOCAL Harris-County-Judge"}
    found, loading = await lists.stored("WY")  # not listed yet
    assert found == {} and loading
    assert not lists_races(NoLists()) and lists_races(national)


async def test_commands_read_only_the_store():
    national = National()
    _, store, _, lists = await setup(national)
    await lists.refresh()
    asked = list(national.asked)
    for state in ("TX", "WY", "PR"):
        await lists.stored(state)
        lists.want(state)
    assert national.asked == asked


async def test_the_first_sweep_is_gradual_and_in_priority_order():
    national = National()
    guilds = [GuildSettings(1, important=["GA SEN", "PA-07"]), GuildSettings(2)]  # 2: default races
    clock, store, book, lists = await setup(national, guilds=guilds)
    lists.want("MT")
    await lists.refresh()
    assert national.asked == ["MT", "GA", "PA"]  # demanded, then watched
    clock.t += 60
    await lists.refresh()
    assert national.asked[3:] == ["TX", "AK", "AL"]  # watched (2: default race), then the rest
    ticks = 2
    while len(set(national.asked)) < len(ALL_JURISDICTIONS):
        clock.t += 60
        before = len(national.asked)
        await lists.refresh()
        assert len(national.asked) - before <= STATES_PER_TICK
        ticks += 1
    assert ticks == -(-len(ALL_JURISDICTIONS) // STATES_PER_TICK)  # 19 minutes for 56 states
    assert len(national.asked) == len(ALL_JURISDICTIONS)  # each once
    clock.t += 60
    assert await lists.refresh() == 0  # nothing due for an hour


async def test_lists_are_refetched_after_the_floor_and_a_restart_does_not_refetch_fresh_ones():
    national = National()
    clock, store, book, lists = await setup(national, limits={"cnn.io": Limit(10_000, 60)})
    while await lists.refresh():
        clock.t += 60
    count = len(national.asked)
    # A restart: new budgets, same database. Nothing is fetched while the lists are fresh.
    restarted = RaceLists(store, {"results": [national]}, object(), clock.now, rates=RateBook(clock=clock.seconds))
    assert await restarted.refresh() == 0 and len(national.asked) == count
    # An hour after the first lists were stored they are due again, oldest first.
    clock.t = FLOORS[KIND]
    assert await lists.refresh() == STATES_PER_TICK
    assert national.asked[count:] == national.asked[:STATES_PER_TICK]


async def test_a_sweep_never_exceeds_the_hosts_hourly_allowance():
    # 56 states x 2 requests = 112 requests, but this host allows the bot 30 an hour.
    national = National()
    clock, store, book, lists = await setup(national, limits={"cnn.io": Limit(60, 3600)})
    assert book.allowance(HOST) == 30
    for _ in range(6 * 60):  # six hours of the minute loop
        await lists.refresh()
        clock.t += 60
    window: deque[float] = deque()
    for at in national.log:
        window.append(at)
        while window[0] <= at - WINDOW:
            window.popleft()
        assert len(window) <= 30
    # Still making progress, at the allowance's pace: every state is listed within six hours.
    assert set(national.asked) == set(ALL_JURISDICTIONS)
    stretch = book.stretch(book.bucket(HOST))
    assert stretch > 1  # 112 requests an hour wanted: every list is refreshed less often
    assert book.interval(budget_name("national", "TX"), KIND) == pytest.approx(FLOORS[KIND] * stretch)


async def test_a_sweep_on_a_roomy_host_is_spread_over_minutes():
    national = National()
    clock, store, book, lists = await setup(national)  # cnn.io: 300 an hour
    per_tick = []
    for _ in range(30):
        before = len(national.log)
        await lists.refresh()
        per_tick.append(len(national.log) - before)
        clock.t += 60
    assert max(per_tick) == 2 * STATES_PER_TICK  # never a burst of 112
    assert sum(per_tick) == 2 * len(ALL_JURISDICTIONS)


async def test_race_lists_share_the_host_with_other_sources():
    # Results on the same host have used up the hour: race lists wait instead of adding to it.
    national = National()
    clock, store, book, lists = await setup(national, limits={"cnn.io": Limit(60, 3600)})
    with book.track("cnn:results", "results"):
        for _ in range(30):
            book.record(HOST)
    await lists.refresh()
    # The first state is allowed (nothing known about its cost yet); after it, every other
    # state is expected to cost what it cost, and the hour has no room for that.
    assert len(national.asked) == 1
    clock.t += 60
    await lists.refresh()
    assert len(national.asked) == 1


# --------------------------------------------------------------------------- failures
async def test_a_failing_source_keeps_its_lists_and_does_not_touch_another_sources():
    official, national = Official(), National()
    clock, store, book, lists = await setup(official, national, limits={"cnn.io": Limit(10_000, 60)})
    lists.want("TX")
    await lists.refresh()
    before = await lists.stored("TX")
    official.fail = True
    national.fail = {"TX"}
    lists.force()
    clock.t += 60
    await lists.refresh()
    assert await lists.stored("TX") == before  # both stored lists survive
    # The national source went on with other states after TX failed; the official one (TX
    # only) failed on everything it tried and backs off as a whole.
    assert national.asked[-STATES_PER_TICK:][0] == "TX" and len(set(national.asked)) > 3
    # A failed state backs off (1 minute, then 2, 4, ...) instead of being asked every
    # minute, and is retried after the back-off, not an hour later.
    asked = len(national.asked)
    await lists.refresh()
    assert "TX" not in national.asked[asked:] and official.asked.count("TX") == 2
    clock.t += 60
    national.fail = set()
    asked = len(national.asked)
    await lists.refresh()
    assert national.asked[asked] == "TX" and official.asked.count("TX") == 3
    clock.t += 60  # official: 2 minutes now
    await lists.refresh()
    assert official.asked.count("TX") == 3
    clock.t += 60
    official.fail = False
    await lists.refresh()
    assert official.asked.count("TX") == 4
    found, _ = await lists.stored("TX")
    assert "TX GOV" in found


async def test_an_empty_answer_does_not_wipe_a_recent_list():
    national = National()
    clock, store, book, lists = await setup(national)
    lists.want("OH")
    await lists.refresh()
    national.empty = {"OH"}
    clock.t = FLOORS[KIND]
    lists.want("OH")
    await lists.refresh()
    assert national.asked.count("OH") == 2
    assert "OH SEN" in (await lists.stored("OH"))[0]
    # A day later the source is believed: the state has no races there any more.
    clock.t = FLOORS[KIND] + 25 * 3600
    lists.want("OH")
    await lists.refresh()
    assert national.asked.count("OH") == 3
    found, loading = await lists.stored("OH")
    assert found == {} and not loading


async def test_a_store_failure_is_logged_not_raised():
    class Broken(MemoryStore):
        async def put_cached(self, *args):
            raise RuntimeError("mongo down")

        async def get_cached(self, *args):
            raise RuntimeError("mongo down")

    national = National()
    lists = RaceLists(Broken(), {"results": [national]}, object())
    assert await lists.refresh() == 1  # tried one state, stored nothing
    assert await lists.stored("TX") == ({}, True)


async def test_refreshes_do_not_overlap():
    class Slow(National):
        async def list_races(self, state, client):
            await asyncio.sleep(0.05)
            return await super().list_races(state, client)

    slow = Slow()
    _, _, _, lists = await setup(slow)
    first = asyncio.create_task(lists.refresh())
    await asyncio.sleep(0)
    assert lists.running and await lists.refresh() == 0
    assert await first == STATES_PER_TICK


async def test_force_makes_fresh_lists_due_a_few_at_a_time():
    national = National()
    clock, store, book, lists = await setup(national, limits={"cnn.io": Limit(10_000, 60)})
    while await lists.refresh():
        clock.t += 60
    count = len(national.asked)
    clock.t += 60
    assert await lists.refresh() == 0
    lists.force()
    assert await lists.refresh() == STATES_PER_TICK
    assert len(national.asked) == count + STATES_PER_TICK


# --------------------------------------------------------------------------- the pieces
def test_list_round_trip_keeps_slugs_and_skips_unreadable_entries():
    races = [
        (RaceKey("WA", Level.LOCAL, "LOCAL", "Advisory-Vote-No-10"), "Advisory Vote No. 10"),
        (parse("AK-AL"), "Alaska At-Large"),
        (parse("OH SEN S"), "Ohio Senate (special)"),
    ]
    assert load_list(dump_list(races)) == races
    text = dump_list(races)[:-1] + ', ["TX", "nonsense", "X", null, false, "bad"], 7]'
    assert load_list(text) == races


@pytest.mark.parametrize(
    "text,state",
    [
        ("PA", "PA"),
        ("pa sen", "PA"),
        ("TX-07", "TX"),
        ("texas gov", "TX"),
        ("Texas", "TX"),
        ("new york", "NY"),
        ("puerto rico", "PR"),
        ("west virginia sen", "WV"),
        ("", None),
        ("se", None),
        ("sen", None),
        ("new", None),
        ("district 7", None),
    ],
)
def test_typed_state(text, state):
    assert typed_state(text) == state


async def test_the_navigator_queues_the_state_it_opens():
    national = National()
    _, store, _, lists = await setup(national)
    client = SimpleNamespace(store=store, sources=lists.sources, http_client=object(), poller=None, race_lists=lists)
    view = RaceNavigator(client, 9, UserSettings(9, home_state="NV"), watched=["NV SEN", "NV-03"])
    await view.refresh()
    assert lists.demand.items(KIND) == ["NV"] and view.loading
    assert [str(key) for key, _ in view.races] == ["NV SEN", "NV-03"]  # watched, while loading
    await lists.refresh()
    assert national.asked[0] == "NV"
    await view.refresh()
    assert not view.loading and "NV LOCAL mayor" in {str(key) for key, _ in view.races}


async def test_the_navigator_offers_the_users_favorites_in_the_state_while_loading():
    _, store, _, lists = await setup(National())
    client = SimpleNamespace(store=store, sources=lists.sources, http_client=object(), poller=None, race_lists=lists)
    settings = UserSettings(9, home_state="AZ", default_level="congressional", favorites=["AZ-06", "PA SEN"])
    view = RaceNavigator(client, 9, settings, watched=["TX SEN"])
    await view.refresh()
    assert view.loading and view.selected_race == "AZ-06"


def test_limits_text_shows_race_lists_once_per_source():
    clock = Clock()
    book = RateBook({"cnn.io": Limit(60, 3600)}, clock=clock.seconds)
    for state in ALL_JURISDICTIONS:
        name = budget_name("national", state)
        book.due(name, KIND)
        with book.track(name, KIND):
            book.record(HOST)
            book.record(HOST)
    text = limits_text(GuildSettings(1), book, Cooldowns())
    assert "racelists: every" in text
    assert text.count("    national:") == 1  # slowed: one line, not 56


def test_an_operator_interval_for_race_lists_covers_every_state():
    book = RateBook(intervals={"national:racelists": 600})
    assert book.interval(budget_name("national", "TX"), KIND) == 600
    assert RateBook(intervals={"racelists": 900}).interval(budget_name("x", "OH"), KIND) == 900
    assert RateBook(intervals={"national": 120}).interval(budget_name("national", "OH"), KIND) == 120


# --------------------------------------------------------------------------- review regressions
async def test_one_state_that_keeps_failing_does_not_stop_the_sweep():
    # civicAPI answering 400 for one jurisdiction must not keep every other state unlisted.
    national = National()
    national.fail = {"AK"}  # first in the never-listed order
    clock, store, book, lists = await setup(national)
    for _ in range(45):
        await lists.refresh()
        clock.t += 60
    stored = await store.cached_idents(CACHE)
    assert len(stored) == len(ALL_JURISDICTIONS) - 1  # everything but AK
    assert cache_ident("AK", "national") not in stored
    # AK backs off on its own (1, 2, 4, 8, 15... minutes) instead of being asked every minute.
    assert 3 <= national.asked.count("AK") <= 7
    assert (await lists.stored("AK"))[1]  # still loading, not "no races"


async def test_a_host_that_is_down_is_not_asked_about_every_state_each_minute():
    national = National()
    national.fail = set(ALL_JURISDICTIONS)
    clock, store, book, lists = await setup(national)
    for _ in range(60):
        await lists.refresh()
        clock.t += 60
    assert len(national.asked) <= 20  # source back-off: 1, 2, 4, 8, 15 min


async def test_a_first_empty_answer_is_retried_soon_and_still_shows_loading():
    # cnn_results and al_counties answer [] when their feed errors or is not live yet.
    national = National()
    national.empty = {"OH"}
    clock, store, book, lists = await setup(national)
    lists.want("OH")
    await lists.refresh()
    assert (await lists.stored("OH")) == ({}, True)  # not frozen as "no races"
    national.empty = set()  # the feed comes up
    for _ in range(10):
        clock.t += 60
        lists.want("OH")
        await lists.refresh()
    found, loading = await lists.stored("OH")
    assert "OH SEN" in found and not loading  # within minutes, not an hour


async def test_a_state_with_genuinely_no_races_stops_loading_after_a_few_tries():
    national = National()
    national.empty = {"OH"}
    clock, store, book, lists = await setup(national)
    for _ in range(40):
        lists.want("OH")
        await lists.refresh()
        clock.t += 60
    assert await lists.stored("OH") == ({}, False)
    assert national.asked.count("OH") == 3


def test_a_part_without_requests_keeps_its_siblings_cost():
    clock = Clock()
    book = RateBook({"cnn.io": Limit(60, 3600)}, clock=clock.seconds)  # 30 an hour
    with book.track(budget_name("national", "TX"), KIND):
        book.record(HOST)
        book.record(HOST)
    with book.track("cnn:results", "results"):
        for _ in range(28):
            book.record(HOST)
    assert not book.due(budget_name("national", "AL"), KIND)
    with book.track(budget_name("national", "OH"), KIND):
        pass  # answered from a cache
    assert not book.due(budget_name("national", "AL"), KIND)


async def test_the_newest_request_goes_first_and_age_order_still_applies():
    national = National()
    clock, store, book, lists = await setup(national, limits={"cnn.io": Limit(10_000, 60)})
    for state in ALL_JURISDICTIONS:  # every state asked for once
        lists.want(state)
    while await lists.refresh():
        clock.t += 60
    clock.t += FLOORS[KIND] + 60  # all stale, oldest first...
    lists.want("TX")  # ...but TX was just asked for
    before = len(national.asked)
    await lists.refresh()
    asked = national.asked[before:]
    assert asked[0] == "TX"
    oldest = [state for state in sorted(national.asked[:STATES_PER_TICK]) if state != "TX"]
    assert asked[1:] == oldest[: STATES_PER_TICK - 1]  # then the oldest lists


def test_demand_keeps_requests_in_the_order_they_were_last_made():
    clock = Clock()
    demand = Demand(clock=clock.seconds)
    for state in ("TX", "OH", "PA", "GA"):
        demand.add(KIND, state)
        clock.t += 1
    demand.add(KIND, "OH")
    assert demand.items(KIND) == ["TX", "PA", "GA", "OH"]
