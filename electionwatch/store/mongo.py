"""MongoDB persistence using pymongo's native asyncio client."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.errors import ServerSelectionTimeoutError

from electionwatch.models import (
    CandidateResult,
    OddsSnapshot,
    RaceCall,
    RaceKey,
    RaceResult,
    to_doc,
)
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings

_ODDS_TTL_SECONDS = 3 * 24 * 60 * 60
# pymongo waits 30 s for a server by default. While MongoDB restarts, every slash command
# would then hang past Discord's 3 s interaction deadline (the user sees "did not respond")
# and every poller tick would stall for 30 s. Fail fast instead; a URI setting still wins.
SERVER_SELECTION_TIMEOUT_MS = 2000
STARTUP_WAIT_SECONDS = 30.0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _guild(doc: dict[str, Any]) -> GuildSettings:
    data = {key: value for key, value in doc.items() if key != "_id"}
    data["guild_id"] = doc["_id"]
    data["post_mode"] = PostMode(data.get("post_mode", PostMode.DEFAULT.value))
    return GuildSettings(**data)


def _user(doc: dict[str, Any]) -> UserSettings:
    data = {key: value for key, value in doc.items() if key != "_id"}
    data["user_id"] = doc["_id"]
    return UserSettings(**data)


def _result(doc: dict[str, Any]) -> RaceResult:
    data = {key: value for key, value in doc.items() if not key.startswith("_") and key != "changed_at"}
    data["key"] = parse(data["key"])
    data["fetched_at"] = _utc(data["fetched_at"])
    if data.get("source_updated_at"):
        data["source_updated_at"] = _utc(data["source_updated_at"])
    data["candidates"] = [CandidateResult(**candidate) for candidate in data["candidates"]]
    return RaceResult(**data)


def _call(doc: dict[str, Any]) -> RaceCall:
    data = {key: value for key, value in doc.items() if key != "_id"}
    data["key"] = parse(data["key"])
    data["called_at"] = _utc(data["called_at"])
    return RaceCall(**data)


def _odds(doc: dict[str, Any]) -> OddsSnapshot:
    data = {key: value for key, value in doc.items() if key != "_id"}
    data["key"] = parse(data["key"])
    data["ts"] = _utc(data["ts"])
    return OddsSnapshot(**data)


class MongoStore:
    def __init__(self, uri: str, database: str = "electionwatch", *, client: AsyncMongoClient | None = None):
        options = (
            {}
            if "serverselectiontimeoutms" in uri.lower()
            else {"serverSelectionTimeoutMS": SERVER_SELECTION_TIMEOUT_MS}
        )
        self.client = client or AsyncMongoClient(uri, tz_aware=True, **options)
        self.db = self.client[database]
        self.guilds = self.db.guilds
        self.users = self.db.users
        self.results = self.db.results
        self.result_history = self.db.result_history
        self.calls = self.db.calls
        self.odds = self.db.odds
        self.posts = self.db.posts
        self.cache = self.db.cache
        self._indexes_ready = False

    async def create_indexes(self) -> None:
        await self.results.create_index([("key", ASCENDING)], unique=True)
        await self.result_history.create_index([("key", ASCENDING), ("fetched_at", ASCENDING)])
        await self.calls.create_index([("key", ASCENDING), ("outlet", ASCENDING)], unique=True)
        await self.odds.create_index([("ts", ASCENDING)], expireAfterSeconds=_ODDS_TTL_SECONDS)
        await self.odds.create_index(
            [("key", ASCENDING), ("market", ASCENDING), ("outcome", ASCENDING), ("ts", ASCENDING)]
        )
        await self.posts.create_index([("guild_id", ASCENDING), ("kind", ASCENDING), ("ref", ASCENDING)], unique=True)
        await self.cache.create_index([("kind", ASCENDING)])
        self._indexes_ready = True

    async def ping(self, wait: float = STARTUP_WAIT_SECONDS) -> None:
        """Startup check: keep trying for `wait` seconds (MongoDB may still be starting)."""
        deadline = time.monotonic() + wait
        while True:
            try:
                await self.client.admin.command("ping")
                return
            except ServerSelectionTimeoutError:
                if time.monotonic() >= deadline:
                    raise

    async def close(self) -> None:
        await self.client.close()

    async def get_guild(self, guild_id: int) -> GuildSettings | None:
        doc = await self.guilds.find_one({"_id": guild_id})
        return _guild(doc) if doc else None

    async def save_guild(self, gs: GuildSettings) -> None:
        doc = to_doc(gs)
        doc.pop("guild_id", None)
        await self.guilds.replace_one({"_id": gs.guild_id}, doc, upsert=True)

    async def all_guilds(self) -> list[GuildSettings]:
        return [_guild(doc) async for doc in self.guilds.find()]

    async def get_user(self, user_id: int) -> UserSettings | None:
        doc = await self.users.find_one({"_id": user_id})
        return _user(doc) if doc else None

    async def save_user(self, us: UserSettings) -> None:
        doc = to_doc(us)
        doc.pop("user_id", None)
        await self.users.replace_one({"_id": us.user_id}, doc, upsert=True)

    async def upsert_result(self, result: RaceResult) -> bool:
        key, fingerprint = str(result.key), result.fingerprint()
        old = await self.results.find_one({"key": key})
        changed = not old or old.get("_fingerprint") != fingerprint
        doc = to_doc(result)
        doc["_fingerprint"] = fingerprint
        doc["changed_at"] = result.fetched_at if changed else old.get("changed_at", result.fetched_at)
        await self.results.replace_one({"key": key}, doc, upsert=True)
        if changed:
            await self.result_history.insert_one(to_doc(result))
        return changed

    async def latest_result(self, key: RaceKey) -> RaceResult | None:
        doc = await self.results.find_one({"key": str(key)})
        return _result(doc) if doc else None

    async def result_changed_at(self, key: RaceKey) -> datetime | None:
        doc = await self.results.find_one({"key": str(key)}, {"changed_at": 1})
        if not doc or doc.get("changed_at") is None:
            return None
        return _utc(doc["changed_at"])

    async def record_call(self, call: RaceCall) -> bool:
        query = {"key": str(call.key), "outlet": call.outlet}
        old = await self.calls.find_one(query)
        if old and old.get("winner") == call.winner and old.get("retracted", False) == call.retracted:
            return False
        await self.calls.replace_one(query, to_doc(call), upsert=True)
        return True

    async def calls_for(self, key: RaceKey) -> list[RaceCall]:
        cursor = self.calls.find({"key": str(key)}).sort("called_at", ASCENDING)
        return [_call(doc) async for doc in cursor]

    async def add_odds(self, snapshot: OddsSnapshot) -> None:
        await self.odds.insert_one(to_doc(snapshot))

    async def odds_since(self, key: RaceKey, market: str, outcome: str, since: datetime) -> list[OddsSnapshot]:
        cursor = self.odds.find({"key": str(key), "market": market, "outcome": outcome, "ts": {"$gte": since}}).sort(
            "ts", ASCENDING
        )
        return [_odds(doc) async for doc in cursor]

    async def latest_odds(self, key: RaceKey) -> list[OddsSnapshot]:
        pipeline = [
            {"$match": {"key": str(key)}},
            # Follows the (key, market, outcome, ts) index instead of sorting every snapshot.
            {"$sort": {"market": 1, "outcome": 1, "ts": -1}},
            {"$group": {"_id": {"market": "$market", "outcome": "$outcome"}, "doc": {"$first": "$$ROOT"}}},
        ]
        cursor = await self.odds.aggregate(pipeline)
        return [_odds(row["doc"]) async for row in cursor]

    async def put_cached(self, kind: str, ident: str, data: str, at: datetime) -> None:
        await self.cache.replace_one(
            {"_id": f"{kind}|{ident}"}, {"kind": kind, "ident": ident, "data": data, "at": at}, upsert=True
        )

    async def get_cached(self, kind: str, ident: str) -> tuple[datetime, str] | None:
        doc = await self.cache.find_one({"_id": f"{kind}|{ident}"})
        return (_utc(doc["at"]), doc["data"]) if doc else None

    async def cached_idents(self, kind: str) -> dict[str, datetime]:
        return {doc["ident"]: _utc(doc["at"]) async for doc in self.cache.find({"kind": kind}, {"ident": 1, "at": 1})}

    async def last_posted(self, guild_id: int, kind: str, ref: str) -> datetime | None:
        doc = await self.posts.find_one({"guild_id": guild_id, "kind": kind, "ref": ref}, {"at": 1})
        return _utc(doc["at"]) if doc else None

    async def mark_posted(self, guild_id: int, kind: str, ref: str, at: datetime) -> None:
        await self.posts.update_one(
            {"guild_id": guild_id, "kind": kind, "ref": ref},
            {"$set": {"at": at}},
            upsert=True,
        )
