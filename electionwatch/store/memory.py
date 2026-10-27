"""In-memory Store for tests and the replay sandbox."""
from __future__ import annotations

import copy
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
        self.posts: dict[tuple[int, str, str], datetime] = {}

    async def get_guild(self, guild_id):
        return copy.deepcopy(self.guilds.get(guild_id))

    async def save_guild(self, gs: GuildSettings):
        self.guilds[gs.guild_id] = copy.deepcopy(gs)

    async def all_guilds(self):
        return [copy.deepcopy(g) for g in self.guilds.values()]

    async def get_user(self, user_id):
        return copy.deepcopy(self.users.get(user_id))

    async def save_user(self, us: UserSettings):
        self.users[us.user_id] = copy.deepcopy(us)

    async def upsert_result(self, r: RaceResult) -> bool:
        k, fp = str(r.key), r.fingerprint()
        changed = self.fps.get(k) != fp
        self.results[k] = copy.deepcopy(r)
        if changed:
            self.fps[k] = fp
            self.changed[k] = r.fetched_at
            self.history.append(copy.deepcopy(r))
        return changed

    async def latest_result(self, key: RaceKey):
        return copy.deepcopy(self.results.get(str(key)))

    async def result_changed_at(self, key: RaceKey):
        return self.changed.get(str(key))

    async def record_call(self, c: RaceCall) -> bool:
        ident = (str(c.key), c.outlet)
        old = self.calls.get(ident)
        if old and old.winner == c.winner and old.retracted == c.retracted:
            return False
        self.calls[ident] = copy.deepcopy(c)
        return True

    async def calls_for(self, key: RaceKey):
        return [copy.deepcopy(c) for (k, _), c in self.calls.items() if k == str(key)]

    async def add_odds(self, s: OddsSnapshot):
        self.odds.append(copy.deepcopy(s))

    async def odds_since(self, key, market, outcome, since):
        return [copy.deepcopy(s) for s in self.odds
                if s.key == key and s.market == market and s.outcome == outcome and s.ts >= since]

    async def last_posted(self, guild_id, kind, ref):
        return self.posts.get((guild_id, kind, ref))

    async def mark_posted(self, guild_id, kind, ref, at):
        self.posts[(guild_id, kind, ref)] = at
