"""The same Store behaviour suite runs against memory and (when configured) Mongo."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from electionwatch.models import CandidateResult, OddsSnapshot, RaceCall, RaceResult
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings
from electionwatch.store.memory import MemoryStore
from electionwatch.store.mongo import MongoStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
KEY = parse("TX SEN")


@pytest.fixture(params=["memory", "mongo"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    uri = os.environ.get("EW_TEST_MONGO_URI")
    if not uri:
        pytest.skip("EW_TEST_MONGO_URI is not set")
    mongo = MongoStore(uri, database=f"electionwatch_test_{request.node.name}_{id(request)}")
    await mongo.create_indexes()
    yield mongo
    await mongo.client.drop_database(mongo.db.name)
    await mongo.close()


def result(votes: int, at: datetime = NOW) -> RaceResult:
    return RaceResult(
        KEY,
        [CandidateResult("Ted Cruz", "REP", votes), CandidateResult("Jane Doe", "DEM", 900)],
        "fake",
        at,
        precincts_reporting=10,
    ).finalize()


@pytest.mark.asyncio
async def test_settings_results_history_and_posts(store):
    guild = GuildSettings(7, channel_id=8, post_mode=PostMode.INTERVAL, interval_minutes=15)
    await store.save_guild(guild)
    assert await store.get_guild(7) == guild
    assert await store.all_guilds() == [guild]
    user = UserSettings(9, home_state="TX", favorites=[str(KEY)])
    await store.save_user(user)
    assert await store.get_user(9) == user

    assert await store.upsert_result(result(1000)) is True
    changed_at = await store.result_changed_at(KEY)
    assert changed_at == NOW
    assert await store.upsert_result(result(1000, NOW + timedelta(minutes=1))) is False
    assert await store.result_changed_at(KEY) == NOW
    assert await store.upsert_result(result(1100, NOW + timedelta(minutes=2))) is True
    latest = await store.latest_result(KEY)
    assert latest is not None and latest.candidates[0].votes == 1100
    assert latest.fetched_at == NOW + timedelta(minutes=2)

    assert await store.last_posted(7, "results", str(KEY)) is None
    await store.mark_posted(7, "results", str(KEY), NOW)
    assert await store.last_posted(7, "results", str(KEY)) == NOW


@pytest.mark.asyncio
async def test_calls_and_odds(store):
    call = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW)
    assert await store.record_call(call) is True
    assert await store.record_call(call) is False
    retracted = RaceCall(KEY, "AP", "Ted Cruz", "REP", NOW, retracted=True)
    assert await store.record_call(retracted) is True
    assert await store.calls_for(KEY) == [retracted]

    old = OddsSnapshot(KEY, "kalshi", "REP", 0.45, NOW - timedelta(hours=2))
    recent = OddsSnapshot(KEY, "kalshi", "REP", 0.55, NOW)
    other = OddsSnapshot(KEY, "kalshi", "DEM", 0.45, NOW)
    for snapshot in (old, recent, other):
        await store.add_odds(snapshot)
    assert await store.odds_since(KEY, "kalshi", "REP", NOW - timedelta(hours=1)) == [recent]
