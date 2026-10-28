"""Replay sandbox: timeline loading, ReplaySource, scripted calls/odds, the run harness."""

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from electionwatch.poster import Post  # noqa: E402
from electionwatch.racekeys import parse  # noqa: E402
from electionwatch.settings import GuildSettings, PostMode, post_due  # noqa: E402
from electionwatch.sources.base import REGISTRY  # noqa: E402
from replay import run as replay_run  # noqa: E402
from replay.clock import FakeClock  # noqa: E402
from replay.scripted import ScriptedCallSource, ScriptedOddsSource, default_calls, default_swings  # noqa: E402
from replay.source import ReplaySource  # noqa: E402
from replay.timeline import load_year, snapshot_from_line, snapshot_to_line  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "replay"
T0 = datetime(2024, 11, 6, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def timelines():
    return load_year(FIX, "2024", ["PA SEN"])


def test_load_year(timelines):
    snaps = timelines["PA SEN"]
    assert [s.result.total_votes for s in snaps] == [2000, 2300, 10600, 19100]
    assert all(s.synthetic for s in snaps)
    assert snaps[0].result.candidates[0].pct == 60.0


def test_load_year_missing_race():
    with pytest.raises(FileNotFoundError):
        load_year(FIX, "2024", ["TX SEN"])


def test_line_roundtrip(timelines):
    s = timelines["PA SEN"][2]
    again = snapshot_from_line(json.loads(json.dumps(snapshot_to_line(s.result, s.ts, True))))
    assert again.result.fingerprint() == s.result.fingerprint() and again.ts == s.ts


def test_fake_clock():
    c = FakeClock(datetime(2024, 11, 6))
    assert c().tzinfo is not None
    assert c.advance(5) - timedelta(minutes=5) == datetime(2024, 11, 6, tzinfo=timezone.utc)


async def test_replay_source_serves_current_snapshot(timelines):
    clock = FakeClock(T0)
    src = ReplaySource(timelines, clock)
    key = parse("PA SEN")
    assert "replay" in REGISTRY["results"]
    assert await src.fetch([key]) == []  # before the first snapshot
    clock.set(T0 + timedelta(minutes=6))
    (r,) = await src.fetch([key, parse("TX SEN")])  # unknown race skipped
    assert r.total_votes == 2000 and r.fetched_at == clock()
    clock.set(T0 + timedelta(minutes=19, seconds=59))
    assert (await src.fetch([key]))[0].total_votes == 2300
    clock.set(T0 + timedelta(hours=5))
    assert (await src.fetch([key]))[0].total_votes == 19100
    assert await src.list_races("PA", None) == [(key, "Pennsylvania Senate")]


async def test_scripted_call_and_odds(timelines):
    clock = FakeClock(T0)
    src = ReplaySource(timelines, clock)
    (call,) = default_calls(src, call_at_eevp=95)
    assert call.winner == "Dave McCormick" and call.at == datetime(2024, 11, 6, 2, 10, tzinfo=timezone.utc)
    calls = ScriptedCallSource([call], clock)
    key = parse("PA SEN")
    assert await calls.fetch_calls([key]) == []
    clock.set(call.at)
    assert [c.winner for c in await calls.fetch_calls([key])] == ["Dave McCormick"]

    odds = ScriptedOddsSource(src, clock, default_swings(src, after_min=10, minutes=10))
    clock.set(T0 + timedelta(minutes=6))
    before = {o.outcome: o.prob for o in await odds.fetch_odds([key])}
    assert before["DEM"] > 0.5 and abs(before["DEM"] + before["REP"] - 1) < 1e-6
    clock.set(T0 + timedelta(minutes=5 + 10 + 10))  # end of the forced swing
    after = {o.outcome: o.prob for o in await odds.fetch_odds([key])}
    assert after["REP"] == pytest.approx(0.8)


class StubPoller:
    """Minimal stand-in with the documented constructor, so the harness is tested even
    before electionwatch.poller lands. Results follow post_due(); calls post once."""

    def __init__(self, store, sources, poster, clock, http):
        self.store, self.sources, self.poster, self.clock = store, sources, poster, clock

    async def tick(self):
        now = self.clock()
        for gs in await self.store.all_guilds():
            keys = [parse(k) for k in gs.important]
            for src in self.sources["results"]:
                for r in await src.fetch(keys, None):
                    await self.store.upsert_result(r)
            for k in keys:
                last = await self.store.last_posted(gs.guild_id, "results", str(k))
                changed = await self.store.result_changed_at(k)
                updated = changed is not None and (last is None or changed > last)
                if post_due(gs, last, now, updated):
                    r = await self.store.latest_result(k)
                    await self.poster.post(
                        gs.guild_id,
                        gs.channel_id,
                        Post("results", str(k), str(k), [f"{c.name} {c.votes}" for c in r.candidates]),
                    )
                    await self.store.mark_posted(gs.guild_id, "results", str(k), now)
            for src in self.sources["calls"]:
                for c in await src.fetch_calls(keys, None):
                    if await self.store.record_call(c):
                        await self.poster.post(
                            gs.guild_id,
                            gs.channel_id,
                            Post("call", str(c.key), str(c.key), [f"{c.outlet}: {c.winner}"]),
                        )


async def test_harness_with_stub_poller(timelines):
    gs = GuildSettings(guild_id=1, channel_id=2, important=["PA SEN"])
    rep = await replay_run.replay(timelines, gs, poller_cls=StubPoller)
    kinds = [p.kind for _, p in rep.posts]
    assert kinds.count("call") == 1
    # 01:05 first post, 01:07 change waits for the 5-min rule -> 01:10, then 01:20, 02:10
    times = [t.strftime("%H:%M") for t, p in rep.posts if p.kind == "results"]
    assert times == ["01:05", "01:10", "01:20", "02:10"]
    assert rep.ok, rep.problems
    md = replay_run.render(rep, "test", gs)
    assert "| PA SEN | 4 | 1 |" in md and "### 01:05 UTC" in md


def test_check_flags_interval_and_duplicates():
    gs = GuildSettings(guild_id=1, post_mode=PostMode.INTERVAL, interval_minutes=15)
    p = Post("results", "PA SEN", "x", ["a"])
    rep = replay_run.check([(T0, p), (T0 + timedelta(minutes=3), p)], gs)
    assert len(rep.problems) == 2


def test_replay_end_to_end_with_real_poller(timelines, tmp_path):
    pytest.importorskip("electionwatch.poller")
    shutil.copytree(FIX / "timelines", tmp_path / "timelines")
    out = tmp_path / "out.md"
    code = replay_run.main(["--year", "2024", "--races", "PA SEN", "--data", str(tmp_path), "--out", str(out)])
    text = out.read_text()
    assert "## Summary" in text and "PA SEN" in text
    assert code == 0, text
