"""In-memory Store for tests and the replay sandbox."""

from __future__ import annotations

import bisect
import copy
from collections import defaultdict
from datetime import datetime

from electionwatch.models import OddsSnapshot, RaceCall, RaceKey, RaceResult
from electionwatch.settings import GuildSettings, UserSettings


class MemoryStore:
    def __init__(self) -> None:
        self.guilds: dict[int, GuildSettings] = {}
        self.users: dict[int, UserSettings] = {}
        self.results: dict[str, RaceResult] = {}
        self.fps: dict[str, str] = {}
        self.changed: dict[str, datetime] = {}
        self.history: list[RaceResult] = []
        self.calls: dict[tuple[str, str], RaceCall] = {}
        self.odds: list[OddsSnapshot] = []
        # (key, market, outcome) -> snapshots in time order, so odds_since doesn't scan
        # every snapshot of the night on every tick (replays of 200+ races slowed to ~20 s/tick).
        self._odds_by: dict[tuple, list[OddsSnapshot]] = defaultdict(list)
        self.posts: dict[tuple[int, str, str], datetime] = {}
        self.cache: dict[tuple[str, str], tuple[datetime, str]] = {}

    async def get_guild(self, guild_id):
        return copy.deepcopy(self.guilds.get(guild_id))

    async def save_guild(self, gs: GuildSettings):
        self.guilds[gs.guild_id] = copy.deepcopy(gs)

    async def all_guilds(self):
        return [copy.deepcopy(guild) for guild in self.guilds.values()]

    async def get_user(self, user_id):
        return copy.deepcopy(self.users.get(user_id))

    async def save_user(self, us: UserSettings):
        self.users[us.user_id] = copy.deepcopy(us)

    async def upsert_result(self, result: RaceResult) -> bool:
        key, fingerprint = str(result.key), result.fingerprint()
        changed = self.fps.get(key) != fingerprint
        self.results[key] = copy.deepcopy(result)
        if changed:
            self.fps[key] = fingerprint
            self.changed[key] = result.fetched_at
            self.history.append(copy.deepcopy(result))
        return changed

    async def latest_result(self, key: RaceKey):
        return copy.deepcopy(self.results.get(str(key)))

    async def result_changed_at(self, key: RaceKey):
        return self.changed.get(str(key))

    async def record_call(self, call: RaceCall) -> bool:
        ident = (str(call.key), call.outlet)
        old = self.calls.get(ident)
        if old and old.winner == call.winner and old.retracted == call.retracted:
            return False
        self.calls[ident] = copy.deepcopy(call)
        return True

    async def calls_for(self, key: RaceKey):
        return [copy.deepcopy(call) for (call_key, _), call in self.calls.items() if call_key == str(key)]

    async def add_odds(self, snapshot: OddsSnapshot):
        snapshot = copy.copy(snapshot)  # OddsSnapshot has no mutable fields; a shallow copy suffices
        self.odds.append(snapshot)
        series = self._odds_by[(snapshot.key, snapshot.market, snapshot.outcome)]
        if series and series[-1].ts > snapshot.ts:
            bisect.insort(series, snapshot, key=lambda odds: odds.ts)
        else:
            series.append(snapshot)

    async def odds_since(self, key, market, outcome, since):
        series = self._odds_by.get((key, market, outcome), [])
        i = bisect.bisect_left(series, since, key=lambda odds: odds.ts)
        return [copy.copy(snapshot) for snapshot in series[i:]]

    async def latest_odds(self, key):
        return [
            copy.copy(series[-1]) for (odds_key, _, _), series in self._odds_by.items() if odds_key == key and series
        ]

    async def put_cached(self, kind, ident, payload, at):
        self.cache[(kind, ident)] = (at, payload)

    async def get_cached(self, kind, ident):
        return self.cache.get((kind, ident))

    async def cached_idents(self, kind):
        return {ident: at for (cached_kind, ident), (at, _) in self.cache.items() if cached_kind == kind}

    async def last_posted(self, guild_id, kind, ref):
        return self.posts.get((guild_id, kind, ref))

    async def mark_posted(self, guild_id, kind, ref, at):
        self.posts[(guild_id, kind, ref)] = at
