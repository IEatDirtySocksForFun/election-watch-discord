from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from electionwatch.models import (
    CandidateResult,
    ExitPoll,
    OddsSnapshot,
    RaceCall,
    RaceResult,
)
from electionwatch.poller import Poller, format_result
from electionwatch.poster import RecordingPoster
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode
from electionwatch.sources.base import CallSource, ExitPollSource, OddsSource, ResultsSource
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
KEY = parse("TX SEN")
NC_KEY = parse("NC-01")


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


class MultiResults(ResultsSource):
    """Returns one result per requested race (TX SEN and NC-01 are covered)."""

    name = "multi"
    states = frozenset({"TX", "NC"})

    async def fetch(self, keys, client):
        found = []
        for key in keys:
            if key == KEY:
                found.append(result())
            elif key == NC_KEY:
                found.append(
                    RaceResult(
                        NC_KEY,
                        [CandidateResult("Pat Roy", "DEM", 1_000), CandidateResult("Lee Ann", "REP", 900)],
                        self.name,
                        NOW,
                    ).finalize()
                )
        return found


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


class FakeOdds(OddsSource):
    name = "odds"

    async def fetch_odds(self, keys, client):
        return [OddsSnapshot(KEY, "kalshi", "REP", 0.70, NOW)]


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
    assert "🔴 Projected winner: Ted Cruz (REP)" in poster.sent[0][2].lines


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
async def test_odds_swing_uses_lazy_detector_and_posts_once_per_window(monkeypatch):
    import sys
    import types

    swings = types.ModuleType("electionwatch.swings")
    swings.detect_swing = lambda history, pts, window: history[-1].prob - history[0].prob >= pts / 100
    monkeypatch.setitem(sys.modules, "electionwatch.swings", swings)
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(
            1,
            channel_id=10,
            important=[str(KEY)],
            odds_alerts=True,
            odds_swing_pts=20,
            odds_window_minutes=60,
        )
    )
    await store.add_odds(OddsSnapshot(KEY, "kalshi", "REP", 0.40, NOW - timedelta(minutes=5)))
    poster = RecordingPoster()
    sources = {"odds": [FakeOdds()]}
    await poller(store, sources, poster).tick()
    await poller(store, sources, poster).tick()
    assert len(poster.sent) == 1
    assert poster.sent[0][2].kind == "odds_swing"
    assert "70.0%" in poster.sent[0][2].lines[0]


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


@pytest.mark.asyncio
async def test_odds_swing_matches_the_real_detector_signature(monkeypatch):
    """electionwatch/swings.py takes a timedelta window and returns (move, earlier, latest)."""
    import sys
    import types

    seen = {}

    def detect_swing(history, pts, window):
        seen["window"] = window
        assert isinstance(window, timedelta), "swings.detect_swing expects a timedelta"
        latest = max(history, key=lambda snapshot: snapshot.ts)
        earlier = min(history, key=lambda snapshot: snapshot.ts)
        move = round((latest.prob - earlier.prob) * 100.0, 2)
        return None if abs(move) < pts else (move, earlier, latest)

    swings = types.ModuleType("electionwatch.swings")
    swings.detect_swing = detect_swing
    monkeypatch.setitem(sys.modules, "electionwatch.swings", swings)

    store = MemoryStore()
    await store.save_guild(
        GuildSettings(
            1, channel_id=10, important=[str(KEY)], odds_alerts=True,
            odds_swing_pts=20, odds_window_minutes=45,
        )
    )
    await store.add_odds(OddsSnapshot(KEY, "kalshi", "REP", 0.40, NOW - timedelta(minutes=5)))
    poster = RecordingPoster()
    await poller(store, {"odds": [FakeOdds()]}, poster).tick()

    assert seen["window"] == timedelta(minutes=45)
    assert len(poster.sent) == 1
    post = poster.sent[0][2]
    assert post.lines[0] == "kalshi · REP: 70.0% (+30.0 pts)"
    assert post.lines[1] == "Swing within 45 minutes"


@pytest.mark.asyncio
async def test_slow_source_times_out_and_the_rest_of_the_tick_survives():
    import asyncio

    class SlowResults(FakeResults):
        name = "slow"

        async def fetch(self, keys, client):
            await asyncio.sleep(5)
            return [self.value]

    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    good = FakeResults()
    race_poller = Poller(
        store, {"results": [SlowResults(), good]}, poster, lambda: NOW, object(), fetch_timeout=0.05
    )
    await race_poller.tick()
    assert good.calls == 1
    assert len(poster.sent) == 1


@pytest.mark.asyncio
async def test_calls_are_posted_immediately_even_on_a_long_interval():
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(
            1,
            channel_id=10,
            important=[str(KEY)],
            post_mode=PostMode.INTERVAL,
            interval_minutes=60,
            call_outlets=["AP"],
        )
    )
    await store.mark_posted(1, "results", str(KEY), NOW - timedelta(minutes=1))
    poster = RecordingPoster()
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW)
    await poller(store, {"calls": [FakeCalls(call)]}, poster).tick()
    assert [post.kind for _, _, post in poster.sent] == ["call"]


@pytest.mark.asyncio
async def test_call_and_exit_poll_toggles_are_respected():
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(1, channel_id=10, important=[str(KEY)], post_calls=False, post_exit_polls=False)
    )
    poster = RecordingPoster()
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW)
    sources = {"calls": [FakeCalls(call)], "exitpolls": [FakeExitPolls()]}
    await poller(store, sources, poster).tick()
    assert poster.sent == []


def test_turnout_line_is_only_added_when_there_are_numbers():
    empty = RaceResult(KEY, [CandidateResult("A", "REP", 0)], "fake", NOW).finalize()
    assert not any(line.startswith("Turnout") for line in format_result(empty, turnout=True).lines)
    assert not any(line.startswith("Turnout") for line in format_result(result(), turnout=False).lines)
    with_turnout = format_result(result(), turnout=True).lines
    assert any(line.startswith("Turnout: 51.3%") for line in with_turnout)


@pytest.mark.asyncio
async def test_explicitly_passed_sources_are_used_even_when_not_enabled_by_default():
    """The replay sandbox hands the poller its own source (enabled_by_default = False)."""

    class SandboxResults(FakeResults):
        name = "sandbox"
        enabled_by_default = False

    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    await poller(store, {"results": [SandboxResults()]}, poster).tick()
    assert [post.kind for _, _, post in poster.sent] == ["results"]


@pytest.mark.asyncio
async def test_source_missing_its_api_key_is_skipped(monkeypatch):
    class KeyedResults(FakeResults):
        name = "keyed"
        env = ("EW_TEST_MISSING_KEY",)

        async def fetch(self, keys, client):
            raise AssertionError("an unavailable adapter must not be fetched")

    monkeypatch.delenv("EW_TEST_MISSING_KEY", raising=False)
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    await poller(store, {"results": [KeyedResults()]}, poster).tick()
    assert poster.sent == []


@pytest.mark.asyncio
async def test_each_guild_only_gets_posts_for_its_own_watch_list():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    await store.save_guild(GuildSettings(2, channel_id=20, important=[str(NC_KEY)]))
    poster = RecordingPoster()
    await poller(store, {"results": [MultiResults()]}, poster).tick()

    by_guild: dict[int, set[str]] = {}
    for guild_id, _, post in poster.sent:
        by_guild.setdefault(guild_id, set()).add(post.race)
    assert by_guild == {1: {"TX SEN"}, 2: {"NC-01"}}


@pytest.mark.asyncio
async def test_empty_watch_list_falls_back_to_config_races():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10))
    poster = RecordingPoster()
    await poller(store, {"results": [MultiResults()]}, poster).tick()
    # config/races.yaml ships only TX SEN.
    assert [post.race for _, _, post in poster.sent] == ["TX SEN"]


def test_results_post_shows_only_top_five():
    from datetime import datetime, timezone
    from electionwatch.models import CandidateResult, RaceResult
    from electionwatch.poller import format_result
    from electionwatch.racekeys import parse
    cands = [CandidateResult(f"C{i}", "IND", 100 - i) for i in range(8)]
    post = format_result(RaceResult(parse("TX SEN"), cands, "t", datetime.now(timezone.utc)).finalize())
    names = [l for l in post.lines if l.split(" ", 1)[1].startswith("C")]
    assert [l.split()[1] for l in names] == ["C0", "C1", "C2", "C3", "C4"]


@pytest.mark.asyncio
async def test_unchanged_result_fetched_after_tick_start_is_not_reposted():
    # Adapters stamp fetched_at with the wall clock *during* the tick, i.e. after the tick's
    # `now`. That must not make an unchanged result look "changed since the last post".
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    clock = {"now": NOW}
    source = FakeResults(result(at=NOW + timedelta(seconds=5)))
    p = Poller(store, {"results": [source]}, poster, lambda: clock["now"], object())

    await p.tick()
    assert len(poster.sent) == 1
    for minutes in range(1, 16):
        clock["now"] = NOW + timedelta(minutes=minutes)
        source.value = result(at=clock["now"] + timedelta(seconds=5))   # same votes
        await p.tick()
    assert len(poster.sent) == 1


@pytest.mark.asyncio
async def test_immediate_mode_posts_each_change_once():
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(1, channel_id=10, important=[str(KEY)], post_mode=PostMode.IMMEDIATE)
    )
    poster = RecordingPoster()
    clock = {"now": NOW}
    source = FakeResults(result(at=NOW + timedelta(seconds=5)))
    p = Poller(store, {"results": [source]}, poster, lambda: clock["now"], object())
    await p.tick()
    clock["now"] = NOW + timedelta(minutes=1)
    source.value = result(at=clock["now"] + timedelta(seconds=5))
    await p.tick()
    clock["now"] = NOW + timedelta(minutes=2)
    source.value = result(votes=2_200_000, at=clock["now"] + timedelta(seconds=5))
    await p.tick()
    assert len(poster.sent) == 2


@pytest.mark.asyncio
async def test_results_sources_are_fetched_concurrently_in_priority_order():
    import asyncio
    import time

    class Slow(ResultsSource):
        states = frozenset({"TX"})

        def __init__(self, name, votes):
            self.name, self.votes = name, votes

        async def fetch(self, keys, client):
            await asyncio.sleep(0.3)
            return [result(votes=self.votes, source=self.name)]

    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=[str(KEY)]))
    poster = RecordingPoster()
    sources = [Slow("first", 2_300_000), Slow("second", 2_200_000), Slow("third", 2_100_000)]
    started = time.monotonic()
    await poller(store, {"results": sources}, poster).tick()
    assert time.monotonic() - started < 0.8      # 3 x 0.3 s if fetched one after another
    assert (await store.latest_result(KEY)).source == "first"
    assert poster.sent[0][2].footer == "Source: first"


@pytest.mark.asyncio
async def test_outlet_call_without_special_flag_reaches_a_watched_special_race():
    # OH/FL Senate 2026 are specials: state results key them "OH SEN S", but outlets and
    # markets have no special flag and answer for "OH SEN".
    special, plain = parse("OH SEN S"), parse("OH SEN")
    asked = []

    class PlainCalls(CallSource):
        name, outlet = "plain", "AP"

        async def fetch_calls(self, keys, client):
            asked.extend(keys)
            return [RaceCall(plain, "AP", "Jon Husted", "REP", NOW)] if plain in keys else []

    store = MemoryStore()
    await store.save_guild(GuildSettings(1, channel_id=10, important=["OH SEN S"]))
    poster = RecordingPoster()
    await poller(store, {"calls": [PlainCalls()]}, poster).tick()
    assert special in asked and plain in asked
    assert [(p.kind, p.race) for _, _, p in poster.sent] == [("call", "OH SEN S")]


def test_call_footer_is_in_utc_even_for_an_offset_timestamp():
    from electionwatch.poller import format_call

    eastern = timezone(timedelta(hours=-5))
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", datetime(2026, 11, 3, 21, 30, tzinfo=eastern))
    assert format_call(call).footer == "Called Nov 04 02:30 UTC"


def test_outlet_timestamps_without_offset_are_aware_utc():
    from electionwatch.sources.calls._common import parse_iso_ts

    assert parse_iso_ts("2026-11-04T01:30:00") == datetime(2026, 11, 4, 1, 30, tzinfo=timezone.utc)
    assert parse_iso_ts("2026-11-03T21:30:00-05:00").tzinfo == timezone.utc


async def test_identical_results_post_is_skipped():
    from datetime import datetime, timezone
    from electionwatch.poller import Poller
    from electionwatch.poster import Post, RecordingPoster
    from electionwatch.settings import GuildSettings, PostMode
    from electionwatch.store.memory import MemoryStore
    import httpx
    poster = RecordingPoster()
    now = datetime(2026, 11, 3, 23, tzinfo=timezone.utc)
    async with httpx.AsyncClient() as http:
        p = Poller(MemoryStore(), {}, poster, lambda: now, http)
        gs = GuildSettings(guild_id=1, channel_id=2, post_mode=PostMode.IMMEDIATE)
        post = Post("results", "TX SEN", "Texas Senate", ["🔴 A (REP) 10 · 60.0%"])
        await p._post(gs, "results", "TX SEN", post, now)
        await p._post(gs, "results", "TX SEN", post, now)
        await p._post(gs, "results", "TX SEN", Post("results", "TX SEN", "Texas Senate", ["🔴 A (REP) 11 · 61.0%"]), now)
    assert len(poster.sent) == 2


async def test_failing_source_backs_off_then_recovers():
    from datetime import datetime, timedelta, timezone
    import httpx
    from electionwatch.poller import Poller
    from electionwatch.poster import RecordingPoster
    from electionwatch.store.memory import MemoryStore

    class Flaky:
        name, calls, fail = "flaky", 0, True
        async def fetch(self, keys, http):
            Flaky.calls += 1
            if Flaky.fail:
                raise RuntimeError("406")
            return ["ok"]

    now = [datetime(2026, 11, 3, 23, tzinfo=timezone.utc)]
    async with httpx.AsyncClient() as http:
        p = Poller(MemoryStore(), {}, RecordingPoster(), lambda: now[0], http)
        src = Flaky()
        for _ in range(3):                       # fails once, then skipped for 1 minute
            try:
                await p._fetch(src, "fetch", [])
            except RuntimeError:
                pass
        assert Flaky.calls == 1
        now[0] += timedelta(minutes=1)
        Flaky.fail = False
        assert await p._fetch(src, "fetch", []) == ["ok"] and Flaky.calls == 2
        assert "flaky" not in p._backoff
