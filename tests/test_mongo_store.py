"""The same Store behaviour suite runs against memory and (when configured) Mongo.

The ``fake`` parameter runs MongoStore against a tiny in-memory stand-in for pymongo so the
document round-trips are exercised even on a machine without a MongoDB server.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from electionwatch.models import CandidateResult, OddsSnapshot, RaceCall, RaceResult
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings
from electionwatch.store.memory import MemoryStore
from electionwatch.store.mongo import MongoStore

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
KEY = parse("TX SEN")


def _match(doc: dict, query: dict) -> bool:
    for field, expected in query.items():
        actual = doc.get(field)
        if isinstance(expected, dict) and "$gte" in expected:
            if actual is None or actual < expected["$gte"]:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, field: str, direction: int = 1):
        self._docs.sort(key=lambda doc: doc.get(field), reverse=direction < 0)
        return self

    async def _iterate(self):
        for doc in self._docs:
            yield doc

    def __aiter__(self):
        return self._iterate()


class FakeCollection:
    """Only the operations MongoStore uses, with Mongo's upsert/document semantics."""

    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list[tuple[list, dict]] = []

    async def create_index(self, keys, **options):
        self.indexes.append((list(keys), options))

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _match(doc, query):
                if projection:
                    return {field: doc[field] for field in projection if field in doc}
                return dict(doc)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([dict(doc) for doc in self.docs if _match(doc, query or {})])

    async def aggregate(self, pipeline):
        """The one pipeline MongoStore.latest_odds sends: $match, $sort ts desc, $group $first."""
        match, _sort, group = pipeline
        docs = sorted((d for d in self.docs if _match(d, match["$match"])), key=lambda d: d["ts"], reverse=True)
        assert _sort["$sort"] == {"market": 1, "outcome": 1, "ts": -1}
        first: dict = {}
        for doc in docs:
            first.setdefault((doc["market"], doc["outcome"]), doc)
        return _Cursor([{"_id": ident, "doc": dict(doc)} for ident, doc in first.items()])

    def _stored(self, query, body):
        stored = {key: value for key, value in query.items() if not isinstance(value, dict)}
        stored.update(body)
        stored.setdefault("_id", len(self.docs) + 1)
        return stored

    async def replace_one(self, query, doc, upsert=False):
        for index, existing in enumerate(self.docs):
            if _match(existing, query):
                stored = dict(doc)
                stored.setdefault("_id", existing.get("_id"))
                self.docs[index] = stored
                return
        if upsert:
            self.docs.append(self._stored(query, dict(doc)))

    async def insert_one(self, doc):
        self.docs.append(self._stored({}, dict(doc)))

    async def update_one(self, query, update, upsert=False):
        for existing in self.docs:
            if _match(existing, query):
                existing.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append(self._stored(query, dict(update.get("$set", {}))))


class FakeDatabase:
    def __init__(self, name: str) -> None:
        self.name = name
        self.collections: dict[str, FakeCollection] = {}

    def __getattr__(self, item: str) -> FakeCollection:
        return self.collections.setdefault(item, FakeCollection())


class FakeMongoClient:
    """Client-shaped stand-in so MongoStore can run without a server."""

    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}
        self.admin = SimpleNamespace(command=self._ping)
        self.closed = False

    async def _ping(self, name: str):
        return {"ok": 1}

    def __getitem__(self, name: str) -> FakeDatabase:
        return self.databases.setdefault(name, FakeDatabase(name))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(params=["memory", "fake", "mongo"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    if request.param == "fake":
        fake = MongoStore("mongodb://unused", database="ew_test", client=FakeMongoClient())
        await fake.create_indexes()
        yield fake
        return
    uri = os.environ.get("EW_TEST_MONGO_URI")
    if not uri:
        pytest.skip("EW_TEST_MONGO_URI is not set")
    mongo = MongoStore(uri, database=f"ew_test_{uuid.uuid4().hex[:12]}")
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


def fake_store() -> MongoStore:
    return MongoStore("mongodb://unused", database="ew_test", client=FakeMongoClient())


@pytest.mark.asyncio
async def test_create_indexes_covers_the_documented_indexes():
    store = fake_store()
    await store.create_indexes()

    def indexes(collection):
        return {tuple(fields): options for fields, options in collection.indexes}

    results = indexes(store.results)
    assert results[(("key", 1),)]["unique"] is True
    assert (("key", 1), ("fetched_at", 1)) in indexes(store.result_history)
    assert indexes(store.calls)[(("key", 1), ("outlet", 1))]["unique"] is True
    assert indexes(store.posts)[(("guild_id", 1), ("kind", 1), ("ref", 1))]["unique"] is True
    ttl = [options for options in indexes(store.odds).values() if "expireAfterSeconds" in options]
    assert ttl and ttl[0]["expireAfterSeconds"] == 3 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_mongo_keeps_one_history_snapshot_per_change_and_round_trips_keys():
    store = fake_store()
    await store.create_indexes()
    special = parse("NC-01 S")
    first = RaceResult(
        special,
        [CandidateResult("A", "DEM", 10), CandidateResult("B", "REP", 20)],
        "fake",
        NOW,
        url="https://example.test/results",
        title="North Carolina CD 1 (Special)",
        registered_voters=1_000,
    ).finalize()
    assert await store.upsert_result(first) is True
    assert await store.upsert_result(first) is False
    assert len(store.result_history.docs) == 1

    latest = await store.latest_result(special)
    assert latest.key == special and latest.title == "North Carolina CD 1 (Special)"
    assert latest.url == "https://example.test/results"
    assert [candidate.name for candidate in latest.candidates] == ["B", "A"]

    second = RaceResult(special, [CandidateResult("B", "REP", 30)], "fake", NOW + timedelta(minutes=1))
    assert await store.upsert_result(second.finalize()) is True
    assert len(store.result_history.docs) == 2
    assert await store.result_changed_at(special) == NOW + timedelta(minutes=1)


def test_server_selection_fails_fast_unless_the_uri_says_otherwise():
    """A MongoDB restart must not hold slash commands past Discord's 3 s deadline."""
    fast = MongoStore("mongodb://127.0.0.1:1", database="ew_test")
    assert fast.client.options.server_selection_timeout <= 2.5
    slow = MongoStore("mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=9000", database="ew_test")
    assert slow.client.options.server_selection_timeout == 9.0


async def test_latest_odds_is_the_newest_snapshot_per_market_and_outcome(store):
    for minutes, prob in ((0, 0.5), (5, 0.6), (3, 0.55)):
        await store.add_odds(OddsSnapshot(KEY, "kalshi", "REP", prob, NOW + timedelta(minutes=minutes)))
    await store.add_odds(OddsSnapshot(KEY, "polymarket", "REP", 0.7, NOW))
    await store.add_odds(OddsSnapshot(parse("GA SEN"), "kalshi", "REP", 0.1, NOW))
    latest = {(s.market, s.outcome): s.prob for s in await store.latest_odds(KEY)}
    assert latest == {("kalshi", "REP"): 0.6, ("polymarket", "REP"): 0.7}
    assert await store.latest_odds(parse("NV SEN")) == []


async def test_cached_pages_round_trip_by_kind(store):
    assert await store.get_cached("polls", "TX SEN|votehub") is None
    await store.put_cached("polls", "TX SEN|votehub", "[]", NOW)
    await store.put_cached("polls", "TX SEN|votehub", '[{"a": 1}]', NOW + timedelta(minutes=30))
    await store.put_cached("earlyvote", "https://civicapi.org/x", "null", NOW)
    at, data = await store.get_cached("polls", "TX SEN|votehub")
    assert (at, data) == (NOW + timedelta(minutes=30), '[{"a": 1}]')
    assert await store.cached_idents("polls") == {"TX SEN|votehub": NOW + timedelta(minutes=30)}
    assert list(await store.cached_idents("earlyvote")) == ["https://civicapi.org/x"]
