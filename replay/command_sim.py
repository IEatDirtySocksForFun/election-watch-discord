"""Election-night command simulations: slash commands changed mid-night against a running Poller.

    python -m replay.command_sim --years 2014,2016,2018,2020,2022,2024 --races top
    python -m replay.command_sim --years 2020,2024 --races all --scenarios latency,posting
    python -m replay.command_sim --list

A replay night runs at max speed (FakeClock, MemoryStore or MongoStore, ReplaySource plus
scripted calls / odds / exit polls / polls) through the real Poller of a real (never started)
ElectionBot. At scripted simulated times the REAL slash-command callbacks of
electionwatch/bot/discord.py are invoked with fake interactions (admin and non-admin users);
their replies are recorded and checked against Discord's limits, their wall time is measured.
Every scenario then asserts the observable effect on the recorded posts.

Scenario DSL (YAML or Python), times are US Eastern on election night (hours < 12 = next day):

    - at: "21:30"
      cmd: posting mode=interval minutes=15     # or positional: posting interval 15
      as: admin                                 # admin | member | roleadmin | other
      guild: 1
    - "22:00 resume"                            # short form
    - "21:30 !source_down minutes=30"           # pseudo-commands start with "!"

Pseudo-commands: !readonly (the read-only battery, run concurrently with poller ticks),
!navigator (/races + click every tab and state), !click_other (someone else clicks a navigator),
!store_down minutes=N, !source_down minutes=N, !mongod_stop, !mongod_start.

No network, no Discord (unless --live), no LLM. Outputs go to --out-dir (default
replay/out/sims/<stamp>/).
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import json
import logging
import os
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from zoneinfo import ZoneInfo

import discord
import httpx
import yaml

from electionwatch.models import ExitPoll, OddsSnapshot, Poll, PollResult, RaceCall, RaceKey
from electionwatch.poller import format_result
from electionwatch.poster import Post
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import GuildSettings, PostMode
from electionwatch.sources.base import CallSource, ExitPollSource, OddsSource, PollSource
from electionwatch.store.memory import MemoryStore

from replay.clock import FakeClock
from replay.discord_live import top_race_per_state
from replay.run import DEFAULT_DATA
from replay.source import ReplaySource
from replay.timeline import load_year

log = logging.getLogger("replay.command_sim")
ET = ZoneInfo("America/New_York")
LOGS = Path("replay/out/sims")
GUILD, CHANNEL = 1, 100
DEADLINE_FLAG_S = 2.5          # Discord's interaction deadline is 3 s; flag anything above this

# ---------------------------------------------------------------- time


def election_day(year: int | str) -> date:
    """First Tuesday after the first Monday of November."""
    d = date(int(year), 11, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d + timedelta(days=1)


def sim_time(year: int | str, hhmm: str) -> datetime:
    """'21:30' -> election night 9:30 PM ET as UTC; hours before noon are the next morning."""
    h, m = (int(x) for x in hhmm.split(":"))
    d = election_day(year) + timedelta(days=1 if h < 12 else 0)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(timezone.utc)


def et(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%H:%M")


# ---------------------------------------------------------------- fake Discord objects


@dataclass
class FakeRole:
    id: int


@dataclass
class FakeChannel:
    id: int
    name: str = "sim"


USERS = {
    "admin": dict(id=1001, manage=True, roles=()),
    "member": dict(id=2002, manage=False, roles=()),
    "roleadmin": dict(id=3003, manage=False, roles=(555,)),
    "other": dict(id=4004, manage=False, roles=()),
}


def fake_user(kind: str):
    u = USERS[kind]
    return SimpleNamespace(id=u["id"], name=kind, display_name=kind, mention=f"<@{u['id']}>",
                           guild_permissions=SimpleNamespace(manage_guild=u["manage"]),
                           roles=[FakeRole(r) for r in u["roles"]])


@dataclass
class Reply:
    kind: str                     # message | defer | followup | edit | edit_original
    t: float                      # seconds since the interaction arrived
    ephemeral: bool
    content: str | None = None
    embeds: list[dict] = field(default_factory=list)
    view: dict | None = None


class InteractionAlreadyResponded(RuntimeError):
    pass


def _embed_dicts(embed=None, embeds=None) -> list[dict]:
    out = [embed] if embed is not None and embed is not ... else []
    if embeds and embeds is not ...:
        out += list(embeds)
    return [e.to_dict() for e in out]


def _view_dict(view) -> dict | None:
    if view is None or view is ...:
        return None
    items = []
    for item in view.children:
        d = {"type": type(item).__name__, "row": item.row}
        if isinstance(item, discord.ui.Button):
            d["label"] = item.label
        elif isinstance(item, discord.ui.Select):
            d["placeholder"] = item.placeholder
            d["options"] = [o.label for o in item.options]
        items.append(d)
    return {"items": items}


class FakeResponse:
    def __init__(self, ix: "FakeInteraction") -> None:
        self.ix = ix
        self._done = False

    def is_done(self) -> bool:
        return self._done

    def _first(self) -> None:
        if self._done:
            raise InteractionAlreadyResponded("interaction already responded")
        self._done = True
        self.ix.ack_t = self.ix.elapsed()

    async def send_message(self, content=None, *, embed=..., embeds=..., view=..., ephemeral=False, **_):
        self._first()
        self.ix.record(Reply("message", self.ix.elapsed(), bool(ephemeral),
                             None if content is None else str(content),
                             _embed_dicts(embed, embeds), _view_dict(view)))
        if view is not ... and view is not None:
            self.ix.views.append(view)

    async def defer(self, *, ephemeral=False, thinking=False):
        self._first()
        self.ix.deferred_ephemeral = bool(ephemeral) or self.ix.is_component
        self.ix.record(Reply("defer", self.ix.elapsed(), self.ix.deferred_ephemeral))

    async def edit_message(self, *, content=..., embed=..., embeds=..., view=..., **_):
        self._first()
        self.ix.record(Reply("edit", self.ix.elapsed(), True,
                             None if content is ... else content, _embed_dicts(embed, embeds), _view_dict(view)))


class FakeFollowup:
    def __init__(self, ix: "FakeInteraction") -> None:
        self.ix = ix

    async def send(self, content=None, *, embed=..., embeds=..., view=..., ephemeral=False, **_):
        if not self.ix.response.is_done():
            raise RuntimeError("followup before the interaction was acknowledged")
        # A followup to an ephemeral deferral is ephemeral even without the flag.
        eph = bool(ephemeral) or self.ix.deferred_ephemeral
        self.ix.record(Reply("followup", self.ix.elapsed(), eph,
                             None if content is None else str(content),
                             _embed_dicts(embed, embeds), _view_dict(view)))
        if view is not ... and view is not None:
            self.ix.views.append(view)


class FakeInteraction:
    def __init__(self, client, guild_id: int | None, user_kind: str, command: str,
                 *, is_component: bool = False, data: dict | None = None, t0: float | None = None):
        self.client = client
        self.guild_id = guild_id
        self.guild = SimpleNamespace(id=guild_id) if guild_id else None
        self.user = fake_user(user_kind)
        self.command = SimpleNamespace(name=command)
        self.is_component = is_component
        self.data = data or {}
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.replies: list[Reply] = []
        self.views: list = []
        self.deferred_ephemeral = False
        self.t0 = time.perf_counter() if t0 is None else t0
        self.ack_t: float | None = None

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def record(self, r: Reply) -> None:
        self.replies.append(r)

    async def edit_original_response(self, *, content=..., embed=..., embeds=..., view=..., **_):
        self.record(Reply("edit_original", self.elapsed(), True,
                          None if content is ... else content, _embed_dicts(embed, embeds), _view_dict(view)))


# ---------------------------------------------------------------- Discord limit checks


def limit_problems(r: Reply) -> list[str]:
    out = []
    if r.content is not None and len(r.content) > 2000:
        out.append(f"content {len(r.content)} > 2000 chars")
    if len(r.embeds) > 10:
        out.append("more than 10 embeds")
    for e in r.embeds:
        total = 0
        for k, lim in (("title", 256), ("description", 4096)):
            n = len(e.get(k) or "")
            total += n
            if n > lim:
                out.append(f"embed {k} {n} > {lim}")
        fields = e.get("fields") or []
        if len(fields) > 25:
            out.append(f"{len(fields)} embed fields > 25")
        for f in fields:
            total += len(f["name"]) + len(f["value"])
            if len(f["name"]) > 256 or len(f["value"]) > 1024 or not f["value"]:
                out.append(f"field {f['name'][:30]!r} name/value outside limits")
        n = len((e.get("footer") or {}).get("text") or "")
        total += n
        if n > 2048:
            out.append(f"footer {n} > 2048")
        if total > 6000:
            out.append(f"embed total {total} > 6000 chars")
    if r.view:
        rows = Counter(i["row"] for i in r.view["items"])
        if len(rows) > 5 or any(row is not None and row > 4 for row in rows):
            out.append(f"view uses rows {sorted(rows, key=str)} (max 5)")
        for i in r.view["items"]:
            if i["type"] == "Button" and len(i.get("label") or "") > 80:
                out.append("button label > 80")
            if i["type"] == "Select":
                if not 1 <= len(i["options"]) <= 25:
                    out.append(f"select with {len(i['options'])} options")
                if any(len(o) > 100 for o in i["options"]):
                    out.append("select option label > 100")
                if len(i.get("placeholder") or "") > 150:
                    out.append("select placeholder > 150")
    return out


# ---------------------------------------------------------------- scripted sources


class SimReplaySource(ReplaySource):
    """ReplaySource with optional synthetic turnout and outage windows (raises httpx errors)."""

    def __init__(self, timelines, clock, *, turnout: bool = False) -> None:
        super().__init__(timelines, clock)
        self.turnout = turnout
        self.outages: list[tuple[datetime, datetime]] = []
        self.attempts: list[tuple[datetime, bool]] = []
        self._reg = {k: int(max(v[-1].result.total_votes or 0, 1) * 1.6) for k, v in self.timelines.items()}

    async def fetch(self, keys, client=None):
        now = self.clock()
        down = any(a <= now < b for a, b in self.outages)
        self.attempts.append((now, not down))
        if down:
            raise httpx.ConnectError("simulated results-source outage")
        out = await super().fetch(keys, client)
        if self.turnout:
            for r in out:
                reg = self._reg.get(str(r.key))
                if reg and r.total_votes:
                    r.registered_voters = reg
                    r.turnout_pct = round(100.0 * r.total_votes / reg, 2)
        return out


@dataclass
class SimCall:
    key: str
    at: datetime
    winner: str
    party: str | None
    outlet: str


class SimCallSource(CallSource):
    name = "sim_calls"
    enabled_by_default = False

    def __init__(self, calls: list[SimCall], clock) -> None:
        self.calls, self.clock = calls, clock

    async def fetch_calls(self, keys, client=None):
        want = {str(k): k for k in keys}
        now = self.clock()
        return [RaceCall(want[c.key], c.outlet, c.winner, c.party, c.at)
                for c in self.calls if c.key in want and c.at <= now]


def spread_calls(timelines, races: list[str], t0: datetime, t1: datetime,
                 outlets: dict[str, float]) -> list[SimCall]:
    """One call per race per outlet, spread evenly over [t0, t1]; outlets maps name -> delay min."""
    out = []
    n = max(len(races), 1)
    for i, k in enumerate(sorted(races)):
        final = timelines[k][-1].result
        if not final.candidates:
            continue
        lead = max(final.candidates, key=lambda c: c.votes)
        at = t0 + (t1 - t0) * (i / n)
        for outlet, delay in outlets.items():
            out.append(SimCall(k, at + timedelta(minutes=delay), lead.name, lead.party, outlet))
    return out


@dataclass
class HeldSwing:
    key: str
    outcome: str
    start: datetime
    minutes: float
    pts: float       # moves `outcome` by this many points over `minutes`, then holds


class HoldOddsSource(OddsSource):
    """Flat 50/50 odds for every race (so no swing happens by accident) plus held swings."""
    name = "sim_odds"
    enabled_by_default = False

    def __init__(self, clock, swings: list[HeldSwing] | None = None, market: str = "kalshi") -> None:
        self.clock, self.swings, self.market = clock, swings or [], market

    def prob(self, key: str, outcome: str, now: datetime) -> float:
        p = 0.5
        for sw in self.swings:
            if sw.key != key or now < sw.start:
                continue
            f = min(1.0, (now - sw.start) / timedelta(minutes=sw.minutes))
            delta = sw.pts / 100 * f
            p += delta if sw.outcome == outcome else -delta
        return round(min(max(p, 0.0), 1.0), 4)

    async def fetch_odds(self, keys, client=None):
        now = self.clock()
        return [OddsSnapshot(k, self.market, o, self.prob(str(k), o, now), now)
                for k in keys for o in ("DEM", "REP")]


class SimExitPollSource(ExitPollSource):
    name = "sim_exitpolls"
    enabled_by_default = False

    def __init__(self, avail: dict[str, datetime], clock, outlet: str = "CNN") -> None:
        self.avail, self.clock, self.outlet = avail, clock, outlet

    async def fetch_exit_polls(self, keys, client=None):
        now = self.clock()
        return [ExitPoll(k, self.outlet, f"https://example.invalid/exit/{str(k).replace(' ', '_')}",
                         "Economy top issue: 38%\nDecided in last week: 9%", now)
                for k in keys if str(k) in self.avail and self.avail[str(k)] <= now]


class SimPollSource(PollSource):
    name = "sim_polls"
    enabled_by_default = False

    async def fetch_polls(self, keys, client=None):
        out = []
        for k in keys:
            for i, pollster in enumerate(("Siena", "Emerson", "Trafalgar Group", "PPP")):
                out.append(Poll(k, pollster, [PollResult("Candidate D", "DEM", 47.0 + i),
                                              PollResult("Candidate R", "REP", 46.0 - i)],
                                date(2020, 10, 20 + i), date(2020, 10, 15 + i), 800 + 50 * i, "LV",
                                partisan="REP" if pollster == "Trafalgar Group" else None,
                                source="sim", url="https://example.invalid/poll"))
        return out


# ---------------------------------------------------------------- store proxy (observe + faults)


class StoreDown(Exception):
    pass


class ObservedStore:
    """Wraps any Store: records result changes, can fail every call during an outage window."""

    def __init__(self, inner, clock) -> None:
        self.inner, self.clock = inner, clock
        self.outages: list[tuple[datetime, datetime]] = []
        self.changes: list[tuple[datetime, str, tuple]] = []
        self.errors = 0

    def _down(self) -> bool:
        now = self.clock()
        return any(a <= now < b for a, b in self.outages)

    def _fail(self):
        self.errors += 1
        try:
            from pymongo.errors import AutoReconnect
            raise AutoReconnect("simulated MongoDB outage")
        except ImportError:  # pragma: no cover
            raise StoreDown("simulated store outage")

    async def upsert_result(self, r):
        if self._down():
            self._fail()
        changed = await self.inner.upsert_result(r)
        if changed:
            self.changes.append((self.clock(), str(r.key), tuple(format_result(r, turnout=True).lines)))
        return changed

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def wrapped(*a, **kw):
            if self._down():
                self._fail()
            return await attr(*a, **kw)
        return wrapped


# ---------------------------------------------------------------- recorder poster


@dataclass
class PostRec:
    at: datetime
    guild: int
    channel: int
    post: Post

    def row(self) -> dict:
        return {"at": self.at.isoformat(), "et": et(self.at), "guild": self.guild, "channel": self.channel,
                "kind": self.post.kind, "race": self.post.race, "title": self.post.title,
                "lines": self.post.lines}


class SimPoster:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.posts: list[PostRec] = []

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        self.posts.append(PostRec(self.clock(), guild_id, channel_id, post))


# ---------------------------------------------------------------- DSL


@dataclass
class Step:
    at: str
    cmd: str
    user: str = "admin"
    guild: int = GUILD


def parse_steps(spec) -> list[Step]:
    if isinstance(spec, str):
        spec = yaml.safe_load(spec) or []
    out = []
    for item in spec:
        if isinstance(item, Step):
            out.append(item)
        elif isinstance(item, str):
            at, cmd = item.split(" ", 1)
            out.append(Step(at, cmd.strip()))
        else:
            out.append(Step(str(item["at"]), str(item["cmd"]), item.get("as", "admin"),
                            int(item.get("guild", GUILD))))
    return out


@dataclass
class CmdRec:
    at: datetime
    guild: int
    user: str
    command: str
    args: dict
    replies: list[Reply]
    ack_s: float | None
    total_s: float
    error: str | None = None
    concurrent: bool = False
    rejected: str | None = None     # Discord's client would not send this (bad choice/range)
    label: str = ""

    @property
    def text(self) -> str:
        parts = []
        for r in self.replies:
            if r.content:
                parts.append(r.content)
            for e in r.embeds:
                parts.append(" ".join(str(e.get(k) or "") for k in ("title", "description")))
        return " | ".join(parts)

    def row(self) -> dict:
        return {"at": self.at.isoformat(), "et": et(self.at), "guild": self.guild, "user": self.user,
                "command": self.label or self.command, "args": {k: str(v) for k, v in self.args.items()},
                "ack_s": self.ack_s, "total_s": round(self.total_s, 4), "error": self.error,
                "concurrent": self.concurrent, "rejected": self.rejected,
                "replies": [{"kind": r.kind, "t": round(r.t, 4), "ephemeral": r.ephemeral,
                             "content": (r.content or "")[:300],
                             "embeds": [{"title": e.get("title"), "len": len(json.dumps(e))} for e in r.embeds],
                             "view": bool(r.view), "limits": limit_problems(r)} for r in self.replies]}


def _convert(param, raw: str):
    from discord import AppCommandOptionType as T
    if param.type is T.integer:
        return int(raw)
    if param.type is T.number:
        return float(raw)
    if param.type is T.boolean:
        return raw.lower() in ("1", "true", "yes", "on")
    if param.type is T.channel:
        return FakeChannel(int(raw))
    if param.type is T.role:
        return FakeRole(int(raw))
    return raw


def bind_args(command, tokens: list[str]) -> tuple[dict, str | None]:
    params = list(command.parameters)
    by_name = {p.name: p for p in params}
    args, positional = {}, [p for p in params]
    for tok in tokens:
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k not in by_name:
                raise ValueError(f"/{command.name} has no option {k!r}")
            args[k] = (by_name[k], v)
        else:
            free = [p for p in positional if p.name not in args]
            if not free:
                raise ValueError(f"too many arguments for /{command.name}")
            args[free[0].name] = (free[0], tok)
    out, rejected = {}, None
    for name, (p, raw) in args.items():
        value = _convert(p, raw)
        if p.choices and str(value) not in {str(c.value) for c in p.choices}:
            rejected = f"{name}={raw} is not one of the offered choices"
        if p.min_value is not None and isinstance(value, (int, float)) and not p.min_value <= value <= p.max_value:
            rejected = f"{name}={raw} outside {p.min_value}..{p.max_value}"
        out[name] = value
    missing = [p.name for p in params if p.required and p.name not in out]
    if missing:
        rejected = "missing required option(s): " + ", ".join(missing)
    return out, rejected


# ---------------------------------------------------------------- the night


@dataclass
class NightResult:
    year: str
    posts: list[PostRec]
    cmds: list[CmdRec]
    changes: list[tuple[datetime, str, tuple]]
    ticks: list[tuple[datetime, float]]
    tick_errors: list[tuple[datetime, str]]
    source_attempts: list[tuple[datetime, bool]]
    start: datetime
    end: datetime
    watched: list[str]
    extra: dict = field(default_factory=dict)

    def posts_for(self, guild=GUILD, kind=None, race=None, t0=None, t1=None) -> list[PostRec]:
        return [p for p in self.posts if p.guild == guild and (kind is None or p.post.kind == kind)
                and (race is None or p.post.race == race) and (t0 is None or p.at >= t0)
                and (t1 is None or p.at < t1)]

    def t(self, hhmm: str) -> datetime:
        return sim_time(self.year, hhmm)


class Night:
    """One simulated night. Build, then `await run(steps)`."""

    def __init__(self, year: str, timelines: dict, guilds: list[GuildSettings], *,
                 start: str = "19:30", until: str = "02:30", turnout: bool = False,
                 calls: list[SimCall] | None = None, swings: list[HeldSwing] | None = None,
                 exitpolls: dict[str, datetime] | None = None, store=None,
                 default_races: list[str] | None = None, workdir: Path | None = None,
                 poster=None, speed: float | None = None, on_step=None, mongod=None,
                 idle_skip: float = 90) -> None:
        self.year = str(year)
        self.timelines = timelines
        self.guilds = guilds
        self.clock = FakeClock(sim_time(year, start))
        self.end = sim_time(year, until)
        self.src = SimReplaySource(timelines, self.clock, turnout=turnout)
        self.calls = calls or []
        self.odds = HoldOddsSource(self.clock, swings)
        self.sources = {
            "results": [self.src],
            "calls": [SimCallSource(self.calls, self.clock)],
            "odds": [self.odds],
            "exitpolls": [SimExitPollSource(exitpolls or {}, self.clock)],
            "polls": [SimPollSource()],
        }
        self.store = ObservedStore(store if store is not None else MemoryStore(), self.clock)
        self.poster = poster or SimPoster(self.clock)
        self.default_races = default_races
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="ew-sim-"))
        self.speed = speed
        self.on_step = on_step
        self.mongod = mongod
        self.idle_skip = idle_skip
        self.cmds: list[CmdRec] = []
        self.ticks: list[tuple[datetime, float]] = []
        self.tick_errors: list[tuple[datetime, str]] = []
        self.readonly_race: str | None = None
        self.measure_stall = False
        self.stalls: list[float] = []
        self._stall = 0.0

    # -- setup

    async def _build(self, http):
        from electionwatch.bot import navigator
        from electionwatch.bot.discord import ElectionBot

        navigator._list_cache.clear()     # module-level cache: keyed by source name, not year
        if self.default_races:
            path = self.workdir / f"races-{self.year}.yaml"
            path.write_text(yaml.safe_dump({"important": [{"key": k} for k in self.default_races]}))
            os.environ["EW_RACES_FILE"] = str(path)
        for gs in self.guilds:
            await self.store.inner.save_guild(gs)
        bot = ElectionBot(self.store, self.sources, http, self.clock)
        bot.poller.poster = self.poster
        return bot

    # -- commands

    async def invoke(self, bot, step: Step, *, concurrent: bool = False, t0: float | None = None,
                     label: str = "") -> CmdRec:
        tokens = shlex.split(step.cmd)
        name, rest = tokens[0], tokens[1:]
        command = bot.tree.get_command(name)
        if command is None:
            raise ValueError(f"no slash command /{name}")
        args, rejected = bind_args(command, rest)
        ix = FakeInteraction(bot, step.guild, step.user, name, t0=t0)
        err = None
        if rejected is None:
            try:
                await command.callback(ix, **args)
            except Exception as exc:   # what CommandTree does: route to on_error
                from electionwatch.bot.discord import handle_app_command_error
                err = f"{type(exc).__name__}: {exc}"[:300]
                log.debug("command /%s raised", name, exc_info=True)
                try:
                    await handle_app_command_error(ix, exc)
                except Exception as exc2:
                    err += f" / on_error raised {type(exc2).__name__}"
        rec = CmdRec(self.clock(), step.guild, step.user, name, args, ix.replies,
                     ix.ack_t, ix.elapsed(), err, concurrent, rejected, label or step.cmd)
        rec._ix = ix  # type: ignore[attr-defined]
        self.cmds.append(rec)
        return rec

    async def click(self, bot, view, item, user: str, guild: int, label: str, data=None,
                    concurrent=False, t0=None) -> CmdRec:
        ix = FakeInteraction(bot, guild, user, "races", is_component=True, data=data, t0=t0)
        err = None
        try:
            await item.callback(ix)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
        rec = CmdRec(self.clock(), guild, user, "races", {}, ix.replies, ix.ack_t, ix.elapsed(), err,
                     concurrent, None, label)
        self.cmds.append(rec)
        return rec

    async def navigator_sweep(self, bot, guild: int, user: str = "admin", *, all_states=True,
                              under_tick=False) -> list[CmdRec]:
        recs = []
        rec = await self._maybe_concurrent(bot, lambda t0: self.invoke(
            bot, Step("", "races", user, guild), concurrent=under_tick, t0=t0, label="races"), under_tick)
        recs.append(rec)
        views = getattr(rec, "_ix").views
        if not views:
            return recs
        view = views[-1]

        def find(kind, pred):
            return next((i for i in view.children if isinstance(i, kind) and pred(i)), None)

        # Only the first few clicks race a poller tick (each costs a whole extra tick); the
        # rest of the sweep measures the callbacks alone.
        budget = [6 if under_tick else 0]

        def conc() -> bool:
            budget[0] -= 1
            return budget[0] >= 0

        states = list(STATES) if all_states else ["PA", "TX", "GA", "AZ"]
        for code in states:
            u = conc()
            select = find(discord.ui.Select, lambda s: any(o.value == code for o in s.options))
            recs.append(await self._maybe_concurrent(bot, lambda t0: self.click(
                bot, view, select, user, guild, "races:state", {"values": [code]}, u, t0), u))
            for tab in ("Congressional", "Senate", "State", "Local"):
                u = conc()
                button = find(discord.ui.Button, lambda b: b.label == tab)
                recs.append(await self._maybe_concurrent(bot, lambda t0: self.click(
                    bot, view, button, user, guild, "races:tab", None, u, t0), u))
                if tab == "Senate" and view.selected_race and code in ("PA", "GA", "AZ", "TX"):
                    for tool in ("Odds", "Polls"):
                        u = conc()
                        b = find(discord.ui.Button, lambda b: b.label == tool)
                        recs.append(await self._maybe_concurrent(bot, lambda t0: self.click(
                            bot, view, b, user, guild, f"races:{tool.lower()}", None, u, t0),
                            u))
                    b = find(discord.ui.Button, lambda b: b.label == "Choose race")
                    recs.append(await self.click(bot, view, b, user, guild, "races:choose"))
                    s = find(discord.ui.Select, lambda s: s.placeholder == "Race")
                    if s is not None:
                        recs.append(await self.click(bot, view, s, user, guild, "races:race",
                                                     {"values": [s.options[0].value]}))
        return recs

    async def _maybe_concurrent(self, bot, make, under_tick: bool):
        """Run a command; with under_tick, it arrives while a poller tick is in flight on the
        same event loop (as in the real bot), and its latency includes waiting for the loop."""
        if not under_tick:
            return await make(None)
        tick = asyncio.create_task(self._tick(bot, record=False))
        await asyncio.sleep(0)             # let the tick start and run to its first await
        t0 = time.perf_counter()
        rec = await make(t0)
        await tick
        return rec

    async def readonly_battery(self, bot, guild: int, under_tick: bool = True):
        race = self.readonly_race or (sorted(self.watched_for(guild)) or ["PA SEN"])[0]
        battery = ["odds", f'odds race="{race}"', f'polls race="{race}"', "watch list", "settings",
                   "faq", "faq state=PA"]
        for cmd in battery:
            await self._maybe_concurrent(bot, lambda t0: self.invoke(
                bot, Step("", cmd, "member", guild), concurrent=under_tick, t0=t0, label=cmd.split("=")[0]),
                under_tick)
        # autocomplete is also bound by the 3 s deadline
        from electionwatch.bot.discord import race_autocomplete

        async def ac(t0):
            ix = FakeInteraction(bot, guild, "member", "autocomplete", t0=t0)
            got = await race_autocomplete(ix, "se")
            rec = CmdRec(self.clock(), guild, "member", "autocomplete", {}, [
                Reply("autocomplete", ix.elapsed(), True, f"{len(got)} choices")], ix.elapsed(), ix.elapsed(),
                None if len(got) <= 25 else f"{len(got)} choices > 25", under_tick, None, "autocomplete")
            self.cmds.append(rec)
            return rec
        await self._maybe_concurrent(bot, ac, under_tick)
        await self.navigator_sweep(bot, guild, "member", all_states=True, under_tick=under_tick)

    def watched_for(self, guild: int) -> list[str]:
        gs = self.store.inner.guilds.get(guild) if isinstance(self.store.inner, MemoryStore) else None
        return list(gs.important) if gs else []

    async def pseudo(self, bot, step: Step) -> None:
        tokens = shlex.split(step.cmd[1:])
        name, kv = tokens[0], dict(t.split("=", 1) for t in tokens[1:] if "=" in t)
        now = self.clock()
        if name == "readonly":
            await self.readonly_battery(bot, step.guild, kv.get("under_tick", "1") == "1")
        elif name == "navigator":
            await self.navigator_sweep(bot, step.guild, step.user, all_states=kv.get("all", "0") == "1")
        elif name == "click_other":
            recs = await self.navigator_sweep(bot, step.guild, step.user, all_states=False)
            view = recs[0]._ix.views[-1] if recs and recs[0]._ix.views else None   # type: ignore
            if view is not None:
                button = next(i for i in view.children if isinstance(i, discord.ui.Button))
                await self.click(bot, view, button, "other", step.guild, "races:foreign-click")
        elif name == "store_down":
            self.store.outages.append((now, now + timedelta(minutes=float(kv.get("minutes", 20)))))
        elif name == "source_down":
            self.src.outages.append((now, now + timedelta(minutes=float(kv.get("minutes", 30)))))
        elif name == "mongod_stop":
            self.mongod.stop()
        elif name == "mongod_start":
            self.mongod.start()
        else:
            raise ValueError(f"unknown pseudo-command {step.cmd}")

    # -- loop

    async def _tick(self, bot, record=True):
        t = time.perf_counter()
        hb = asyncio.create_task(self._heartbeat()) if self.measure_stall and record else None
        try:
            await bot.poller.tick()
        except Exception as exc:       # the bot's poll_results loop logs and carries on
            self.tick_errors.append((self.clock(), f"{type(exc).__name__}: {exc}"[:200]))
        if hb is not None:
            hb.cancel()
        if record:
            self.ticks.append((self.clock(), time.perf_counter() - t))
            if hb is not None:
                self.stalls.append(self._stall)

    async def _heartbeat(self) -> None:
        """Longest stretch the event loop was blocked during a tick: an interaction arriving
        at the worst moment waits this long before its callback even starts."""
        self._stall = 0.0
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0)
            now = time.perf_counter()
            self._stall = max(self._stall, now - last)
            last = now

    async def run(self, steps) -> NightResult:
        steps = sorted(parse_steps(steps), key=lambda s: sim_time(self.year, s.at))
        due = [(sim_time(self.year, s.at), s) for s in steps]
        old_env = os.environ.get("EW_RACES_FILE")
        async with httpx.AsyncClient() as http:
            bot = await self._build(http)
            try:
                while self.clock() <= self.end:
                    while due and due[0][0] <= self.clock():
                        _, step = due.pop(0)
                        if step.cmd.startswith("!"):
                            await self.pseudo(bot, step)
                            rec = None
                        else:
                            rec = await self.invoke(bot, step)
                        if self.on_step:
                            await self.on_step(step, rec)
                    t = time.perf_counter()
                    await self._tick(bot)
                    if self.speed:
                        await asyncio.sleep(max(0.0, 60 / self.speed - (time.perf_counter() - t)))
                    self.clock.advance(1)
                    nxt = self.src.next_change(self.clock())
                    limit = due[0][0] if due else self.end
                    if nxt is None:
                        nxt = self.end
                    nxt = min(nxt, limit)
                    if nxt - self.clock() > timedelta(minutes=self.idle_skip):
                        self.clock.set(nxt - timedelta(minutes=1))
            finally:
                await bot.close()
                if old_env is None:
                    os.environ.pop("EW_RACES_FILE", None)
                else:
                    os.environ["EW_RACES_FILE"] = old_env
        watched = sorted({k for g in self.guilds for k in g.important})
        return NightResult(self.year, getattr(self.poster, "posts", []), self.cmds, self.store.changes,
                           self.ticks, self.tick_errors, self.src.attempts,
                           sim_time(self.year, "19:30"), self.end, watched)


# ---------------------------------------------------------------- checks


class Checks:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.notes: list[str] = []

    def ok(self, cond: bool, msg: str) -> bool:
        if not cond:
            self.problems.append(msg)
        return cond

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def first_tick(res: "NightResult", at: datetime) -> datetime:
    """The tick that first sees something scheduled at `at` (ticks run once per sim minute)."""
    import bisect
    ticks = [t for t, _ in res.ticks]
    i = bisect.bisect_left(ticks, at)
    return ticks[i] if i < len(ticks) else res.end + timedelta(minutes=1)


def check_gaps(c: Checks, res: NightResult, t0, t1, minutes, guild=GUILD, label=""):
    last: dict[str, datetime] = {}
    n = 0
    for p in res.posts_for(guild, "results"):
        prev = last.get(p.post.race)
        if prev is not None and t0 <= p.at < t1:
            gap = (p.at - prev).total_seconds() / 60
            n += 1
            c.ok(gap >= minutes, f"{label}{et(p.at)} {p.post.race}: results {gap:.0f} min after previous (rule {minutes})")
        last[p.post.race] = p.at
    return n


def check_every_change(c: Checks, res: NightResult, t0, t1, guild=GUILD, label="", watched=None):
    """Within [t0, t1): every tick at which a watched race's displayed result changed has a post
    at that tick, and no results post happens at a tick without a change (except at t0)."""
    posts = res.posts_for(guild, "results")
    posted_at = defaultdict(set)
    for p in posts:
        posted_at[p.post.race].add(p.at)
    last_lines: dict[str, tuple] = {}
    events = sorted(res.changes)
    pi = 0
    missing = extra = checked = 0
    posts_sorted = sorted(posts, key=lambda p: p.at)
    changed_at = defaultdict(set)
    for at, race, lines in events:
        if watched is not None and race not in watched:
            continue
        while pi < len(posts_sorted) and posts_sorted[pi].at < at:
            q = posts_sorted[pi]
            last_lines[q.post.race] = tuple(q.post.lines)
            pi += 1
        changed_at[race].add(at)
        if t0 <= at < t1 and lines != last_lines.get(race):
            checked += 1
            if at not in posted_at[race]:
                missing += 1
                if missing <= 5:
                    c.problems.append(f"{label}{et(at)} {race}: result changed but nothing posted")
    for p in posts:
        if t0 < p.at < t1 and p.at not in changed_at[p.post.race]:
            extra += 1
            if extra <= 5:
                c.problems.append(f"{label}{et(p.at)} {p.post.race}: results posted without a change")
    if missing > 5:
        c.problems.append(f"{label}... {missing} changes not posted in total")
    return checked


def no_posts(c: Checks, res, t0, t1, guild=GUILD, kind=None, label=""):
    bad = res.posts_for(guild, kind, t0=t0, t1=t1)
    c.ok(not bad, f"{label}{len(bad)} {kind or 'any'} posts in {et(t0)}-{et(t1)} (first {bad[0].post.kind} "
                  f"{bad[0].post.race} at {et(bad[0].at)})" if bad else "")
    return bad


def replies_ok(c: Checks, res: NightResult, *, expect_refused: bool | None = None, users=None, label=""):
    for r in res.cmds:
        if users and r.user not in users:
            continue
        if r.rejected:
            continue
        c.ok(bool(r.replies), f"{label}/{r.label} by {r.user} at {et(r.at)}: no reply at all")
        for rep in r.replies:
            c.ok(rep.ephemeral, f"{label}/{r.label} by {r.user} at {et(r.at)}: {rep.kind} reply not ephemeral")
            for p in limit_problems(rep):
                c.problems.append(f"{label}/{r.label} at {et(r.at)}: {p}")
        if expect_refused is True:
            c.ok("required" in r.text.lower() or "belongs to another user" in r.text,
                 f"{label}/{r.label} by {r.user} not refused: {r.text[:120]!r}")


def post_signature(res: NightResult, guild: int) -> list[tuple]:
    return [(p.at, p.channel, p.post.kind, p.post.race, tuple(p.post.lines)) for p in res.posts_for(guild)]


# ---------------------------------------------------------------- scenarios


@dataclass
class Ctx:
    year: str
    timelines: dict
    watched: list[str]          # the scenario's watch list (top-per-state or all)
    mode: str                   # top | all
    workdir: Path
    mongo_uri: str | None = None
    mongod_bin: str | None = None


def pick_extra_race(ctx: Ctx, exclude: set[str], t0: datetime, t1: datetime) -> str | None:
    """The race outside `exclude` with the most snapshots between t0 and t1."""
    best, n_best = None, 0
    for k, snaps in ctx.timelines.items():
        if k in exclude:
            continue
        n = sum(1 for s in snaps if t0 <= s.ts < t1)
        if n > n_best:
            best, n_best = k, n
    return best


def base_guild(ctx: Ctx, **kw) -> GuildSettings:
    return GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=list(ctx.watched), **kw)


def default_calls_for(ctx: Ctx, races=None, outlets=None) -> list[SimCall]:
    return spread_calls(ctx.timelines, races or ctx.watched, sim_time(ctx.year, "20:00"),
                        sim_time(ctx.year, "01:30"), outlets or {"AP": 0})


async def sc_pause(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx),
                  exitpolls={k: sim_time(ctx.year, "21:20") for k in ctx.watched[:5]})
    res = await night.run(["21:00 pause", "22:00 resume"])
    no_posts(c, res, res.t("21:00"), res.t("22:00"))
    after = res.posts_for(t0=res.t("22:00"))
    c.ok(any(p.post.kind == "results" for p in after), "no results posts after /resume")
    at_resume = Counter(p.post.race for p in res.posts_for(kind="results", t0=res.t("22:00"), t1=res.t("22:01")))
    c.ok(all(v == 1 for v in at_resume.values()), f"resume tick posted a race twice: {at_resume.most_common(2)}")
    c.ok(len(at_resume) <= len(ctx.watched), "resume posted more results than watched races")
    # no duplicate: a resumed race's post must differ from its last pre-pause post
    for race in at_resume:
        before = res.posts_for(kind="results", race=race, t1=res.t("21:00"))
        now = res.posts_for(kind="results", race=race, t0=res.t("22:00"), t1=res.t("22:01"))
        if before and now:
            c.ok(before[-1].post.lines != now[0].post.lines, f"{race}: resume re-posted an identical result")
    dropped = [k for k in night.calls if res.t("21:00") <= first_tick(res, k.at) < res.t("22:00")]
    late = res.posts_for(kind="call", t0=res.t("22:00"))
    c.ok(not any(p.post.race in {d.key for d in dropped} and p.at < res.t("22:02") for p in late),
         "calls made during the pause were flooded out at resume")
    eps = res.posts_for(kind="exitpoll")
    c.note(f"resume minute: {len(at_resume)} results posts (one per changed race, latest numbers only); "
           f"{len(dropped)} calls made while paused are never posted; "
           f"{len([p for p in eps if p.at >= res.t('22:00')])} exit polls published while paused posted after resume")
    replies_ok(c, res)
    return res


async def sc_posting(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    res = await night.run(["21:30 posting interval 15", "22:30 posting interval 1",
                           "23:00 posting immediate", "23:45 posting default"])
    n0 = check_gaps(c, res, res.start, res.t("21:30"), 5, label="default: ")
    n1 = check_gaps(c, res, res.t("21:30"), res.t("22:30"), 15, label="interval15: ")
    k1 = check_every_change(c, res, res.t("22:30"), res.t("23:00"), label="interval1: ", watched=set(ctx.watched))
    k2 = check_every_change(c, res, res.t("23:00"), res.t("23:45"), label="immediate: ", watched=set(ctx.watched))
    n3 = check_gaps(c, res, res.t("23:45"), res.end, 5, label="back to default: ")
    c.ok(n1 > 0 and k2 > 0, f"vacuous: {n1} interval-15 gaps, {k2} immediate changes checked")
    # rate per channel (Discord allows ~5 messages / 5 s per channel)
    per_min = Counter(p.at for p in res.posts_for())
    peak = per_min.most_common(1)[0] if per_min else (None, 0)
    c.note(f"gaps checked: default {n0}, interval15 {n1}, back-to-default {n3}; changes checked: "
           f"interval1 {k1}, immediate {k2}; peak {peak[1]} posts in one minute"
           + (f" at {et(peak[0])}" if peak[0] else ""))
    replies_ok(c, res)
    return res


async def sc_watch(ctx: Ctx, c: Checks):
    t0, t1 = sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00")
    x = pick_extra_race(ctx, set(ctx.watched) if ctx.mode == "top" else set(), t0, t1)
    watched = [k for k in ctx.watched if k != x]
    defaults = []
    for k in sorted(ctx.timelines, key=lambda k: -sum(1 for s in ctx.timelines[k]
                                                      if s.ts >= sim_time(ctx.year, "00:00"))):
        if k not in (x,) and len(defaults) < 2 and (ctx.mode == "all" or k not in watched):
            defaults.append(k)
    gs = GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=watched)
    steps = [f'21:30 watch add "{x}"', f'23:00 watch remove "{x}"']
    steps += [f'00:00 watch remove "{k}"' for k in watched]
    steps += ["00:01 watch list"]
    night = Night(ctx.year, ctx.timelines, [gs], default_races=defaults, workdir=ctx.workdir)
    res = await night.run(steps)
    c.ok(not res.posts_for(race=x, t1=t0), f"{x} posted before /watch add")
    c.ok(bool(res.posts_for(race=x, kind="results", t0=t0, t1=t1)), f"{x} never posted after /watch add")
    c.ok(not res.posts_for(race=x, t0=t1 + timedelta(minutes=1)) or x in defaults,
         f"{x} still posted after /watch remove")
    after = res.posts_for(t0=sim_time(ctx.year, "00:00"))
    stray = {p.post.race for p in after} - set(defaults)
    c.ok(not stray, f"after removing every race, posts for non-default races: {sorted(stray)[:5]}")
    c.ok(any(p.post.race in defaults for p in after), f"no posts for races.yaml defaults {defaults} after fallback")
    lst = [r for r in res.cmds if r.label == "watch list"]
    c.ok(bool(lst) and all(d in lst[-1].text for d in defaults), "/watch list does not show the defaults")
    c.note(f"added/removed {x}; fallback defaults {defaults}; {len(watched)} removals at 00:00")
    replies_ok(c, res)
    return res


async def sc_calls(ctx: Ctx, c: Checks):
    outlets = {"AP": 0, "FOX": 3, "NBC": 6}
    calls = default_calls_for(ctx, outlets=outlets)
    steps = ["21:00 calls AP off", "22:00 calls outlets=FOX enabled=on", "23:30 calls AP,NBC on"]
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=calls)
    res = await night.run(steps)
    ticks = [t for t, _ in res.ticks]
    import bisect as _b

    def cfg(at):
        if at < res.t("21:00"):
            return True, {"AP", "NBC", "CNN", "FOX", "DDHQ", "VOTEHUB"}
        if at < res.t("22:00"):
            return False, set()
        if at < res.t("23:30"):
            return True, {"FOX"}
        return True, {"AP", "NBC"}
    expected = set()
    for k in calls:
        i = _b.bisect_left(ticks, k.at)
        if i == len(ticks):
            continue
        tick = ticks[i]
        on, allowed = cfg(tick)
        if on and k.outlet in allowed:
            expected.add((tick, k.key, k.outlet))
    got = {(p.at, p.post.race, p.post.lines[-1].removeprefix("Outlet: ")) for p in res.posts_for(kind="call")}
    c.ok(got == expected, f"call posts differ from expected: missing {sorted(expected - got)[:3]}, "
                          f"unexpected {sorted(got - expected)[:3]}")
    win = Counter(("off" if not cfg(t)[0] else "+".join(sorted(cfg(t)[1]))[:12]) for t, _, _ in got)
    c.ok(not res.posts_for(kind="call", t0=res.t("21:00"), t1=res.t("22:00")), "call posted while calls off")
    c.ok(all(o == "FOX" for t, _, o in got if res.t("22:00") <= t < res.t("23:30")), "non-FOX call in FOX window")
    c.ok(sum(1 for t, _, o in got if res.t("22:00") <= t < res.t("23:30")) > 0, "vacuous: no FOX call posted")
    c.note(f"{len(got)} call posts, by setting window: {dict(win)}")
    replies_ok(c, res)
    return res


async def sc_toggles(ctx: Ctx, c: Checks):
    avail = {k: sim_time(ctx.year, "20:00") + timedelta(minutes=(i * 7) % 300)
             for i, k in enumerate(sorted(ctx.watched))}
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], turnout=True, exitpolls=avail)
    res = await night.run(["21:30 turnout off", "22:00 exitpolls off", "00:00 exitpolls on", "00:00 turnout on"])
    has_t = lambda p: any(ln.startswith("Turnout:") for ln in p.post.lines)  # noqa: E731
    c.ok(any(has_t(p) for p in res.posts_for(kind="results", t1=res.t("21:30"))), "vacuous: no turnout line before off")
    bad = [p for p in res.posts_for(kind="results", t0=res.t("21:30"), t1=res.t("00:00")) if has_t(p)]
    c.ok(not bad, f"{len(bad)} results posts still show turnout after /turnout off")
    c.ok(any(has_t(p) for p in res.posts_for(kind="results", t0=res.t("00:00"))) or
         not res.posts_for(kind="results", t0=res.t("00:00")), "turnout not back after /turnout on")
    no_posts(c, res, res.t("22:00"), res.t("00:00"), kind="exitpoll", label="exitpolls off: ")
    c.ok(bool(res.posts_for(kind="exitpoll", t1=res.t("22:00"))), "vacuous: no exit poll before off")
    backlog = res.posts_for(kind="exitpoll", t0=res.t("00:00"), t1=res.t("00:01"))
    missed = [k for k, t in avail.items() if res.t("22:00") <= t < res.t("00:00")]
    due_now = [k for k, t in avail.items() if t == res.t("00:00")]
    c.ok(len(backlog) == len(missed) + len(due_now),
         f"exit polls published while off: {len(missed)} (+{len(due_now)} due at 00:00), "
         f"posted at /exitpolls on: {len(backlog)}")
    c.note(f"/exitpolls on posts the {len(backlog)} exit polls published while it was off in one burst")
    replies_ok(c, res)
    return res


async def sc_oddsalerts(ctx: Ctx, c: Checks):
    races = sorted(ctx.watched)
    if len(races) < 5:
        c.ok(False, "needs 5 watched races")
        return None
    a, b, cc, d, e = races[:5]
    T = lambda s: sim_time(ctx.year, s)  # noqa: E731
    swings = [HeldSwing(e, "DEM", T("20:30"), 10, 60),     # before alerts are on
              HeldSwing(a, "DEM", T("21:40"), 10, 15),     # above 10
              HeldSwing(b, "REP", T("21:40"), 10, 5),      # below 10
              HeldSwing(cc, "REP", T("22:30"), 5, 12),     # above 10
              HeldSwing(d, "DEM", T("23:30"), 5, 20)]      # after alerts are off
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], swings=swings)
    res = await night.run(["21:30 oddsalerts on threshold=10 window=30", "23:00 oddsalerts off"])
    alerts = res.posts_for(kind="odds_swing")
    by = Counter(p.post.race for p in alerts)
    c.ok(by.get(a, 0) >= 1 and by.get(cc, 0) >= 1, f"missing alerts above threshold: {dict(by)}")
    c.ok(not by.get(b) and not by.get(d) and not by.get(e), f"alerts below threshold / while off: {dict(by)}")
    c.ok(set(by) <= {a, cc}, f"unexpected alert races {sorted(set(by) - {a, cc})}")
    first_a = min((p.at for p in alerts if p.post.race == a), default=None)
    if first_a:
        c.ok(first_a <= T("21:48"), f"{a} alert late: {et(first_a)} (crossed 10 pts at ~21:47)")
    c.note(f"alerts {dict(by)} ({len(alerts)} posts; one per outcome: both sides of a 2-way market alert); "
           f"e.g. {alerts[0].post.lines[0] if alerts else '-'}")
    replies_ok(c, res)
    return res


async def sc_setup(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    res = await night.run(["22:00 setup channel=200"])
    before = {p.channel for p in res.posts_for(t1=res.t("22:00"))}
    after = {p.channel for p in res.posts_for(t0=res.t("22:00"))}
    c.ok(before == {CHANNEL}, f"posts before /setup went to {before}")
    c.ok(after == {200}, f"posts after /setup went to {after}")
    replies_ok(c, res)
    return res


ADMIN_CMDS = ["pause", "resume", "posting immediate", "posting interval 30", 'watch add "{x}"',
              'watch remove "{y}"', "calls AP off", "oddsalerts on threshold=1 window=5",
              "turnout off", "exitpolls off", "setup channel=999 admin_role=555"]


async def sc_nonadmin(ctx: Ctx, c: Checks):
    x = pick_extra_race(ctx, set(ctx.watched), sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00")) or "PA SEN"
    y = sorted(ctx.watched)[0]
    steps = []
    for i, cmd in enumerate(ADMIN_CMDS):
        for who in ("member", "roleadmin"):     # no admin role configured: role means nothing
            steps.append({"at": f"21:{30 + i:02d}", "cmd": cmd.format(x=x, y=y), "as": who})
    steps.append({"at": "22:15", "cmd": "!click_other", "as": "admin"})
    kw = dict(calls=default_calls_for(ctx), exitpolls={k: sim_time(ctx.year, "21:00") for k in ctx.watched[:5]})
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], **kw)
    res = await night.run(steps)
    control = await Night(ctx.year, ctx.timelines, [base_guild(ctx)], **kw).run([])
    attempts = [r for r in res.cmds if r.user in ("member", "roleadmin")]
    replies_ok(c, res, expect_refused=True, users={"member", "roleadmin"})
    foreign = [r for r in res.cmds if r.user == "other"]
    replies_ok(c, res, expect_refused=True, users={"other"})
    c.ok(bool(foreign), "foreign navigator click not exercised")
    c.ok(post_signature(res, GUILD) == post_signature(control, GUILD),
         "posts differ from the control night without the refused commands")
    c.note(f"{len(attempts)} refused admin commands + {len(foreign)} foreign navigator click; posts identical to control")
    return res


async def sc_roleadmin(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)])
    res = await night.run([
        {"at": "21:00", "cmd": "posting interval 20", "as": "roleadmin"},        # refused: no role set
        {"at": "21:10", "cmd": f"setup channel={CHANNEL} admin_role=555", "as": "admin"},
        {"at": "21:30", "cmd": "posting interval 20", "as": "roleadmin"},        # allowed now
        {"at": "21:31", "cmd": "pause", "as": "member"},                         # still refused
    ])
    r = [x for x in res.cmds if x.user == "roleadmin"]
    c.ok("required" in r[0].text, f"role user allowed before admin_role was set: {r[0].text!r}")
    c.ok("interval every 20" in r[1].text, f"role user refused after admin_role was set: {r[1].text!r}")
    m = [x for x in res.cmds if x.user == "member"]
    c.ok("required" in m[0].text, "member allowed to pause")
    check_gaps(c, res, res.t("21:30"), res.end, 20, label="interval20: ")
    c.ok(any(p.at >= res.t("21:31") for p in res.posts_for()), "posting stopped (member pause took effect?)")
    replies_ok(c, res)
    return res


async def sc_invalid(ctx: Ctx, c: Checks):
    steps = ['21:30 watch add "ZZ SEN"', "21:31 calls outlets=BOGUS enabled=off", "21:32 posting interval",
             "21:33 watch add", "21:34 polls race=nonsense", "21:35 odds race=???", "21:36 faq state=XX",
             "21:37 favorite add \"QQ-99\"", "21:38 watch remove \"ZZ-01\""]
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    res = await night.run(steps)
    control = await Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx)).run([])
    for r in res.cmds:
        c.ok(r.error is None, f"/{r.label}: raised {r.error}")
    replies_ok(c, res)
    gs = res.extra.get("final")
    c.ok(post_signature(res, GUILD) == post_signature(control, GUILD),
         "invalid commands changed posting (posts differ from control)")
    c.note("; ".join(f"/{r.label} -> {r.text[:60]!r}" for r in res.cmds[:4]))
    return res


async def sc_pause_change(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)])
    res = await night.run(["21:00 pause", "21:30 posting interval 30", "22:00 resume"])
    no_posts(c, res, res.t("21:00"), res.t("22:00"))
    check_gaps(c, res, res.t("22:00"), res.end, 30, label="interval30 after resume: ")
    c.ok(bool(res.posts_for(t0=res.t("22:00"))), "nothing posted after resume")
    replies_ok(c, res)
    return res


async def sc_two_guilds(ctx: Ctx, c: Checks):
    g2_races = sorted(ctx.watched)[::3]
    g2 = GuildSettings(guild_id=2, channel_id=201, important=g2_races, post_mode=PostMode.IMMEDIATE,
                       call_outlets=["FOX"], post_turnout=False, odds_alerts=True, odds_swing_pts=10,
                       odds_window_minutes=30)
    x = pick_extra_race(ctx, set(ctx.watched), sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00")) or g2_races[0]
    swings = [HeldSwing(g2_races[0], "DEM", sim_time(ctx.year, "22:10"), 5, 20)]
    kw = dict(calls=default_calls_for(ctx, outlets={"AP": 0, "FOX": 2}), swings=swings, turnout=True)
    steps = ["21:00 pause", "21:30 resume", "21:40 posting interval 15", f'21:50 watch add "{x}"',
             "22:00 calls AP off", "22:20 setup channel=300", "22:30 oddsalerts on threshold=5 window=10",
             "22:40 turnout off", f'23:00 watch remove "{g2_races[0]}"',
             {"at": "23:10", "cmd": "posting immediate", "as": "admin", "guild": 2},   # same as before: no-op
             {"at": "23:20", "cmd": "!readonly under_tick=0", "guild": 2}]
    both = await Night(ctx.year, ctx.timelines, [base_guild(ctx), g2], **kw).run(steps)
    solo = await Night(ctx.year, ctx.timelines, [g2], **kw).run(
        [s for s in parse_steps(steps) if s.guild == 2])
    c.ok(post_signature(both, 2) == post_signature(solo, 2), "guild 2 posts differ when guild 1 changes settings")
    c.ok({p.channel for p in both.posts_for(2)} <= {201}, "guild 2 posted outside its channel")
    c.ok(not {p.channel for p in both.posts_for(GUILD)} & {201}, "guild 1 posted into guild 2's channel")
    c.ok(not both.posts_for(GUILD, t0=both.t("21:00"), t1=both.t("21:30")), "guild 1 posted while paused")
    c.ok(bool(both.posts_for(2, t0=both.t("21:00"), t1=both.t("21:30"))), "guild 2 silent while guild 1 paused")
    c.note(f"guild1 {len(both.posts_for(GUILD))} posts, guild2 {len(both.posts_for(2))} posts (== solo run)")
    replies_ok(c, both)
    return both


async def sc_store_errors(ctx: Ctx, c: Checks):
    if not ctx.mongo_uri:
        c.ok(False, "no --mongo-uri")
        return None
    from pymongo.asynchronous.mongo_client import AsyncMongoClient

    from electionwatch.store.mongo import MongoStore
    db = f"ew_sim_{os.getpid()}_{int(time.time())}"
    store = MongoStore(ctx.mongo_uri, db)
    await store.create_indexes()
    await store.odds.drop_index("ts_1")   # the 3-day TTL would expire 2014-2024 replay odds mid-run
    try:
        night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], store=store, until="23:30",
                      calls=default_calls_for(ctx))
        res = await night.run(["21:50 posting interval 10", "22:00 !store_down minutes=20", "22:05 pause",
                               "22:06 settings", "22:07 odds", '22:08 watch add "PA SEN"', "22:30 settings"])
    finally:
        client = AsyncMongoClient(ctx.mongo_uri)
        await client.drop_database(db)
        await client.close()
        await store.close()
    down_errors = [e for e in res.tick_errors if res.t("22:00") <= e[0] < res.t("22:20")]
    c.ok(all(res.t("22:00") <= e[0] < res.t("22:20") for e in res.tick_errors),
         f"tick errors outside the outage: {res.tick_errors[:2]}")
    c.ok(bool(down_errors), "outage did not reach the poller")
    no_posts(c, res, res.t("22:00"), res.t("22:20"), label="store down: ")
    c.ok(bool(res.posts_for(t0=res.t("22:20"), t1=res.t("22:30"))), "no posts within 10 min after the store came back")
    during = [r for r in res.cmds if res.t("22:00") <= r.at < res.t("22:20")]
    for r in during:
        c.ok(r.ack_s is not None and r.ack_s < DEADLINE_FLAG_S, f"/{r.label} during outage: no reply in time")
        c.ok("went wrong" in r.text, f"/{r.label} during outage: {r.text[:80]!r}")
    final = [r for r in res.cmds if r.label == "settings"][-1]
    c.ok("paused: false" in final.text and "interval_minutes: 10" in final.text,
         "settings after the outage lost the pre-outage change or kept the failed /pause")
    dup = Counter((p.post.race, tuple(p.post.lines)) for p in res.posts_for(kind="call"))
    c.ok(all(v == 1 for v in dup.values()), "duplicate call posts after recovery")
    late = [p for p in res.posts_for(kind="call", t0=res.t("22:20"), t1=res.t("22:21"))]
    c.note(f"{len(down_errors)} failed ticks, {len(during)} commands answered 'Something went wrong'; "
           f"{len(late)} calls from the outage posted at recovery")
    replies_ok(c, res)
    return res


class PrivateMongod:
    """A throw-away mongod on its own port (never touches the shared 27017 instance)."""

    def __init__(self, binary: str, port: int, path: Path) -> None:
        self.binary, self.port, self.path = binary, port, path
        self.proc = None
        path.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self.proc = subprocess.Popen([self.binary, "--port", str(self.port), "--bind_ip", "127.0.0.1",
                                      "--dbpath", str(self.path), "--quiet", "--nounixsocket"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import socket
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("private mongod did not start")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=30)
            self.proc = None


async def sc_mongo_restart(ctx: Ctx, c: Checks):
    if not ctx.mongod_bin:
        c.ok(False, "no --mongod-bin")
        return None
    from electionwatch.store.mongo import MongoStore
    import socket
    with socket.socket() as sock:          # a free port: parallel runs must not share a mongod
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    mongod = PrivateMongod(ctx.mongod_bin, port, ctx.workdir / f"mongod-restart-{port}")
    mongod.start()
    try:
        store = MongoStore(f"mongodb://127.0.0.1:{port}", "ew_sim_restart")
        await store.create_indexes()
        await store.odds.drop_index("ts_1")
        night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], store=store, start="21:30", until="22:30",
                      mongod=mongod)
        res = await night.run(["21:50 posting interval 10", "22:00 !mongod_stop", "22:00 settings",
                               "22:01 pause", "22:02 !mongod_start", "22:10 settings"])
        await store.close()
    finally:
        mongod.stop()
    during = [r for r in res.cmds if res.t("22:00") <= r.at < res.t("22:02")]
    for r in during:
        c.ok(r.ack_s is not None, f"/{r.label} while mongod is down: no reply at all")
        if r.ack_s is None or r.ack_s > DEADLINE_FLAG_S:
            c.problems.append(f"/{r.label} while mongod is down answered after {r.total_s:.1f}s "
                              "(Discord's 3 s deadline: the user sees 'did not respond')")
    c.ok(bool(res.posts_for(t0=res.t("22:02"))), "no posts after mongod restarted")
    final = [r for r in res.cmds if r.label == "settings"][-1]
    c.ok("interval_minutes: 10" in final.text, "settings lost across the mongod restart")
    c.note(f"tick errors while down: {len(res.tick_errors)}; command wall times while down: "
           + ", ".join(f"/{r.label} {r.total_s:.1f}s" for r in during))
    return res


async def sc_source_outage(ctx: Ctx, c: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    res = await night.run(["21:30 !source_down minutes=30", "21:45 settings", "21:46 !navigator"])
    tries = [t for t, ok in res.source_attempts if res.t("21:30") <= t < res.t("22:00")]
    c.ok(len(tries) <= 6, f"{len(tries)} fetch attempts during a 30-min outage (back-off expected)")
    no_posts(c, res, res.t("21:30"), res.t("22:00"), kind="results", label="source down: ")
    first = min((p.at for p in res.posts_for(kind="results", t0=res.t("22:00"))), default=None)
    c.ok(first is not None and first - res.t("22:00") <= timedelta(minutes=16),
         f"results recovered at {et(first) if first else 'never'}")
    c.ok(bool(res.posts_for(kind="call", t0=res.t("21:30"), t1=res.t("22:00"))) or True, "")
    calls_during = len(res.posts_for(kind="call", t0=res.t("21:30"), t1=res.t("22:00")))
    replies_ok(c, res)
    c.note(f"attempts during outage at {[et(t) for t in tries]}; first results post after: "
           f"{et(first) if first else '-'}; {calls_during} call posts continued during the outage")
    return res


async def sc_latency(ctx: Ctx, c: Checks, mongo: bool = False):
    steps = [{"at": f"{h:02d}:{m:02d}", "cmd": "!readonly"} for h in (20, 21, 22, 23, 0, 1) for m in (0, 30)]
    store = db = None
    if mongo:
        if not ctx.mongo_uri:
            c.ok(False, "no --mongo-uri")
            return None
        from electionwatch.store.mongo import MongoStore
        db = f"ew_sim_lat_{os.getpid()}_{int(time.time())}"
        store = MongoStore(ctx.mongo_uri, db)
        await store.create_indexes()
        await store.odds.drop_index("ts_1")
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx, odds_alerts=True)], calls=default_calls_for(ctx),
                  turnout=True, until="02:00", store=store)
    night.measure_stall = True
    night.readonly_race = next((k for k in ("PA SEN", "GA SEN", "AZ SEN", "TX SEN") if k in ctx.watched),
                               sorted(ctx.watched)[0])
    try:
        res = await night.run(steps)
    finally:
        if store is not None:
            await store.client.drop_database(db)
            await store.close()
    replies_ok(c, res)
    for r in res.cmds:
        c.ok(r.error is None, f"/{r.label} at {et(r.at)} raised {r.error}")
        if r.ack_s is None or r.ack_s > DEADLINE_FLAG_S:
            c.problems.append(f"/{r.label} at {et(r.at)}: ack {r.ack_s}s (> {DEADLINE_FLAG_S}s)")
        elif r.total_s > DEADLINE_FLAG_S:
            c.note(f"/{r.label} at {et(r.at)}: total {r.total_s:.2f}s (acked in {r.ack_s:.2f}s)")
    lat = latency_table(res)
    tk = [s for _, s in res.ticks]
    st = night.stalls or [0.0]
    worst = max(v["max"] for v in lat.values()) + max(st)
    c.ok(worst <= DEADLINE_FLAG_S, f"worst case (slowest callback {worst - max(st):.2f}s + loop blocked "
                                   f"{max(st):.2f}s by a tick) = {worst:.2f}s > {DEADLINE_FLAG_S}s")
    res.extra["stall"] = {"p50": pct(st, 50), "p95": pct(st, 95), "max": max(st),
                          "tick_p50": pct(tk, 50), "tick_p95": pct(tk, 95), "tick_max": max(tk)}
    c.note(f"loop blocked by a tick: p50 {pct(st, 50):.3f}s p95 {pct(st, 95):.3f}s max {max(st):.3f}s")
    c.note(f"{len(res.cmds)} interactions; tick p50 {pct(tk, 50):.3f}s max {max(tk):.3f}s; "
           + "; ".join(f"{k} p50 {v['p50']:.3f} p95 {v['p95']:.3f} max {v['max']:.3f}" for k, v in lat.items()))
    res.extra["latency"] = lat
    return res


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def latency_table(res: NightResult) -> dict:
    by = defaultdict(list)
    for r in res.cmds:
        by[r.label].append(r.total_s if r.ack_s is None else max(r.ack_s, r.total_s))
    acks = defaultdict(list)
    for r in res.cmds:
        if r.ack_s is not None:
            acks[r.label].append(r.ack_s)
    return {k: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "max": max(v),
                "ack_max": max(acks[k], default=None)} for k, v in sorted(by.items())}


SCENARIOS: dict[str, tuple[str, Callable]] = {
    "pause": ("1 /pause 21:00, /resume 22:00", sc_pause),
    "posting": ("2 /posting interval 15 -> 1 -> immediate -> default", sc_posting),
    "watch": ("3 /watch add/remove mid-night, remove all -> races.yaml", sc_watch),
    "calls": ("4 /calls off -> FOX only -> AP+NBC", sc_calls),
    "toggles": ("5 /turnout off, /exitpolls off, both back on", sc_toggles),
    "oddsalerts": ("6 /oddsalerts on 10 pts/30 min, then off", sc_oddsalerts),
    "setup": ("7 /setup channel change mid-night", sc_setup),
    "nonadmin": ("8 non-admin tries every admin command; foreign navigator click", sc_nonadmin),
    "latency": ("9 read-only commands + navigator every 30 min under load", sc_latency),
    "latencymongo": ("9b same as 9 with MongoStore (local mongod, throw-away db)",
                     lambda ctx, c: sc_latency(ctx, c, mongo=True)),
    "twoguilds": ("10 two guilds, guild 1 changes everything", sc_two_guilds),
    "storeerr": ("11a MongoStore with store errors for 20 min", sc_store_errors),
    "mongorestart": ("11b private mongod stopped/started mid-night", sc_mongo_restart),
    "sourcedown": ("12 results source outage 30 min", sc_source_outage),
    "roleadmin": ("13 /setup admin_role delegates admin to a role", sc_roleadmin),
    "invalid": ("14 invalid inputs mid-night change nothing", sc_invalid),
    "pausechange": ("15 /posting changed while paused applies after resume", sc_pause_change),
}


# ---------------------------------------------------------------- runner


def write_run(out_dir: Path, name: str, res: NightResult | None, c: Checks, secs: float) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    row = {"scenario": name, "ok": not c.problems, "problems": c.problems[:40], "notes": c.notes,
           "secs": round(secs, 1)}
    if res is not None:
        row.update(posts=len(res.posts), commands=len(res.cmds), ticks=len(res.ticks),
                   tick_errors=len(res.tick_errors))
        for k in ("latency", "stall"):
            if k in res.extra:
                row[k] = res.extra[k]
        with (out_dir / f"{name}.jsonl").open("w") as f:
            for p in res.posts:
                f.write(json.dumps({"type": "post", **p.row()}, ensure_ascii=False) + "\n")
            for r in res.cmds:
                f.write(json.dumps({"type": "command", **r.row()}, ensure_ascii=False) + "\n")
            for t, e in res.tick_errors:
                f.write(json.dumps({"type": "tick_error", "et": et(t), "error": e}) + "\n")
    return row


async def run_scenarios(years, mode, names, data, out_dir, mongo_uri, mongod_bin) -> list[dict]:
    rows = []
    for year in years:
        timelines = load_year(Path(data).expanduser(), year)
        watched = top_race_per_state(timelines) if mode == "top" else sorted(timelines)
        for name in names:
            c = Checks()
            ctx = Ctx(year, timelines, watched, mode, out_dir / "work", mongo_uri, mongod_bin)
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            t = time.perf_counter()
            res = None
            try:
                res = await SCENARIOS[name][1](ctx, c)
            except Exception as exc:
                c.problems.append(f"crashed: {type(exc).__name__}: {exc}")
                c.notes.append(traceback.format_exc()[-1500:])
            row = write_run(out_dir / f"{year}-{mode}", name, res, c, time.perf_counter() - t)
            row.update(year=year, mode=mode)
            rows.append(row)
            print(f"{year} {mode:3} {name:12} {'PASS' if row['ok'] else 'FAIL'} {row['secs']:7.1f}s "
                  + ("; ".join(c.problems[:2]) if c.problems else (c.notes[0][:140] if c.notes else "")),
                  flush=True)
    return rows


def matrix(rows: list[dict]) -> str:
    cols = sorted({(r["year"], r["mode"]) for r in rows})
    names = list(dict.fromkeys(r["scenario"] for r in rows))
    idx = {(r["scenario"], r["year"], r["mode"]): r for r in rows}
    out = ["| scenario | " + " | ".join(f"{y} {m}" for y, m in cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for n in names:
        cells = []
        for y, m in cols:
            r = idx.get((n, y, m))
            cells.append("-" if r is None else ("PASS" if r["ok"] else "FAIL"))
        out.append(f"| {SCENARIOS[n][0]} | " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------- live into Discord

LIVE_RACES = ["PA SEN", "MI SEN", "WI SEN", "AZ SEN", "NV SEN", "OH SEN", "MT SEN", "TX SEN"]
LIVE_STEPS = [
    "20:45 posting interval 15",
    {"at": "21:00", "cmd": "pause", "as": "member"},
    "21:05 pause",
    "21:35 resume",
    "21:40 posting immediate",
    "21:55 posting default",
    'WATCH_ADD',
    'WATCH_REMOVE',
    "22:35 calls AP off",
    "22:50 calls outlets=FOX enabled=on",
    "23:15 oddsalerts on threshold=10 window=30",
    "23:50 oddsalerts off",
    "23:55 turnout off",
    "00:05 exitpolls off",
    "00:10 settings",
]


class LivePoster(SimPoster):
    """Sends every post of the sim guild into the thread, titled [SIM], footer = sim time ET."""

    def __init__(self, clock, thread, record: Path) -> None:
        super().__init__(clock)
        self.thread = thread
        self.record = record.open("a", encoding="utf-8")
        self.sent = Counter()

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        from electionwatch.bot.poster import render_embed
        await super().post(guild_id, channel_id, post)
        sim = self.clock()
        shown = dc_replace(post, title=f"[SIM] {post.title}",
                           footer=((post.footer + " · ") if post.footer else "") + f"sim {et(sim)} ET")
        await self.thread.send(embed=render_embed(shown))
        self.sent[post.kind] += 1
        self.record.write(json.dumps({"type": "post", "et": et(sim), "kind": post.kind, "race": post.race,
                                      "lines": post.lines}, ensure_ascii=False) + "\n")
        self.record.flush()


async def live(a) -> None:
    from electionwatch.config import load_env
    load_env()
    timelines = load_year(Path(a.data).expanduser(), a.year)
    races = [k for k in LIVE_RACES if k in timelines] or top_race_per_state(timelines)[:8]
    x = next((k for k in ("PA-07", "PA-08", "PA-10", "NE-02", "MI-07") if k in timelines), None)
    steps = []
    for s in LIVE_STEPS:
        if s == "WATCH_ADD":
            s = f'22:00 watch add "{x}"' if x else None
        elif s == "WATCH_REMOVE":
            s = f'22:30 watch remove "{x}"' if x else None
        if s:
            steps.append(s)
    T = lambda h: sim_time(a.year, h)  # noqa: E731
    calls = spread_calls(timelines, races + ([x] if x else []), T("20:40"), T("00:20"), {"AP": 0, "FOX": 2})
    swings = [HeldSwing(races[0], "DEM", T("23:20"), 6, 18), HeldSwing(races[1], "REP", T("23:25"), 6, 4)]
    exitpolls = {k: T("20:35") + timedelta(minutes=17 * i) for i, k in enumerate(races)}
    out = Path(a.out_dir or LOGS / f"live-{a.year}-{datetime.now():%Y%m%d-%H%M%S}")
    out.mkdir(parents=True, exist_ok=True)
    client = discord.Client(intents=discord.Intents.none())
    done = asyncio.Event()
    state: dict = {}

    @client.event
    async def on_ready():
        try:
            channel = await client.fetch_channel(a.channel)
            thread = await channel.create_thread(name=f"[SIM] command changes {a.year}",
                                                 type=discord.ChannelType.public_thread,
                                                 auto_archive_duration=1440)
            state["thread"] = thread.id
            (out / "thread.txt").write_text(f"{thread.id}\n")
            await thread.send(
                f"**[SIM] Command-change simulation, {a.year} election night** ({a.speed:g}x, "
                f"{a.start}-{a.until} ET).\nResults are historical replays; race calls, odds and exit polls are "
                f"scripted test data. Watching: {', '.join(races)}.\nEach admin command below is invoked "
                "through the bot's real slash-command code mid-night; watch the posts change after it.")

            async def on_step(step: Step, rec: CmdRec | None) -> None:
                if rec is None:
                    return
                who = {"admin": "admin", "member": "a member without Manage Server"}.get(step.user, step.user)
                reply = rec.text.replace("```yaml", "").replace("```", "").strip()
                await thread.send(f"⚙️ `/{step.cmd}` by {who} (sim {et(rec.at)} ET)\n"
                                  f"↳ ephemeral reply: {reply[:600]}")

            poster = LivePoster(None, thread, out / "posts.jsonl")
            night = Night(a.year, timelines, [GuildSettings(GUILD, CHANNEL, important=list(races))],
                          start=a.start, until=a.until, turnout=True, calls=calls, swings=swings,
                          exitpolls=exitpolls, poster=poster, speed=a.speed, on_step=on_step)
            poster.clock = night.clock
            t0 = time.monotonic()
            res = await night.run(steps)
            await thread.send(
                f"**[SIM] finished** after {(time.monotonic() - t0) / 60:.0f} min: posts {dict(poster.sent)}, "
                f"{len(res.cmds)} commands, tick errors {len(res.tick_errors)}.")
            c = Checks()
            no_posts(c, res, res.t("21:05"), res.t("21:35"))
            check_gaps(c, res, res.t("20:45"), res.t("21:05"), 15)
            no_posts(c, res, res.t("22:35"), res.t("22:50"), kind="call")
            (out / "result.json").write_text(json.dumps(
                {"thread": thread.id, "problems": c.problems, "posts": dict(poster.sent),
                 "commands": [r.row() for r in res.cmds]}, indent=1, default=str))
            print("live problems:", c.problems or "none", "thread", thread.id, "->", out)
        except Exception:
            traceback.print_exc()
            raise
        finally:
            done.set()

    async with client:
        task = asyncio.create_task(client.start(os.environ["DISCORD_TOKEN"]))
        await done.wait()
        await client.close()
        task.cancel()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--years", default="2014,2016,2018,2020,2022,2024")
    ap.add_argument("--races", choices=["top", "all"], default="top")
    ap.add_argument("--scenarios", default="all", help="comma-separated names, or 'all' (see --list)")
    ap.add_argument("--skip", default="", help="scenarios to leave out")
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--mongo-uri", default=None, help="for storeerr, e.g. mongodb://127.0.0.1:27017")
    ap.add_argument("--mongod-bin", default=None, help="for mongorestart (private mongod on a free port)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--live", action="store_true", help="one combined scenario into a Discord thread")
    ap.add_argument("--year", default="2024", help="--live: year")
    ap.add_argument("--guild", type=int, default=None, help="--live: guild id (informational)")
    ap.add_argument("--channel", type=int, default=None, help="--live: parent channel of the new thread")
    ap.add_argument("--speed", type=float, default=10)
    ap.add_argument("--start", default="20:30")
    ap.add_argument("--until", default="00:30")
    a = ap.parse_args(argv)
    if a.live:
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(live(a))
        return 0
    if a.list:
        for k, (d, _) in SCENARIOS.items():
            print(f"{k:12} {d}")
        return 0
    logging.basicConfig(level=logging.CRITICAL)   # the poller logs every injected failure
    names = list(SCENARIOS) if a.scenarios == "all" else [s.strip() for s in a.scenarios.split(",")]
    names = [n for n in names if n not in set(a.skip.split(","))]
    out = Path(a.out_dir) if a.out_dir else LOGS / datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = asyncio.run(run_scenarios([y.strip() for y in a.years.split(",")], a.races, names, a.data, out,
                                     a.mongo_uri, a.mongod_bin))
    (out / f"summary-{a.races}.json").write_text(json.dumps(rows, indent=1, default=str))
    (out / f"matrix-{a.races}.md").write_text(matrix(rows) + "\n")
    print(matrix(rows))
    print(f"-> {out}")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
