"""EW_COMMAND_DATA=live: commands fetch from the sources, within the same request budgets."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx

from electionwatch.__main__ import validate_env
from electionwatch.bot.command_data import CommandData
from electionwatch.bot.logic import limits_card
from electionwatch.live import LiveFetch, command_mode
from electionwatch.models import CandidateResult, OddsSnapshot, Poll, PollResult, RaceResult
from electionwatch.poller import ODDS_CHECKED, POLLS_CACHE, polls_ident
from electionwatch.racekeys import parse
from electionwatch.racelists import CACHE, RaceLists, cache_ident
from electionwatch.ratelimit import RateBook
from electionwatch.sources.base import OddsSource, PollSource, ResultsSource
from electionwatch.sources.early_vote import API, CACHE_KIND, EarlyVoteClient, NotCached
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
TX = parse("TX SEN")
OH = parse("OH SEN S")


class Clock:
    """The bot's clock (datetime) and the budget clock (seconds), moved together."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def seconds(self) -> float:
        return self.now.timestamp()

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Odds(OddsSource):
    name = "kalshi"

    def __init__(self, fail: bool = False, delay: float = 0.0):
        self.calls = 0
        self.fail = fail
        self.delay = delay

    async def fetch_odds(self, keys, client):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise httpx.ConnectError("down")
        return [OddsSnapshot(key, "kalshi", "REP", 0.6, NOW) for key in keys if not key.special]


class Polls(PollSource):
    name = "votehub"

    def __init__(self):
        self.calls = 0

    async def fetch_polls(self, keys, client):
        self.calls += 1
        return [Poll(key, "Pollster", [PollResult("A", "DEM", 50.0)], date(2026, 10, 30)) for key in keys]


class Official(ResultsSource):
    name = "official"
    states = frozenset({"TX"})
    partial = False

    def __init__(self):
        self.calls = 0

    async def fetch(self, keys, client):
        self.calls += 1
        title = "(partial: 2 counties)" if self.partial else None
        return [RaceResult(key, [CandidateResult("A", "DEM", 10)], self.name, NOW, title=title) for key in keys]

    async def list_races(self, state, client):
        self.calls += 1
        return [(TX, "Texas Senate")]


class Fallback(Official):
    name = "fallback"
    fallback = True


def live(store, clock, rates=None, **sources) -> LiveFetch:
    return LiveFetch(store, sources, object(), rates, clock, max_age=60, timeout=5, monotonic=clock.seconds)


# --------------------------------------------------------------------------- settings
def test_mode_defaults_to_the_database_and_check_flags_a_typo():
    assert command_mode({}) == "db"
    assert command_mode({"EW_COMMAND_DATA": " LIVE "}) == "live"
    assert command_mode({"EW_COMMAND_DATA": "sometimes"}) == "db"
    base = {"DISCORD_TOKEN": "x", "MONGODB_URI": "x"}
    assert validate_env({**base, "EW_COMMAND_DATA": "live"}) == []
    assert validate_env({**base, "EW_COMMAND_DATA": "sometimes"}) == ["EW_COMMAND_DATA"]
    fetcher = LiveFetch.from_env({"EW_LIVE_MAX_AGE": "30", "EW_LIVE_TIMEOUT": "oops"}, None, {}, None)
    assert (fetcher.max_age, fetcher.timeout) == (30.0, 10.0)


async def test_has_room_ignores_the_interval_but_not_the_hourly_allowance():
    clock = Clock()
    book = RateBook(clock=clock.seconds)
    assert book.has_room("kalshi:odds") and book.spacing("kalshi:odds") == 0  # never ran: no hosts yet
    request = SimpleNamespace(url=httpx.URL("https://api.elections.kalshi.com/x"))
    with book.track("kalshi:odds", "odds"):
        await book.on_request(request)
    assert not book.due("kalshi:odds", "odds")  # the refresh waits for its interval...
    assert book.has_room("kalshi:odds")  # ...a live command doesn't
    assert book.spacing("kalshi:odds") == pytest.approx(3600 / book.allowance("api.elections.kalshi.com"))
    for _ in range(int(book.allowance("api.elections.kalshi.com"))):
        book.record("api.elections.kalshi.com")
    assert not book.has_room("kalshi:odds")  # the hour's allowance is shared and used up


# --------------------------------------------------------------------------- odds
async def test_odds_are_fetched_stored_and_reused_while_fresh():
    clock, store, source = Clock(), MemoryStore(), Odds()
    data = CommandData(store, {"odds": [source]}, object(), clock, live=live(store, clock, odds=[source]))

    assert [snapshot.prob for snapshot in await data.get_odds(TX)] == [0.6]
    assert source.calls == 1
    clock.advance(30)
    await data.get_odds(TX)
    assert source.calls == 1  # younger than EW_LIVE_MAX_AGE: the stored answer
    clock.advance(31)
    await data.get_odds(TX)
    assert source.calls == 2


async def test_a_crowd_asking_at_once_shares_one_fetch():
    clock, store, source = Clock(), MemoryStore(), Odds(delay=0.05)
    fetcher = live(store, clock, odds=[source])
    await asyncio.gather(*(fetcher.odds(TX) for _ in range(10)))
    assert source.calls == 1


async def test_no_room_in_the_hour_answers_from_the_database():
    clock, store, source = Clock(), MemoryStore(), Odds()
    await store.add_odds(OddsSnapshot(TX, "kalshi", "REP", 0.4, NOW))
    rates = SimpleNamespace(has_room=lambda name: False, spacing=lambda name: 0.0)
    data = CommandData(store, {"odds": [source]}, object(), clock, live=live(store, clock, rates, odds=[source]))
    assert [snapshot.prob for snapshot in await data.get_odds(TX)] == [0.4]
    assert source.calls == 0


async def test_a_failing_source_backs_off_and_the_stored_answer_stands():
    clock, store, source = Clock(), MemoryStore(), Odds(fail=True)
    await store.add_odds(OddsSnapshot(TX, "kalshi", "REP", 0.4, NOW))
    data = CommandData(store, {"odds": [source]}, object(), clock, live=live(store, clock, odds=[source]))
    assert [snapshot.prob for snapshot in await data.get_odds(TX)] == [0.4]
    clock.advance(30)
    await data.get_odds(TX)
    assert source.calls == 1  # backing off for a minute after the failure
    clock.advance(31)
    await data.get_odds(TX)
    assert source.calls == 2


async def test_a_special_election_is_asked_by_its_plain_key_too_and_unpriced_races_are_marked():
    clock, store, source = Clock(), MemoryStore(), Odds()
    fetcher = live(store, clock, odds=[source])
    await fetcher.odds(OH)
    assert [str(snapshot.key) for snapshot in await store.latest_odds(OH)] == ["OH SEN S"]
    await fetcher.odds(parse("TX GOV"))
    assert await store.get_cached(ODDS_CHECKED, "TX GOV") is None  # priced
    source.fetch_odds = lambda keys, client: asyncio.sleep(0, result=[])
    await fetcher.odds(parse("TX-07"))
    assert await store.get_cached(ODDS_CHECKED, "TX-07") is not None  # asked, no market


async def test_live_fetches_to_one_source_are_spaced_by_its_share_of_the_allowance():
    clock, store, source = Clock(), MemoryStore(), Odds()
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds)

    rates = SimpleNamespace(has_room=lambda name: True, spacing=lambda name: 2.0)
    fetcher = LiveFetch(store, {"odds": [source]}, object(), rates, clock, monotonic=clock.seconds, sleep=sleep)
    await fetcher.odds(TX)
    await fetcher.odds(parse("TX GOV"))
    assert source.calls == 2 and slept == [pytest.approx(2.0)]


# --------------------------------------------------------------------------- polls
async def test_polls_for_a_race_nobody_watches_come_back_straight_away():
    clock, store, source = Clock(), MemoryStore(), Polls()
    db = CommandData(store, {"polls": [source]}, object(), clock)
    assert "queued" in (await db.polls_embed(TX)).description  # database mode: next refresh
    data = CommandData(store, {"polls": [source]}, object(), clock, live=live(store, clock, polls=[source]))
    embed = await data.polls_embed(TX)
    assert "queued" not in (embed.description or "")
    assert source.calls == 1 and await store.get_cached(POLLS_CACHE, polls_ident("votehub", TX)) is not None


async def test_polls_a_refresh_stored_recently_are_not_fetched_again():
    clock, store, source = Clock(), MemoryStore(), Polls()
    await store.put_cached(POLLS_CACHE, polls_ident("votehub", TX), "[]", NOW - timedelta(seconds=10))
    await live(store, clock, polls=[source]).polls(TX)
    assert source.calls == 0


# --------------------------------------------------------------------------- results and race lists
async def test_results_ask_official_feeds_first_and_fallbacks_only_without_a_complete_answer():
    clock, store = Clock(), MemoryStore()
    official, fallback = Official(), Fallback()
    fetcher = live(store, clock, results=[official, fallback])
    await fetcher.results(TX)
    assert (official.calls, fallback.calls) == (1, 0)
    assert (await store.latest_result(TX)).source == "official"

    official.partial = True
    clock.advance(61)
    await fetcher.results(TX)
    assert (official.calls, fallback.calls) == (2, 1)
    assert (await store.latest_result(TX)).source == "fallback"


async def test_the_navigator_fetches_lists_and_results_in_live_mode():
    from electionwatch.bot.navigator import RaceNavigator
    from electionwatch.settings import UserSettings

    clock, store, source = Clock(), MemoryStore(), Official()
    sources = {"results": [source]}
    lists = RaceLists(store, sources, object(), clock)
    client = SimpleNamespace(
        store=store, sources=sources, http_client=object(), poller=None, race_lists=lists, live=live(store, clock)
    )
    client.live.sources = sources
    view = RaceNavigator(client, 9, UserSettings(9, home_state="TX"))
    await view.refresh()
    assert await store.get_cached(CACHE, cache_ident("TX", "official")) is not None
    assert view.selected_race == "TX SEN"
    embed = await view.embed()
    assert "Texas" in embed.title and source.calls == 2  # one list, one result


async def test_an_empty_race_list_is_left_to_the_background_refresh():
    clock, store = Clock(), MemoryStore()

    class Empty(Official):
        async def list_races(self, state, client):
            return []

    sources = {"results": [Empty()]}
    await live(store, clock, **sources).race_list(RaceLists(store, sources, object(), clock), "TX")
    assert await store.get_cached(CACHE, cache_ident("TX", "official")) is None


# --------------------------------------------------------------------------- early vote
NATIONAL = {"regions": {"TX": {"Democratic": {"votes": 10}}}, "statewide_total": 10, "snapshot_date": "2026-10-30"}


@respx.mock
async def test_early_vote_downloads_while_civicapi_has_room_and_falls_back_to_the_saved_page():
    store = MemoryStore()
    route = respx.get(url__startswith=f"{API}/US/").respond(json=NATIONAL)
    room = {"ok": True}
    async with httpx.AsyncClient() as http:
        client = EarlyVoteClient(http, store=store, ttl=0, min_interval=0, may_download=lambda: room["ok"])
        assert "TX" in (await client.national()).states
        downloads = route.call_count
        assert any(ident.startswith(f"{API}/US/") for ident in await store.cached_idents(CACHE_KIND))

        room["ok"] = False  # no room left this hour: the saved pages answer
        assert "TX" in (await client.national()).states
        assert route.call_count == downloads

        room["ok"] = True
        route.mock(return_value=httpx.Response(503))  # civicAPI down: the saved pages answer
        assert "TX" in (await client.national()).states

        empty = EarlyVoteClient(http, store=MemoryStore(), ttl=0, min_interval=0, may_download=lambda: False)
        with pytest.raises(NotCached):
            await empty.national()


# --------------------------------------------------------------------------- /limits
def test_limits_say_which_mode_commands_run_in():
    from electionwatch.bot.cooldown import Cooldowns

    assert "Commands read the database only" in limits_card(None, None, Cooldowns()).text()
    text = limits_card(None, None, Cooldowns(), live_max_age=60).text()
    assert "commands also fetch from the source when an answer is over 60 s old" in text
    assert "odds: 2 min" in text  # twice the live max age, not twice the refresh interval
