from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from electionwatch.models import (
    CandidateResult,
    ExitPoll,
    RaceCall,
    RaceResult,
)
from electionwatch.poller import Poller
from electionwatch.poster import RecordingPoster
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode
from electionwatch.sources.base import CallSource, ExitPollSource, ResultsSource
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
KEY = parse("TX SEN")


def result(votes: int = 2_104_331, at: datetime = NOW, source: str = "good") -> RaceResult:
    return RaceResult(
        KEY,
        [
            CandidateResult("Ted Cruz", "REP", votes, 51.2),
            CandidateResult("Jane Doe", "DEM", 2_000_000, 48.8),
        ],
        source,
        at,
        precincts_reporting=4_812,
        precincts_total=7_893,
        expected_vote_pct=62,
        registered_voters=8_000_000,
        turnout_pct=51.3,
    ).finalize()


class FakeResults(ResultsSource):
    name = "good"
    states = frozenset({"TX"})

    def __init__(self, value=None):
        self.value = value or result()
        self.calls = 0

    async def fetch(self, keys, client):
        self.calls += 1
        return [self.value]


class BrokenResults(FakeResults):
    name = "broken"

    async def fetch(self, keys, client):
        raise RuntimeError("endpoint unavailable")


class FakeCalls(CallSource):
    name = "calls"
    outlet = "AP"

    def __init__(self, call):
        self.call = call

    async def fetch_calls(self, keys, client):
        return [self.call]


class BrokenCalls(FakeCalls):
    name = "broken_calls"

    async def fetch_calls(self, keys, client):
        raise RuntimeError("endpoint unavailable")


class FakeExitPolls(ExitPollSource):
    name = "exit"

    async def fetch_exit_polls(self, keys, client):
        return [ExitPoll(KEY, "AP", "https://example.test/poll", "Women: 52% Doe")]


def poller(store, sources, poster):
    return Poller(store, sources, poster, lambda: NOW, object())


@pytest.mark.asyncio
async def test_interval_uses_change_since_guilds_last_post_and_formats_turnout():
    store = MemoryStore()
    guild = GuildSettings(
        1,
        channel_id=10,
        important=[str(KEY)],
        post_mode=PostMode.INTERVAL,
        interval_minutes=15,
        post_turnout=True,
    )
    await store.save_guild(guild)
    await store.mark_posted(1, "results", str(KEY), NOW - timedelta(minutes=15))
    poster = RecordingPoster()
    source = FakeResults(result(at=NOW - timedelta(minutes=10)))

    await poller(store, {"results": [source]}, poster).tick()
    assert len(poster.sent) == 1
    guild_id, channel_id, post = poster.sent[0]
    assert (guild_id, channel_id) == (1, 10)
    assert post.lines[0] == "🔴 Ted Cruz (REP) 2,104,331 · 51.2%"
    assert "62% expected vote · 4,812/7,893 precincts" in post.lines
    assert any(line.startswith("Turnout: 51.3%") for line in post.lines)

    # No new change: another tick must not repost merely because the interval elapsed.
    await poller(store, {"results": [source]}, poster).tick()
    assert len(poster.sent) == 1


@pytest.mark.asyncio
async def test_default_interval_not_due_and_source_failure_isolated():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    await store.mark_posted(1, "results", str(KEY), NOW - timedelta(minutes=4))
    poster = RecordingPoster()
    good = FakeResults()
    await poller(store, {"results": [BrokenResults(), good]}, poster).tick()
    assert poster.sent == []
    assert good.calls == 1


@pytest.mark.asyncio
async def test_first_result_source_wins_and_guild_source_restrictions():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    await store.save_guild(
        GuildSettings(2, channel_id=20, important=[str(KEY)], results_sources=["second"])
    )
    first = FakeResults(result(votes=100, source="first"))
    second = FakeResults(result(votes=200, source="second"))
    poster = RecordingPoster()
    await poller(store, {"results": [first, second]}, poster).tick()

    latest = await store.latest_result(KEY)
    assert latest.source == "first"
    assert next(c.votes for c in latest.candidates if c.name == "Ted Cruz") == 100
    by_guild = {guild: post for guild, _, post in poster.sent}
    assert any("Ted Cruz (REP) 100" in line for line in by_guild[1].lines)
    assert any("Ted Cruz (REP) 200" in line for line in by_guild[2].lines)


@pytest.mark.asyncio
async def test_calls_respect_guild_outlets_and_survive_another_source_failure():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)], call_outlets=["AP"]))
    await store.save_guild(GuildSettings(2, channel_id=20, important=[str(KEY)], call_outlets=["CNN"]))
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW)
    poster = RecordingPoster()
    sources = {"calls": [BrokenCalls(call), FakeCalls(call)]}

    await poller(store, sources, poster).tick()
    await poller(store, sources, poster).tick()
    assert len(poster.sent) == 1
    assert poster.sent[0][0] == 1
    assert poster.sent[0][2].kind == "call"
    assert "Projected winner: Ted Cruz (REP)" in poster.sent[0][2].lines


@pytest.mark.asyncio
async def test_exit_poll_posts_once_per_guild_race_and_outlet():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    sources = {"exitpolls": [FakeExitPolls()]}
    await poller(store, sources, poster).tick()
    await poller(store, sources, poster).tick()
    assert len(poster.sent) == 1
    assert poster.sent[0][2].kind == "exitpoll"


@pytest.mark.asyncio
async def test_paused_guild_suppresses_all_posts():
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(1, channel_id=10, important=[str(KEY)], paused=True)
    )
    poster = RecordingPoster()
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW)
    sources = {"results": [FakeResults()], "calls": [FakeCalls(call)], "exitpolls": [FakeExitPolls()]}
    await poller(store, sources, poster).tick()
    assert poster.sent == []
