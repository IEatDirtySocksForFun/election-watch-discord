"""Request budgets, the demand queue, user cooldowns, and how the poller, commands and early
vote use them: sources are asked only when due, commands never call a source."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import respx

from electionwatch.bot.cooldown import Cooldowns
from electionwatch.bot.logic import change_limits, limits_text
from electionwatch.demand import Demand
from electionwatch.models import OddsSnapshot, Poll, PollResult
from electionwatch.poller import POLLS_CACHE, Poller, load_polls, polls_ident
from electionwatch.racekeys import parse
from electionwatch.ratelimit import FLOORS, Limit, RateBook, parse_limits, parse_seconds
from electionwatch.settings import GuildSettings
from electionwatch.sources.base import OddsSource, PollSource, ResultsSource
from electionwatch.sources.early_vote import API, CACHE_KIND, EarlyVoteClient, NotCached
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
TX = parse("TX SEN")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Request:
    def __init__(self, host: str) -> None:
        self.url = httpx.URL(f"https://{host}/x")


async def spend(book: RateBook, name: str, kind: str, host: str, n: int) -> None:
    with book.track(name, kind):
        for _ in range(n):
            await book.on_request(Request(host))


# --------------------------------------------------------------------------- the math
def test_allowance_is_half_the_published_limit_per_hour():
    book = RateBook()
    assert book.allowance("gamma-api.polymarket.com") == 300 * 360 / 2       # 300 per 10 s
    assert book.allowance("en.wikipedia.org") == 200 * 60 / 2                # 200 per minute
    assert book.allowance("politics.api.cnn.io") == 10 * 60 / 2              # parent domain cnn.io
    assert book.allowance("unknown.example.org") == 3600 / 2                 # a request a second
    assert RateBook(safety=4).allowance("civicapi.org") == 3600 / 4


async def test_interval_grows_with_requests_per_refresh_and_never_beats_the_floor():
    clock = Clock()
    book = RateBook({"feed.example": Limit(600, 3600)}, clock=clock)   # allowance 300/h
    await spend(book, "a:results", "results", "feed.example", 2)
    assert book.interval("a:results", "results") == FLOORS["results"]   # 2 x 3600/300 = 24 s < 60 s
    await spend(book, "a:results", "results", "feed.example", 50)
    assert book.interval("a:results", "results") == 50 * 3600 / 300     # 600 s: a big watch list
    assert book.status()[0]["requests_per_refresh"] == 50


async def test_due_waits_for_the_interval_and_for_room_in_the_hourly_allowance():
    clock = Clock()
    book = RateBook({"feed.example": Limit(200, 3600)}, clock=clock)   # allowance 100/h
    assert book.due("a:odds", "odds")
    await spend(book, "a:odds", "odds", "feed.example", 40)
    assert not book.due("a:odds", "odds")
    clock.t += 3600 * 40 / 100                                         # its computed interval
    assert book.due("a:odds", "odds")
    await spend(book, "b:calls", "calls", "feed.example", 40)          # another source, same host
    clock.t += 3600 * 40 / 100
    assert book.used("feed.example") == 80
    assert not book.due("a:odds", "odds")                              # 80 + 40 > 100
    clock.t += 3600                                                    # the hour rolls over
    assert book.due("a:odds", "odds")


async def test_a_refresh_bigger_than_the_allowance_still_runs_once_an_hour():
    clock = Clock()
    book = RateBook({"feed.example": Limit(20, 3600)}, clock=clock)    # allowance 10/h
    await spend(book, "a:results", "results", "feed.example", 30)
    clock.t += 3600 * 3 + 1
    assert book.due("a:results", "results")


async def test_operator_intervals_replace_the_computed_ones_and_force_overrides_the_wait():
    clock = Clock()
    book = RateBook(intervals={"odds": 300, "polymarket:odds": 900}, clock=clock)
    await spend(book, "kalshi:odds", "odds", "api.elections.kalshi.com", 1)
    await spend(book, "polymarket:odds", "odds", "gamma-api.polymarket.com", 1)
    assert book.interval("kalshi:odds", "odds") == 300
    assert book.interval("polymarket:odds", "odds") == 900
    assert not book.due("kalshi:odds", "odds")
    assert set(book.force({"odds"})) == {"kalshi:odds", "polymarket:odds"}
    assert book.due("kalshi:odds", "odds") and book.due("polymarket:odds", "odds")


def test_env_parsing_skips_bad_parts():
    assert parse_limits("civicapi.org=600/60, bad, x=1/0, api.votehub.com=1000") == {
        "civicapi.org": Limit(600, 60, "EW_RATE_LIMITS"),
        "api.votehub.com": Limit(1000, 3600, "EW_RATE_LIMITS"),
    }
    assert parse_seconds("odds=120,polls=x,polymarket=-1, results = 30") == {"odds": 120, "results": 30}
    book = RateBook.from_env({"EW_RATE_SAFETY": "3", "EW_RATE_LIMITS": "civicapi.org=60/60",
                              "EW_INTERVALS": "polls=600"})
    assert (book.safety, book.allowance("civicapi.org"), book.intervals) == (3, 1200, {"polls": 600})
    assert RateBook.from_env({"EW_RATE_SAFETY": "nonsense"}).safety == 2


def test_demand_expires_and_is_capped():
    clock = Clock()
    demand = Demand(limit=2, ttl=60, clock=clock)
    assert demand.add("odds", "TX SEN") and demand.add("odds", "GA SEN")
    assert not demand.add("odds", "NV SEN")
    assert demand.add("odds", "TX SEN")                 # a repeat refreshes its time
    clock.t += 61
    assert demand.items("odds") == [] and demand.add("odds", "NV SEN")


def test_user_cooldown_is_twice_the_refresh_and_can_be_overridden():
    clock = Clock()
    limits = Cooldowns(clock=clock)
    seconds = limits.user_seconds(60)
    assert seconds == 120
    assert limits.hit(1, "odds", "TX SEN", seconds) == 0
    assert limits.hit(1, "odds", "tx sen", seconds) == 120            # same target, any case
    assert limits.hit(1, "odds", "GA SEN", seconds) == 0              # other targets are fine
    assert limits.hit(2, "odds", "TX SEN", seconds) == 0              # other users are fine
    clock.t += 120
    assert limits.hit(1, "odds", "TX SEN", seconds) == 0
    assert Cooldowns(user_cooldown=10).user_seconds(60) == 10
    assert Cooldowns(user_cooldown=10).user_seconds(60, server=0) == 0


def test_hourly_cap_and_refresh_cooldown():
    clock = Clock()
    limits = Cooldowns(user_hourly=2, clock=clock)
    assert limits.hit(1, "odds", "a", 0) == limits.hit(1, "polls", "b", 0) == 0
    assert limits.hit(1, "odds", "c", 0) == 3600
    assert limits.hit(1, "odds", "c", 0, hourly=0) == 0                # server: no cap
    assert limits.refresh_seconds(60) == 120
    assert limits.refresh_hit(7, 120) == 0 and limits.refresh_hit(7, 120) == 120
    assert Cooldowns.from_env({"EW_USER_COOLDOWN": "5", "EW_USER_HOURLY": "x",
                               "EW_REFRESH_COOLDOWN": "-3"}).user_cooldown == 5


def test_change_limits_and_the_limits_text():
    gs = GuildSettings(1)
    assert change_limits(gs, user_cooldown=30, user_hourly=0) == \
        "Limits set: user cooldown 30, commands per user per hour 0"
    assert (gs.user_cooldown_seconds, gs.user_commands_per_hour) == (30, 0)
    change_limits(gs, user_cooldown=-1)
    assert gs.user_cooldown_seconds is None
    with pytest.raises(ValueError):
        change_limits(gs)
    with pytest.raises(ValueError):
        change_limits(gs, refresh_cooldown=-5)
    text = limits_text(gs, RateBook(), Cooldowns())
    assert "odds: every 60 s" in text and "polls: every 30 min" in text
    assert "1/2 of its published limit" in text and "per user per hour: no cap" in text


# --------------------------------------------------------------------------- the poller
class CountingResults(ResultsSource):
    name = "official"
    states = frozenset({"TX"})

    def __init__(self):
        self.calls = 0

    async def fetch(self, keys, client):
        self.calls += 1
        from electionwatch.models import CandidateResult, RaceResult
        return [RaceResult(TX, [CandidateResult("A", "DEM", 10)], self.name, NOW)]


class Fallback(CountingResults):
    name = "fallback"
    fallback = True


class CountingOdds(OddsSource):
    name = "odds"

    def __init__(self):
        self.calls = 0

    async def fetch_odds(self, keys, client):
        self.calls += 1
        return [OddsSnapshot(key, "kalshi", "REP", 0.6, NOW) for key in keys if key == TX]


class CountingPolls(PollSource):
    name = "votehub"

    def __init__(self):
        self.calls = 0

    async def fetch_polls(self, keys, client):
        self.calls += 1
        return [Poll(TX, "Pollster", [PollResult("A", "DEM", 50.0)], date(2026, 10, 30))]


class Poster:
    async def post(self, *args):
        pass


async def make_poller(clock, **sources):
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=2, important=["TX SEN"]))
    book = RateBook(intervals={"results": 120}, clock=lambda: clock.t)
    demand = Demand(clock=lambda: clock.t)
    poller = Poller(store, sources, Poster(), lambda: NOW, object(), rates=book, demand=demand)
    return store, poller


async def test_a_source_that_is_not_due_answers_with_its_last_results_not_the_fallbacks():
    clock = Clock()
    official, fallback = CountingResults(), Fallback()
    store, poller = await make_poller(clock, results=[official, fallback])
    await poller.tick()
    clock.t += 60
    await poller.tick()                        # official not due (120 s): its answer is reused
    assert (official.calls, fallback.calls) == (1, 0)
    clock.t += 60
    await poller.tick()
    assert (official.calls, fallback.calls) == (2, 0)


async def test_odds_and_polls_refresh_on_their_own_schedule_and_include_demanded_races():
    clock = Clock()
    odds, polls = CountingOdds(), CountingPolls()
    store, poller = await make_poller(clock, odds=[odds], polls=[polls])
    poller.demand.add("polls", "GA SEN")
    await poller.tick()
    hit = await store.get_cached(POLLS_CACHE, polls_ident("votehub", TX))
    assert [p.pollster for p in load_polls(hit[1])] == ["Pollster"]
    assert load_polls((await store.get_cached(POLLS_CACHE, polls_ident("votehub", parse("GA SEN"))))[1]) == []
    for _ in range(10):
        clock.t += 60
        await poller.tick()
    assert odds.calls == 11                    # every minute
    assert polls.calls == 1                    # every 30 min
    clock.t += FLOORS["polls"]
    await poller.tick()
    assert polls.calls == 2


# --------------------------------------------------------------------------- early vote
@respx.mock
async def test_commands_read_early_vote_pages_the_refresher_stored():
    store = MemoryStore()
    national = {"regions": {"TX": {"Democratic": {"votes": 10}}}, "statewide_total": 10,
                "snapshot_date": "2026-10-30"}
    respx.get(url__startswith=f"{API}/US/").respond(json=national)
    route = respx.get(url__startswith=f"{API}/TX").respond(404)
    respx.get(url__startswith=f"{API}/historical").respond(404)
    async with httpx.AsyncClient() as client:
        reader = EarlyVoteClient(client, store=store, cache_only=True, min_interval=0)
        with pytest.raises(NotCached):
            await reader.national()
        refresher = EarlyVoteClient(client, store=store, min_interval=0)
        assert await refresher.refresh() == 1
        calls = len(respx.calls)
        ev = await reader.state("TX", counties=True, age=True)
        assert ev is not None and ev.total_ballots == 20   # returned + in person
        assert len(respx.calls) == calls        # the reader made no request
    assert route.called
    assert any(ident.startswith(f"{API}/US/") for ident in await store.cached_idents(CACHE_KIND))


async def test_early_vote_embed_says_loading_when_nothing_is_stored():
    from electionwatch.bot.early_vote import early_vote_embed

    reader = EarlyVoteClient(object(), store=MemoryStore(), cache_only=True)
    embed = await early_vote_embed(reader, "TX", None)
    assert "still being loaded" in embed.description
