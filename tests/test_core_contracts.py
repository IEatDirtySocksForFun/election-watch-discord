from datetime import datetime, timedelta, timezone

import pytest

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import RaceKeyError, describe, parse
from electionwatch.settings import GuildSettings, PostMode, post_due
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,canon,name", [
    ("NC-01", "NC-01", "North Carolina Congressional District 1"),
    ("nc-1", "NC-01", "North Carolina Congressional District 1"),
    ("AK-AL", "AK-AL", "Alaska At-Large Congressional District"),
    ("TX SEN", "TX SEN", "Texas Senate"),
    ("TX-SEN", "TX SEN", "Texas Senate"),
    ("OH SEN S", "OH SEN S", "Ohio Senate (Special)"),
    ("GA GOV", "GA GOV", "Georgia Governor"),
    ("TX SD-5", "TX SD-05", "Texas State Senate District 5"),
    ("TX HD 121", "TX HD-121", "Texas State House District 121"),
    ("TX LOCAL Harris County Judge", "TX LOCAL harris-county-judge", "Texas local: harris county judge"),
    ("Texas Senate", "TX SEN", "Texas Senate"),
    ("North Carolina Congressional District 1", "NC-01", "North Carolina Congressional District 1"),
    ("New York Governor", "NY GOV", "New York Governor"),
])
def test_parse_roundtrip(text, canon, name):
    k = parse(text)
    assert str(k) == canon
    assert parse(canon) == k
    assert describe(k) == name


@pytest.mark.parametrize("bad", ["", "ZZ SEN", "TX", "TX FOO", "TX LOCAL", "TX SEN X"])
def test_parse_rejects(bad):
    with pytest.raises(RaceKeyError):
        parse(bad)


def test_post_due_rule():
    gs = GuildSettings(guild_id=1)
    assert not post_due(gs, None, NOW, updated=False)
    assert post_due(gs, None, NOW, updated=True)
    assert not post_due(gs, NOW - timedelta(minutes=4), NOW, True)
    assert post_due(gs, NOW - timedelta(minutes=5), NOW, True)
    gs.post_mode, gs.interval_minutes = PostMode.INTERVAL, 15
    assert not post_due(gs, NOW - timedelta(minutes=10), NOW, True)
    assert post_due(gs, NOW - timedelta(minutes=15), NOW, True)
    gs.post_mode = PostMode.IMMEDIATE
    assert post_due(gs, NOW - timedelta(seconds=1), NOW, True)
    gs.paused = True
    assert not post_due(gs, None, NOW, True)


async def test_memory_store_change_detection():
    s = MemoryStore()
    key = parse("TX SEN")
    r = RaceResult(key, [CandidateResult("A", "REP", 10), CandidateResult("B", "DEM", 30)],
                   "test", NOW).finalize()
    assert r.candidates[0].name == "B" and r.candidates[0].pct == 75.0
    assert await s.upsert_result(r) is True
    assert await s.upsert_result(r) is False
    r.candidates[1].votes += 1
    assert await s.upsert_result(r) is True
    assert len(s.history) == 2
