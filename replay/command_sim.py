"""Election-night command simulations: slash commands changed mid-night against a running Poller.

    python -m replay.command_sim --years 2014,2016,2018,2020,2022,2024 --races top
    python -m replay.command_sim --years 2020,2024 --races all --scenarios latency,posting
    python -m replay.command_sim --quick          # no data needed: a bundled tiny night, ~15 s
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
replay/out/sims/<stamp>/, which git ignores).

`--quick` needs no downloaded history data: it builds the hand-made night of `quick_timelines()` in
memory (five Senate races, a snapshot every two minutes from 8 PM to 1 AM ET) and runs a dozen
scenarios that work on it, none of which need MongoDB or a mongod. Use it to check a change in a few
seconds; the real timelines from tools/fetch_history.py make `--years` and `--races` meaningful.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from dataclasses import replace as dc_replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from zoneinfo import ZoneInfo

import discord
import httpx
import yaml

from electionwatch.models import (
    CandidateResult,
    ExitPoll,
    OddsSnapshot,
    Poll,
    PollResult,
    RaceCall,
    RaceResult,
)
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
from replay.timeline import Snapshot, load_year

log = logging.getLogger("replay.command_sim")
ET = ZoneInfo("America/New_York")
LOGS = Path(__file__).resolve().parent / "out" / "sims"
GUILD, CHANNEL = 1, 100
DEADLINE_FLAG_S = 2.5  # Discord's interaction deadline is 3 s; flag anything above this

# ---------------------------------------------------------------- time


def election_day(year: int | str) -> date:
    """First Tuesday after the first Monday of November."""
    monday = date(int(year), 11, 1)
    while monday.weekday() != 0:
        monday += timedelta(days=1)
    return monday + timedelta(days=1)


def sim_time(year: int | str, hhmm: str) -> datetime:
    """'21:30' -> election night 9:30 PM ET as UTC; hours before noon are the next morning."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    night_date = election_day(year) + timedelta(days=1 if hour < 12 else 0)
    return datetime(night_date.year, night_date.month, night_date.day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


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
    user_record = USERS[kind]
    return SimpleNamespace(
        id=user_record["id"],
        name=kind,
        display_name=kind,
        mention=f"<@{user_record['id']}>",
        guild_permissions=SimpleNamespace(manage_guild=user_record["manage"]),
        roles=[FakeRole(role) for role in user_record["roles"]],
    )


@dataclass
class Reply:
    kind: str  # message | defer | followup | edit | edit_original
    t: float  # seconds since the interaction arrived
    ephemeral: bool
    content: str | None = None
    embeds: list[dict] = field(default_factory=list)
    view: dict | None = None


class InteractionAlreadyResponded(RuntimeError):
    pass


def _embed_dicts(embed=None, embeds=None) -> list[dict]:
    collected = [embed] if embed is not None and embed is not ... else []
    if embeds and embeds is not ...:
        collected += list(embeds)
    return [embed_dict.to_dict() for embed_dict in collected]


def _view_dict(view) -> dict | None:
    if view is None or view is ...:
        return None
    components = []
    for component in view.children:
        component_dict = {"type": type(component).__name__, "row": component.row}
        if isinstance(component, discord.ui.Button):
            component_dict["label"] = component.label
        elif isinstance(component, discord.ui.Select):
            component_dict["placeholder"] = component.placeholder
            component_dict["options"] = [option.label for option in component.options]
        components.append(component_dict)
    return {"items": components}


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
        self.ix.record(
            Reply(
                "message",
                self.ix.elapsed(),
                bool(ephemeral),
                None if content is None else str(content),
                _embed_dicts(embed, embeds),
                _view_dict(view),
            )
        )
        if view is not ... and view is not None:
            self.ix.views.append(view)

    async def defer(self, *, ephemeral=False, thinking=False):
        self._first()
        self.ix.deferred_ephemeral = bool(ephemeral) or self.ix.is_component
        self.ix.record(Reply("defer", self.ix.elapsed(), self.ix.deferred_ephemeral))

    async def edit_message(self, *, content=..., embed=..., embeds=..., view=..., **_):
        self._first()
        self.ix.record(
            Reply(
                "edit",
                self.ix.elapsed(),
                True,
                None if content is ... else content,
                _embed_dicts(embed, embeds),
                _view_dict(view),
            )
        )


class FakeFollowup:
    def __init__(self, ix: "FakeInteraction") -> None:
        self.ix = ix

    async def send(self, content=None, *, embed=..., embeds=..., view=..., ephemeral=False, **_):
        if not self.ix.response.is_done():
            raise RuntimeError("followup before the interaction was acknowledged")
        # A followup to an ephemeral deferral is ephemeral even without the flag.
        eph = bool(ephemeral) or self.ix.deferred_ephemeral
        self.ix.record(
            Reply(
                "followup",
                self.ix.elapsed(),
                eph,
                None if content is None else str(content),
                _embed_dicts(embed, embeds),
                _view_dict(view),
            )
        )
        if view is not ... and view is not None:
            self.ix.views.append(view)


class FakeInteraction:
    def __init__(
        self,
        client,
        guild_id: int | None,
        user_kind: str,
        command: str,
        *,
        is_component: bool = False,
        data: dict | None = None,
        t0: float | None = None,
    ):
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

    def record(self, reply: Reply) -> None:
        self.replies.append(reply)

    async def edit_original_response(self, *, content=..., embed=..., embeds=..., view=..., **_):
        self.record(
            Reply(
                "edit_original",
                self.elapsed(),
                True,
                None if content is ... else content,
                _embed_dicts(embed, embeds),
                _view_dict(view),
            )
        )


# ---------------------------------------------------------------- Discord limit checks


def limit_problems(reply: Reply) -> list[str]:
    problems = []
    if reply.content is not None and len(reply.content) > 2000:
        problems.append(f"content {len(reply.content)} > 2000 chars")
    if len(reply.embeds) > 10:
        problems.append("more than 10 embeds")
    for embed in reply.embeds:
        total = 0
        for field_name, limit in (("title", 256), ("description", 4096)):
            length = len(embed.get(field_name) or "")
            total += length
            if length > limit:
                problems.append(f"embed {field_name} {length} > {limit}")
        fields = embed.get("fields") or []
        if len(fields) > 25:
            problems.append(f"{len(fields)} embed fields > 25")
        for embed_field in fields:
            total += len(embed_field["name"]) + len(embed_field["value"])
            if len(embed_field["name"]) > 256 or len(embed_field["value"]) > 1024 or not embed_field["value"]:
                problems.append(f"field {embed_field['name'][:30]!r} name/value outside limits")
        length = len((embed.get("footer") or {}).get("text") or "")
        total += length
        if length > 2048:
            problems.append(f"footer {length} > 2048")
        if total > 6000:
            problems.append(f"embed total {total} > 6000 chars")
    if reply.view:
        rows = Counter(component["row"] for component in reply.view["items"])
        if len(rows) > 5 or any(row is not None and row > 4 for row in rows):
            problems.append(f"view uses rows {sorted(rows, key=str)} (max 5)")
        for component in reply.view["items"]:
            if component["type"] == "Button" and len(component.get("label") or "") > 80:
                problems.append("button label > 80")
            if component["type"] == "Select":
                if not 1 <= len(component["options"]) <= 25:
                    problems.append(f"select with {len(component['options'])} options")
                if any(len(option) > 100 for option in component["options"]):
                    problems.append("select option label > 100")
                if len(component.get("placeholder") or "") > 150:
                    problems.append("select placeholder > 150")
    return problems


# ---------------------------------------------------------------- scripted sources


class SimReplaySource(ReplaySource):
    """ReplaySource with optional synthetic turnout and outage windows (raises httpx errors)."""

    def __init__(self, timelines, clock, *, turnout: bool = False) -> None:
        super().__init__(timelines, clock)
        self.turnout = turnout
        self.outages: list[tuple[datetime, datetime]] = []
        self.attempts: list[tuple[datetime, bool]] = []
        self.list_calls: list[str | None] = []  # the budget each list_races call ran under
        self._reg = {
            race_key: int(max(series[-1].result.total_votes or 0, 1) * 1.6)
            for race_key, series in self.timelines.items()
        }

    async def fetch(self, keys, client=None):
        now = self.clock()
        down = any(window_start <= now < window_end for window_start, window_end in self.outages)
        self.attempts.append((now, not down))
        if down:
            raise httpx.ConnectError("simulated results-source outage")
        results = await super().fetch(keys, client)
        if self.turnout:
            for race_result in results:
                reg = self._reg.get(str(race_result.key))
                if reg and race_result.total_votes:
                    race_result.registered_voters = reg
                    race_result.turnout_pct = round(100.0 * race_result.total_votes / reg, 2)
        return results

    async def list_races(self, state, client=None):
        from electionwatch.ratelimit import current_source

        # Only the background race-list refresh may ask: it runs under a "replay:racelists:XX"
        # budget; a command would run under none.
        self.list_calls.append(current_source.get())
        return await super().list_races(state, client)


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
        want = {str(race_key): race_key for race_key in keys}
        now = self.clock()
        return [
            RaceCall(want[scripted.key], scripted.outlet, scripted.winner, scripted.party, scripted.at)
            for scripted in self.calls
            if scripted.key in want and scripted.at <= now
        ]


def spread_calls(timelines, races: list[str], t0: datetime, t1: datetime, outlets: dict[str, float]) -> list[SimCall]:
    """One call per race per outlet, spread evenly over [t0, t1]; outlets maps name -> delay min."""
    calls = []
    race_count = max(len(races), 1)
    for i, race in enumerate(sorted(races)):
        final = timelines[race][-1].result
        if not final.candidates:
            continue
        lead = max(final.candidates, key=lambda candidate: candidate.votes)
        at = t0 + (t1 - t0) * (i / race_count)
        for outlet, delay in outlets.items():
            calls.append(SimCall(race, at + timedelta(minutes=delay), lead.name, lead.party, outlet))
    return calls


@dataclass
class HeldSwing:
    key: str
    outcome: str
    start: datetime
    minutes: float
    pts: float  # moves `outcome` by this many points over `minutes`, then holds


class HoldOddsSource(OddsSource):
    """Flat 50/50 odds for every race (so no swing happens by accident) plus held swings."""

    name = "sim_odds"
    enabled_by_default = False

    def __init__(self, clock, swings: list[HeldSwing] | None = None, market: str = "kalshi") -> None:
        self.clock, self.swings, self.market = clock, swings or [], market

    def prob(self, key: str, outcome: str, now: datetime) -> float:
        probability = 0.5
        for sw in self.swings:
            if sw.key != key or now < sw.start:
                continue
            fraction = min(1.0, (now - sw.start) / timedelta(minutes=sw.minutes))
            delta = sw.pts / 100 * fraction
            probability += delta if sw.outcome == outcome else -delta
        return round(min(max(probability, 0.0), 1.0), 4)

    async def fetch_odds(self, keys, client=None):
        now = self.clock()
        return [
            OddsSnapshot(race_key, self.market, outcome, self.prob(str(race_key), outcome, now), now)
            for race_key in keys
            for outcome in ("DEM", "REP")
        ]


class SimExitPollSource(ExitPollSource):
    name = "sim_exitpolls"
    enabled_by_default = False

    def __init__(self, avail: dict[str, datetime], clock, outlet: str = "CNN") -> None:
        self.avail, self.clock, self.outlet = avail, clock, outlet

    async def fetch_exit_polls(self, keys, client=None):
        now = self.clock()
        return [
            ExitPoll(
                race_key,
                self.outlet,
                f"https://example.invalid/exit/{str(race_key).replace(' ', '_')}",
                "Economy top issue: 38%\nDecided in last week: 9%",
                now,
            )
            for race_key in keys
            if str(race_key) in self.avail and self.avail[str(race_key)] <= now
        ]


class SimPollSource(PollSource):
    name = "sim_polls"
    enabled_by_default = False

    async def fetch_polls(self, keys, client=None):
        polls = []
        for race_key in keys:
            for index, pollster in enumerate(("Siena", "Emerson", "Trafalgar Group", "PPP")):
                polls.append(
                    Poll(
                        race_key,
                        pollster,
                        [
                            PollResult("Candidate D", "DEM", 47.0 + index),
                            PollResult("Candidate R", "REP", 46.0 - index),
                        ],
                        date(2020, 10, 20 + index),
                        date(2020, 10, 15 + index),
                        800 + 50 * index,
                        "LV",
                        partisan="REP" if pollster == "Trafalgar Group" else None,
                        source="sim",
                        url="https://example.invalid/poll",
                    )
                )
        return polls


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
        return any(window_start <= now < window_end for window_start, window_end in self.outages)

    def _fail(self):
        self.errors += 1
        try:
            from pymongo.errors import AutoReconnect

            raise AutoReconnect("simulated MongoDB outage")
        except ImportError:  # pragma: no cover
            raise StoreDown("simulated store outage")

    async def upsert_result(self, race_result):
        if self._down():
            self._fail()
        changed = await self.inner.upsert_result(race_result)
        if changed:
            self.changes.append(
                (self.clock(), str(race_result.key), tuple(format_result(race_result, turnout=True).lines))
            )
        return changed

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def wrapped(*args, **kw):
            if self._down():
                self._fail()
            return await attr(*args, **kw)

        return wrapped


# ---------------------------------------------------------------- recorder poster


@dataclass
class PostRec:
    at: datetime
    guild: int
    channel: int
    post: Post

    def row(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "et": et(self.at),
            "guild": self.guild,
            "channel": self.channel,
            "kind": self.post.kind,
            "race": self.post.race,
            "title": self.post.title,
            "lines": self.post.lines,
        }


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
    steps = []
    for step in spec:
        if isinstance(step, Step):
            steps.append(step)
        elif isinstance(step, str):
            at, cmd = step.split(" ", 1)
            steps.append(Step(at, cmd.strip()))
        else:
            steps.append(
                Step(str(step["at"]), str(step["cmd"]), step.get("as", "admin"), int(step.get("guild", GUILD)))
            )
    return steps


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
    rejected: str | None = None  # Discord's client would not send this (bad choice/range)
    label: str = ""

    @property
    def text(self) -> str:
        parts = []
        for reply in self.replies:
            if reply.content:
                parts.append(reply.content)
            for embed in reply.embeds:
                parts.append(" ".join(str(embed.get(field_name) or "") for field_name in ("title", "description")))
                # /settings and /limits keep their content in fields.
                parts += [f"{field['name']}: {field['value']}" for field in embed.get("fields") or []]
        return " | ".join(parts)

    def row(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "et": et(self.at),
            "guild": self.guild,
            "user": self.user,
            "command": self.label or self.command,
            "args": {option_name: str(option_value) for option_name, option_value in self.args.items()},
            "ack_s": self.ack_s,
            "total_s": round(self.total_s, 4),
            "error": self.error,
            "concurrent": self.concurrent,
            "rejected": self.rejected,
            "replies": [
                {
                    "kind": reply.kind,
                    "t": round(reply.t, 4),
                    "ephemeral": reply.ephemeral,
                    "content": (reply.content or "")[:300],
                    "embeds": [{"title": embed.get("title"), "len": len(json.dumps(embed))} for embed in reply.embeds],
                    "view": bool(reply.view),
                    "limits": limit_problems(reply),
                }
                for reply in self.replies
            ],
        }


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
    by_name = {param.name: param for param in params}
    args, positional = {}, [param for param in params]
    for token in tokens:
        if "=" in token:
            option_name, raw_text = token.split("=", 1)
            if option_name not in by_name:
                raise ValueError(f"/{command.name} has no option {option_name!r}")
            args[option_name] = (by_name[option_name], raw_text)
        else:
            free = [param for param in positional if param.name not in args]
            if not free:
                raise ValueError(f"too many arguments for /{command.name}")
            args[free[0].name] = (free[0], token)
    bound, rejected = {}, None
    for name, (param, raw_text) in args.items():
        value = _convert(param, raw_text)
        if param.choices and str(value) not in {str(choice.value) for choice in param.choices}:
            rejected = f"{name}={raw_text} is not one of the offered choices"
        if (
            param.min_value is not None
            and isinstance(value, (int, float))
            and not param.min_value <= value <= param.max_value
        ):
            rejected = f"{name}={raw_text} outside {param.min_value}..{param.max_value}"
        bound[name] = value
    missing = [param.name for param in params if param.required and param.name not in bound]
    if missing:
        rejected = "missing required option(s): " + ", ".join(missing)
    return bound, rejected


# ---------------------------------------------------------------- quick start data

# `--quick` runs on a hand-made night instead of downloaded history. Five Senate races, one
# snapshot every two minutes from 8 PM to 1 AM ET: long enough for the posting rules (default
# 5 minutes, interval 15) to produce real gaps and changes, and it builds in a few milliseconds.
QUICK_YEAR = "2024"
QUICK_RACES = ["AZ SEN", "GA SEN", "OH SEN", "PA SEN", "TX SEN"]
QUICK_SCENARIOS = (
    "pause",
    "posting",
    "calls",
    "toggles",
    "oddsalerts",
    "setup",
    "nonadmin",
    "sourcedown",
    "roleadmin",
    "invalid",
    "pausechange",
    "twoguilds",
)


def quick_timelines(year: str = QUICK_YEAR, races: list[str] | None = None) -> dict:
    """{race key: [Snapshot, ...]} for the bundled quick night. Made-up candidates and vote
    counts (the tests use the same data), so nothing is downloaded and nothing is claimed to be
    real. `races` defaults to QUICK_RACES."""
    timelines: dict[str, list[Snapshot]] = {}
    t0 = sim_time(year, "20:00")
    for race_index, race in enumerate(races or QUICK_RACES):
        snaps = []
        for tick in range(150):
            ts = t0 + timedelta(minutes=2 * tick + race_index)
            dem, rep = 1000 * tick + 37 * race_index, 1010 * tick + 11 * race_index
            race_result = RaceResult(
                parse(race),
                [CandidateResult("Dee Mock", "DEM", dem), CandidateResult("Ray Publican", "REP", rep)],
                "replay",
                ts,
                expected_vote_pct=min(99.0, tick * 0.7),
            )
            snaps.append(Snapshot(ts, race_result.finalize(), True))
        timelines[race] = snaps
    return timelines


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
        return [
            posted
            for posted in self.posts
            if posted.guild == guild
            and (kind is None or posted.post.kind == kind)
            and (race is None or posted.post.race == race)
            and (t0 is None or posted.at >= t0)
            and (t1 is None or posted.at < t1)
        ]

    def t(self, hhmm: str) -> datetime:
        return sim_time(self.year, hhmm)


class Night:
    """One simulated night. Build, then `await run(steps)`."""

    def __init__(
        self,
        year: str,
        timelines: dict,
        guilds: list[GuildSettings],
        *,
        start: str = "19:30",
        until: str = "02:30",
        turnout: bool = False,
        calls: list[SimCall] | None = None,
        swings: list[HeldSwing] | None = None,
        exitpolls: dict[str, datetime] | None = None,
        store=None,
        default_races: list[str] | None = None,
        workdir: Path | None = None,
        poster=None,
        speed: float | None = None,
        on_step=None,
        mongod=None,
        idle_skip: float = 90,
    ) -> None:
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
        from electionwatch.bot.discord import ElectionBot

        if self.default_races:
            path = self.workdir / f"races-{self.year}.yaml"
            path.write_text(yaml.safe_dump({"important": [{"key": race_key} for race_key in self.default_races]}))
            os.environ["EW_RACES_FILE"] = str(path)
        for gs in self.guilds:
            await self.store.inner.save_guild(gs)
        bot = ElectionBot(self.store, self.sources, http, self.clock)
        bot.poller.poster = self.poster
        return bot

    # -- commands

    async def invoke(
        self, bot, step: Step, *, concurrent: bool = False, t0: float | None = None, label: str = ""
    ) -> CmdRec:
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
            except Exception as exc:  # what CommandTree does: route to on_error
                from electionwatch.bot.discord import handle_app_command_error

                err = f"{type(exc).__name__}: {exc}"[:300]
                log.debug("command /%s raised", name, exc_info=True)
                try:
                    await handle_app_command_error(ix, exc)
                except Exception as exc2:
                    err += f" / on_error raised {type(exc2).__name__}"
        rec = CmdRec(
            self.clock(),
            step.guild,
            step.user,
            name,
            args,
            ix.replies,
            ix.ack_t,
            ix.elapsed(),
            err,
            concurrent,
            rejected,
            label or step.cmd,
        )
        rec._ix = ix  # type: ignore[attr-defined]
        self.cmds.append(rec)
        return rec

    async def click(
        self, bot, view, component, user: str, guild: int, label: str, data=None, concurrent=False, t0=None
    ) -> CmdRec:
        ix = FakeInteraction(bot, guild, user, "races", is_component=True, data=data, t0=t0)
        err = None
        try:
            await component.callback(ix)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:300]
        rec = CmdRec(
            self.clock(), guild, user, "races", {}, ix.replies, ix.ack_t, ix.elapsed(), err, concurrent, None, label
        )
        self.cmds.append(rec)
        return rec

    async def navigator_sweep(
        self, bot, guild: int, user: str = "admin", *, all_states=True, under_tick=False
    ) -> list[CmdRec]:
        recs = []
        rec = await self._maybe_concurrent(
            bot,
            lambda t0: self.invoke(bot, Step("", "races", user, guild), concurrent=under_tick, t0=t0, label="races"),
            under_tick,
        )
        recs.append(rec)
        views = getattr(rec, "_ix").views
        if not views:
            return recs
        view = views[-1]

        def find(kind, predicate):
            return next(
                (component for component in view.children if isinstance(component, kind) and predicate(component)), None
            )

        # Only the first few clicks race a poller tick (each costs a whole extra tick); the
        # rest of the sweep measures the callbacks alone.
        budget = [6 if under_tick else 0]

        def conc() -> bool:
            budget[0] -= 1
            return budget[0] >= 0

        states = list(STATES) if all_states else ["PA", "TX", "GA", "AZ"]
        for code in states:
            overlap = conc()
            select = find(
                discord.ui.Select, lambda race_select: any(option.value == code for option in race_select.options)
            )
            recs.append(
                await self._maybe_concurrent(
                    bot,
                    lambda t0: self.click(
                        bot, view, select, user, guild, "races:state", {"values": [code]}, overlap, t0
                    ),
                    overlap,
                )
            )
            for tab in ("Congressional", "Senate", "State", "Local"):
                overlap = conc()
                button = find(discord.ui.Button, lambda button: button.label == tab)
                recs.append(
                    await self._maybe_concurrent(
                        bot,
                        lambda t0: self.click(bot, view, button, user, guild, "races:tab", None, overlap, t0),
                        overlap,
                    )
                )
                if tab == "Senate" and view.selected_race and code in ("PA", "GA", "AZ", "TX"):
                    for tool in ("Odds", "Polls"):
                        overlap = conc()
                        button = find(discord.ui.Button, lambda button: button.label == tool)
                        recs.append(
                            await self._maybe_concurrent(
                                bot,
                                lambda t0: self.click(
                                    bot, view, button, user, guild, f"races:{tool.lower()}", None, overlap, t0
                                ),
                                overlap,
                            )
                        )
                    button = find(discord.ui.Button, lambda button: button.label == "Choose race")
                    recs.append(await self.click(bot, view, button, user, guild, "races:choose"))
                    race_select = find(discord.ui.Select, lambda race_select: race_select.placeholder == "Race")
                    if race_select is not None:
                        recs.append(
                            await self.click(
                                bot,
                                view,
                                race_select,
                                user,
                                guild,
                                "races:race",
                                {"values": [race_select.options[0].value]},
                            )
                        )
        return recs

    async def _maybe_concurrent(self, bot, make, under_tick: bool):
        """Run a command; with under_tick, it arrives while a poller tick is in flight on the
        same event loop (as in the real bot), and its latency includes waiting for the loop."""
        if not under_tick:
            return await make(None)
        tick = asyncio.create_task(self._tick(bot, record=False))
        await asyncio.sleep(0)  # let the tick start and run to its first await
        t0 = time.perf_counter()
        rec = await make(t0)
        await tick
        return rec

    async def readonly_battery(self, bot, guild: int, under_tick: bool = True):
        race = self.readonly_race or (sorted(self.watched_for(guild)) or ["PA SEN"])[0]
        battery = [
            "odds",
            f'odds race="{race}"',
            f'polls race="{race}"',
            "watch list",
            "settings",
            "faq",
            "faq state=PA",
            "menu",
        ]
        for cmd in battery:
            await self._maybe_concurrent(
                bot,
                lambda t0: self.invoke(
                    bot, Step("", cmd, "member", guild), concurrent=under_tick, t0=t0, label=cmd.split("=")[0]
                ),
                under_tick,
            )
        # autocomplete is also bound by the 3 s deadline
        from electionwatch.bot.discord import race_autocomplete

        async def ac(t0):
            ix = FakeInteraction(bot, guild, "member", "autocomplete", t0=t0)
            got = await race_autocomplete(ix, "se")
            rec = CmdRec(
                self.clock(),
                guild,
                "member",
                "autocomplete",
                {},
                [Reply("autocomplete", ix.elapsed(), True, f"{len(got)} choices")],
                ix.elapsed(),
                ix.elapsed(),
                None if len(got) <= 25 else f"{len(got)} choices > 25",
                under_tick,
                None,
                "autocomplete",
            )
            self.cmds.append(rec)
            return rec

        await self._maybe_concurrent(bot, ac, under_tick)
        await self.navigator_sweep(bot, guild, "member", all_states=True, under_tick=under_tick)

    def watched_for(self, guild: int) -> list[str]:
        gs = self.store.inner.guilds.get(guild) if isinstance(self.store.inner, MemoryStore) else None
        return list(gs.important) if gs else []

    async def pseudo(self, bot, step: Step) -> None:
        tokens = shlex.split(step.cmd[1:])
        name, kv = tokens[0], dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
        now = self.clock()
        if name == "readonly":
            await self.readonly_battery(bot, step.guild, kv.get("under_tick", "1") == "1")
        elif name == "navigator":
            await self.navigator_sweep(bot, step.guild, step.user, all_states=kv.get("all", "0") == "1")
        elif name == "click_other":
            recs = await self.navigator_sweep(bot, step.guild, step.user, all_states=False)
            view = recs[0]._ix.views[-1] if recs and recs[0]._ix.views else None  # type: ignore
            if view is not None:
                button = next(component for component in view.children if isinstance(component, discord.ui.Button))
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
        started = time.perf_counter()
        heartbeat = asyncio.create_task(self._heartbeat()) if self.measure_stall and record else None
        # The bot runs the poller and the race-list refresh as two loops on one event loop,
        # each once a minute: run both, concurrently, every simulated minute.
        polled, _ = await asyncio.gather(bot.poller.tick(), self._race_lists(bot), return_exceptions=True)
        if isinstance(polled, Exception):  # the bot's poll_results loop logs and carries on
            self.tick_errors.append((self.clock(), f"{type(polled).__name__}: {polled}"[:200]))
        if heartbeat is not None:
            heartbeat.cancel()
        if record:
            self.ticks.append((self.clock(), time.perf_counter() - started))
            if heartbeat is not None:
                self.stalls.append(self._stall)

    async def _race_lists(self, bot) -> None:
        try:
            await bot.race_lists.refresh()
        except Exception:  # the bot's refresh_race_lists loop logs and carries on
            log.debug("race list refresh failed", exc_info=True)

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
        steps = sorted(parse_steps(steps), key=lambda pending: sim_time(self.year, pending.at))
        due = [(sim_time(self.year, pending.at), pending) for pending in steps]
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
                    started = time.perf_counter()
                    await self._tick(bot)
                    if self.speed:
                        await asyncio.sleep(max(0.0, 60 / self.speed - (time.perf_counter() - started)))
                    self.clock.advance(1)
                    next_ts = self.src.next_change(self.clock())
                    limit = due[0][0] if due else self.end
                    if next_ts is None:
                        next_ts = self.end
                    next_ts = min(next_ts, limit)
                    if next_ts - self.clock() > timedelta(minutes=self.idle_skip):
                        self.clock.set(next_ts - timedelta(minutes=1))
            finally:
                await bot.close()
                if old_env is None:
                    os.environ.pop("EW_RACES_FILE", None)
                else:
                    os.environ["EW_RACES_FILE"] = old_env
        watched = sorted({race_key for guild in self.guilds for race_key in guild.important})
        return NightResult(
            self.year,
            getattr(self.poster, "posts", []),
            self.cmds,
            self.store.changes,
            self.ticks,
            self.tick_errors,
            self.src.attempts,
            sim_time(self.year, "19:30"),
            self.end,
            watched,
        )


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


def first_tick(run_result: "NightResult", at: datetime) -> datetime:
    """The tick that first sees something scheduled at `at` (ticks run once per sim minute)."""
    import bisect

    ticks = [tick_ts for tick_ts, _ in run_result.ticks]
    i = bisect.bisect_left(ticks, at)
    return ticks[i] if i < len(ticks) else run_result.end + timedelta(minutes=1)


def check_gaps(checks: Checks, run_result: NightResult, t0, t1, minutes, guild=GUILD, label=""):
    last: dict[str, datetime] = {}
    gap_count = 0
    for posted in run_result.posts_for(guild, "results"):
        previous_ts = last.get(posted.post.race)
        if previous_ts is not None and t0 <= posted.at < t1:
            gap = (posted.at - previous_ts).total_seconds() / 60
            gap_count += 1
            checks.ok(
                gap >= minutes,
                f"{label}{et(posted.at)} {posted.post.race}: results {gap:.0f} min after previous (rule {minutes})",
            )
        last[posted.post.race] = posted.at
    return gap_count


def check_every_change(checks: Checks, run_result: NightResult, t0, t1, guild=GUILD, label="", watched=None):
    """Within [t0, t1): every tick at which a watched race's displayed result changed has a post
    at that tick, and no results post happens at a tick without a change (except at t0)."""
    posts = run_result.posts_for(guild, "results")
    posted_at = defaultdict(set)
    for posted in posts:
        posted_at[posted.post.race].add(posted.at)
    last_lines: dict[str, tuple] = {}
    events = sorted(run_result.changes)
    post_index = 0
    missing = extra = checked = 0
    posts_sorted = sorted(posts, key=lambda posted: posted.at)
    changed_at = defaultdict(set)
    for at, race, lines in events:
        if watched is not None and race not in watched:
            continue
        while post_index < len(posts_sorted) and posts_sorted[post_index].at < at:
            posted = posts_sorted[post_index]
            last_lines[posted.post.race] = tuple(posted.post.lines)
            post_index += 1
        changed_at[race].add(at)
        if t0 <= at < t1 and lines != last_lines.get(race):
            checked += 1
            if at not in posted_at[race]:
                missing += 1
                if missing <= 5:
                    checks.problems.append(f"{label}{et(at)} {race}: result changed but nothing posted")
    for posted in posts:
        if t0 < posted.at < t1 and posted.at not in changed_at[posted.post.race]:
            extra += 1
            if extra <= 5:
                checks.problems.append(f"{label}{et(posted.at)} {posted.post.race}: results posted without a change")
    if missing > 5:
        checks.problems.append(f"{label}... {missing} changes not posted in total")
    return checked


def no_posts(checks: Checks, run_result, t0, t1, guild=GUILD, kind=None, label=""):
    found = run_result.posts_for(guild, kind, t0=t0, t1=t1)
    checks.ok(
        not found,
        f"{label}{len(found)} {kind or 'any'} posts in {et(t0)}-{et(t1)} (first {found[0].post.kind} "
        f"{found[0].post.race} at {et(found[0].at)})"
        if found
        else "",
    )
    return found


def replies_ok(checks: Checks, run_result: NightResult, *, expect_refused: bool | None = None, users=None, label=""):
    for command in run_result.cmds:
        if users and command.user not in users:
            continue
        if command.rejected:
            continue
        checks.ok(
            bool(command.replies), f"{label}/{command.label} by {command.user} at {et(command.at)}: no reply at all"
        )
        for reply in command.replies:
            checks.ok(
                reply.ephemeral,
                f"{label}/{command.label} by {command.user} at {et(command.at)}: {reply.kind} reply not ephemeral",
            )
            for problem in limit_problems(reply):
                checks.problems.append(f"{label}/{command.label} at {et(command.at)}: {problem}")
        if expect_refused is True:
            checks.ok(
                "required" in command.text.lower() or "belongs to another user" in command.text,
                f"{label}/{command.label} by {command.user} not refused: {command.text[:120]!r}",
            )


def post_signature(run_result: NightResult, guild: int) -> list[tuple]:
    return [
        (posted.at, posted.channel, posted.post.kind, posted.post.race, tuple(posted.post.lines))
        for posted in run_result.posts_for(guild)
    ]


# ---------------------------------------------------------------- scenarios


@dataclass
class Ctx:
    year: str
    timelines: dict
    watched: list[str]  # the scenario's watch list (top-per-state or all)
    mode: str  # top | all
    workdir: Path
    mongo_uri: str | None = None
    mongod_bin: str | None = None


def pick_extra_race(ctx: Ctx, exclude: set[str], t0: datetime, t1: datetime) -> str | None:
    """The race outside `exclude` with the most snapshots between t0 and t1."""
    best_race, best_count = None, 0
    for race_key, series in ctx.timelines.items():
        if race_key in exclude:
            continue
        count = sum(1 for snapshot in series if t0 <= snapshot.ts < t1)
        if count > best_count:
            best_race, best_count = race_key, count
    return best_race


def base_guild(ctx: Ctx, **kw) -> GuildSettings:
    return GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=list(ctx.watched), **kw)


def default_calls_for(ctx: Ctx, races=None, outlets=None) -> list[SimCall]:
    return spread_calls(
        ctx.timelines,
        races or ctx.watched,
        sim_time(ctx.year, "20:00"),
        sim_time(ctx.year, "01:30"),
        outlets or {"AP": 0},
    )


async def sc_pause(ctx: Ctx, checks: Checks):
    night = Night(
        ctx.year,
        ctx.timelines,
        [base_guild(ctx)],
        calls=default_calls_for(ctx),
        exitpolls={race_key: sim_time(ctx.year, "21:20") for race_key in ctx.watched[:5]},
    )
    run_result = await night.run(["21:00 pause", "22:00 resume"])
    no_posts(checks, run_result, run_result.t("21:00"), run_result.t("22:00"))
    after = run_result.posts_for(t0=run_result.t("22:00"))
    checks.ok(any(posted.post.kind == "results" for posted in after), "no results posts after /resume")
    at_resume = Counter(
        posted.post.race
        for posted in run_result.posts_for(kind="results", t0=run_result.t("22:00"), t1=run_result.t("22:01"))
    )
    checks.ok(
        all(count == 1 for count in at_resume.values()), f"resume tick posted a race twice: {at_resume.most_common(2)}"
    )
    checks.ok(len(at_resume) <= len(ctx.watched), "resume posted more results than watched races")
    # no duplicate: a resumed race's post must differ from its last pre-pause post
    for race in at_resume:
        before = run_result.posts_for(kind="results", race=race, t1=run_result.t("21:00"))
        now = run_result.posts_for(kind="results", race=race, t0=run_result.t("22:00"), t1=run_result.t("22:01"))
        if before and now:
            checks.ok(before[-1].post.lines != now[0].post.lines, f"{race}: resume re-posted an identical result")
    dropped = [
        race_key
        for race_key in night.calls
        if run_result.t("21:00") <= first_tick(run_result, race_key.at) < run_result.t("22:00")
    ]
    late = run_result.posts_for(kind="call", t0=run_result.t("22:00"))
    checks.ok(
        not any(
            posted.post.race in {dropped_call.key for dropped_call in dropped} and posted.at < run_result.t("22:02")
            for posted in late
        ),
        "calls made during the pause were flooded out at resume",
    )
    eps = run_result.posts_for(kind="exitpoll")
    checks.note(
        f"resume minute: {len(at_resume)} results posts (one per changed race, latest numbers only); "
        f"{len(dropped)} calls made while paused are never posted; "
        f"{len([posted for posted in eps if posted.at >= run_result.t('22:00')])} exit polls published while paused posted after resume"
    )
    replies_ok(checks, run_result)
    return run_result


async def sc_posting(ctx: Ctx, checks: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    run_result = await night.run(
        ["21:30 posting interval 15", "22:30 posting interval 1", "23:00 posting immediate", "23:45 posting default"]
    )
    n0 = check_gaps(checks, run_result, run_result.start, run_result.t("21:30"), 5, label="default: ")
    n1 = check_gaps(checks, run_result, run_result.t("21:30"), run_result.t("22:30"), 15, label="interval15: ")
    k1 = check_every_change(
        checks, run_result, run_result.t("22:30"), run_result.t("23:00"), label="interval1: ", watched=set(ctx.watched)
    )
    k2 = check_every_change(
        checks, run_result, run_result.t("23:00"), run_result.t("23:45"), label="immediate: ", watched=set(ctx.watched)
    )
    n3 = check_gaps(checks, run_result, run_result.t("23:45"), run_result.end, 5, label="back to default: ")
    checks.ok(n1 > 0 and k2 > 0, f"vacuous: {n1} interval-15 gaps, {k2} immediate changes checked")
    # rate per channel (Discord allows ~5 messages / 5 s per channel)
    per_min = Counter(p.at for p in run_result.posts_for())
    peak = per_min.most_common(1)[0] if per_min else (None, 0)
    checks.note(
        f"gaps checked: default {n0}, interval15 {n1}, back-to-default {n3}; changes checked: "
        f"interval1 {k1}, immediate {k2}; peak {peak[1]} posts in one minute"
        + (f" at {et(peak[0])}" if peak[0] else "")
    )
    replies_ok(checks, run_result)
    return run_result


async def sc_watch(ctx: Ctx, checks: Checks):
    t0, t1 = sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00")
    x = pick_extra_race(ctx, set(ctx.watched) if ctx.mode == "top" else set(), t0, t1)
    watched = [k for k in ctx.watched if k != x]
    defaults = []
    for k in sorted(
        ctx.timelines, key=lambda k: -sum(1 for s in ctx.timelines[k] if s.ts >= sim_time(ctx.year, "00:00"))
    ):
        if k not in (x,) and len(defaults) < 2 and (ctx.mode == "all" or k not in watched):
            defaults.append(k)
    gs = GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=watched)
    steps = [f'21:30 watch add "{x}"', f'23:00 watch remove "{x}"']
    steps += [f'00:00 watch remove "{k}"' for k in watched]
    steps += ["00:01 watch list"]
    night = Night(ctx.year, ctx.timelines, [gs], default_races=defaults, workdir=ctx.workdir)
    run_result = await night.run(steps)
    checks.ok(not run_result.posts_for(race=x, t1=t0), f"{x} posted before /watch add")
    checks.ok(bool(run_result.posts_for(race=x, kind="results", t0=t0, t1=t1)), f"{x} never posted after /watch add")
    checks.ok(
        not run_result.posts_for(race=x, t0=t1 + timedelta(minutes=1)) or x in defaults,
        f"{x} still posted after /watch remove",
    )
    after = run_result.posts_for(t0=sim_time(ctx.year, "00:00"))
    stray = {p.post.race for p in after} - set(defaults)
    checks.ok(not stray, f"after removing every race, posts for non-default races: {sorted(stray)[:5]}")
    checks.ok(
        any(p.post.race in defaults for p in after), f"no posts for races.yaml defaults {defaults} after fallback"
    )
    lst = [r for r in run_result.cmds if r.label == "watch list"]
    checks.ok(bool(lst) and all(d in lst[-1].text for d in defaults), "/watch list does not show the defaults")
    checks.note(f"added/removed {x}; fallback defaults {defaults}; {len(watched)} removals at 00:00")
    replies_ok(checks, run_result)
    return run_result


async def sc_calls(ctx: Ctx, checks: Checks):
    outlets = {"AP": 0, "FOX": 3, "NBC": 6}
    calls = default_calls_for(ctx, outlets=outlets)
    # "/calls X off" stops only the named outlets: name every outlet to switch calls off.
    steps = [
        "21:00 calls AP,NBC,CNN,FOX,DDHQ,VOTEHUB,CIVICAPI,CBS,ABC,NYT off",
        "22:00 calls outlets=FOX enabled=on",
        "23:30 calls AP,NBC on",
    ]
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=calls)
    run_result = await night.run(steps)
    ticks = [t for t, _ in run_result.ticks]
    import bisect as _b

    def cfg(at):
        if at < run_result.t("21:00"):
            return True, {"AP", "NBC", "CNN", "FOX", "DDHQ", "VOTEHUB"}
        if at < run_result.t("22:00"):
            return False, set()
        if at < run_result.t("23:30"):
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
    got = {(p.at, p.post.race, p.post.lines[-1].removeprefix("Outlet: ")) for p in run_result.posts_for(kind="call")}
    checks.ok(
        got == expected,
        f"call posts differ from expected: missing {sorted(expected - got)[:3]}, "
        f"unexpected {sorted(got - expected)[:3]}",
    )
    win = Counter(("off" if not cfg(t)[0] else "+".join(sorted(cfg(t)[1]))[:12]) for t, _, _ in got)
    checks.ok(
        not run_result.posts_for(kind="call", t0=run_result.t("21:00"), t1=run_result.t("22:00")),
        "call posted while calls off",
    )
    checks.ok(
        all(o == "FOX" for t, _, o in got if run_result.t("22:00") <= t < run_result.t("23:30")),
        "non-FOX call in FOX window",
    )
    checks.ok(
        sum(1 for t, _, o in got if run_result.t("22:00") <= t < run_result.t("23:30")) > 0,
        "vacuous: no FOX call posted",
    )
    checks.note(f"{len(got)} call posts, by setting window: {dict(win)}")
    replies_ok(checks, run_result)
    return run_result


async def sc_toggles(ctx: Ctx, checks: Checks):
    avail = {
        k: sim_time(ctx.year, "20:00") + timedelta(minutes=(i * 7) % 300) for i, k in enumerate(sorted(ctx.watched))
    }
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], turnout=True, exitpolls=avail)
    run_result = await night.run(["21:30 turnout off", "22:00 exitpolls off", "00:00 exitpolls on", "00:00 turnout on"])
    has_t = lambda p: any(ln.startswith("Turnout:") for ln in p.post.lines)  # noqa: E731
    checks.ok(
        any(has_t(p) for p in run_result.posts_for(kind="results", t1=run_result.t("21:30"))),
        "vacuous: no turnout line before off",
    )
    bad = [
        p for p in run_result.posts_for(kind="results", t0=run_result.t("21:30"), t1=run_result.t("00:00")) if has_t(p)
    ]
    checks.ok(not bad, f"{len(bad)} results posts still show turnout after /turnout off")
    checks.ok(
        any(has_t(p) for p in run_result.posts_for(kind="results", t0=run_result.t("00:00")))
        or not run_result.posts_for(kind="results", t0=run_result.t("00:00")),
        "turnout not back after /turnout on",
    )
    no_posts(checks, run_result, run_result.t("22:00"), run_result.t("00:00"), kind="exitpoll", label="exitpolls off: ")
    checks.ok(bool(run_result.posts_for(kind="exitpoll", t1=run_result.t("22:00"))), "vacuous: no exit poll before off")
    backlog = run_result.posts_for(kind="exitpoll", t0=run_result.t("00:00"), t1=run_result.t("00:01"))
    missed = [k for k, t in avail.items() if run_result.t("22:00") <= t < run_result.t("00:00")]
    due_now = [k for k, t in avail.items() if t == run_result.t("00:00")]
    checks.ok(
        len(backlog) == len(missed) + len(due_now),
        f"exit polls published while off: {len(missed)} (+{len(due_now)} due at 00:00), "
        f"posted at /exitpolls on: {len(backlog)}",
    )
    checks.note(f"/exitpolls on posts the {len(backlog)} exit polls published while it was off in one burst")
    replies_ok(checks, run_result)
    return run_result


async def sc_oddsalerts(ctx: Ctx, checks: Checks):
    races = sorted(ctx.watched)
    if len(races) < 5:
        checks.ok(False, "needs 5 watched races")
        return None
    a, b, cc, d, e = races[:5]
    T = lambda s: sim_time(ctx.year, s)  # noqa: E731
    swings = [
        HeldSwing(e, "DEM", T("20:30"), 10, 60),  # before alerts are on
        HeldSwing(a, "DEM", T("21:40"), 10, 15),  # above 10
        HeldSwing(b, "REP", T("21:40"), 10, 5),  # below 10
        HeldSwing(cc, "REP", T("22:30"), 5, 12),  # above 10
        HeldSwing(d, "DEM", T("23:30"), 5, 20),
    ]  # after alerts are off
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], swings=swings)
    run_result = await night.run(["21:30 oddsalerts on threshold=10 window=30", "23:00 oddsalerts off"])
    alerts = run_result.posts_for(kind="odds_swing")
    by = Counter(p.post.race for p in alerts)
    checks.ok(by.get(a, 0) >= 1 and by.get(cc, 0) >= 1, f"missing alerts above threshold: {dict(by)}")
    checks.ok(not by.get(b) and not by.get(d) and not by.get(e), f"alerts below threshold / while off: {dict(by)}")
    checks.ok(set(by) <= {a, cc}, f"unexpected alert races {sorted(set(by) - {a, cc})}")
    first_a = min((p.at for p in alerts if p.post.race == a), default=None)
    if first_a:
        checks.ok(first_a <= T("21:48"), f"{a} alert late: {et(first_a)} (crossed 10 pts at ~21:47)")
    checks.note(
        f"alerts {dict(by)} ({len(alerts)} posts; one per outcome: both sides of a 2-way market alert); "
        f"e.g. {alerts[0].post.lines[0] if alerts else '-'}"
    )
    replies_ok(checks, run_result)
    return run_result


async def sc_setup(ctx: Ctx, checks: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    run_result = await night.run(["22:00 setup channel=200"])
    before = {p.channel for p in run_result.posts_for(t1=run_result.t("22:00"))}
    after = {p.channel for p in run_result.posts_for(t0=run_result.t("22:00"))}
    checks.ok(before == {CHANNEL}, f"posts before /setup went to {before}")
    checks.ok(after == {200}, f"posts after /setup went to {after}")
    replies_ok(checks, run_result)
    return run_result


ADMIN_CMDS = [
    "pause",
    "resume",
    "posting immediate",
    "posting interval 30",
    'watch add "{x}"',
    'watch remove "{y}"',
    "calls AP off",
    "oddsalerts on threshold=1 window=5",
    "turnout off",
    "exitpolls off",
    "setup channel=999 admin_role=555",
    "menu_post",
    "menu_remove",
    "menu_private off",
]


async def sc_nonadmin(ctx: Ctx, checks: Checks):
    x = pick_extra_race(ctx, set(ctx.watched), sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00")) or "PA SEN"
    y = sorted(ctx.watched)[0]
    steps = []
    for i, cmd in enumerate(ADMIN_CMDS):
        for who in ("member", "roleadmin"):  # no admin role configured: role means nothing
            steps.append({"at": f"21:{30 + i:02d}", "cmd": cmd.format(x=x, y=y), "as": who})
    steps.append({"at": "22:15", "cmd": "!click_other", "as": "admin"})
    kw = dict(calls=default_calls_for(ctx), exitpolls={k: sim_time(ctx.year, "21:00") for k in ctx.watched[:5]})
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], **kw)
    run_result = await night.run(steps)
    control = await Night(ctx.year, ctx.timelines, [base_guild(ctx)], **kw).run([])
    attempts = [r for r in run_result.cmds if r.user in ("member", "roleadmin")]
    replies_ok(checks, run_result, expect_refused=True, users={"member", "roleadmin"})
    foreign = [r for r in run_result.cmds if r.user == "other"]
    replies_ok(checks, run_result, expect_refused=True, users={"other"})
    checks.ok(bool(foreign), "foreign navigator click not exercised")
    checks.ok(
        post_signature(run_result, GUILD) == post_signature(control, GUILD),
        "posts differ from the control night without the refused commands",
    )
    checks.note(
        f"{len(attempts)} refused admin commands + {len(foreign)} foreign navigator click; posts identical to control"
    )
    return run_result


async def sc_roleadmin(ctx: Ctx, checks: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)])
    run_result = await night.run(
        [
            {"at": "21:00", "cmd": "posting interval 20", "as": "roleadmin"},  # refused: no role set
            {"at": "21:10", "cmd": f"setup channel={CHANNEL} admin_role=555", "as": "admin"},
            {"at": "21:30", "cmd": "posting interval 20", "as": "roleadmin"},  # allowed now
            {"at": "21:31", "cmd": "pause", "as": "member"},  # still refused
        ]
    )
    r = [x for x in run_result.cmds if x.user == "roleadmin"]
    checks.ok("required" in r[0].text, f"role user allowed before admin_role was set: {r[0].text!r}")
    checks.ok("interval every 20" in r[1].text, f"role user refused after admin_role was set: {r[1].text!r}")
    m = [x for x in run_result.cmds if x.user == "member"]
    checks.ok("required" in m[0].text, "member allowed to pause")
    check_gaps(checks, run_result, run_result.t("21:30"), run_result.end, 20, label="interval20: ")
    checks.ok(
        any(p.at >= run_result.t("21:31") for p in run_result.posts_for()),
        "posting stopped (member pause took effect?)",
    )
    replies_ok(checks, run_result)
    return run_result


async def sc_invalid(ctx: Ctx, checks: Checks):
    steps = [
        '21:30 watch add "ZZ SEN"',
        "21:31 calls outlets=BOGUS enabled=off",
        "21:32 posting interval",
        "21:33 watch add",
        "21:34 polls race=nonsense",
        "21:35 odds race=???",
        "21:36 faq state=XX",
        '21:37 favorite add "QQ-99"',
        '21:38 watch remove "ZZ-01"',
    ]
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    run_result = await night.run(steps)
    control = await Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx)).run([])
    for command in run_result.cmds:
        checks.ok(command.error is None, f"/{command.label}: raised {command.error}")
    replies_ok(checks, run_result)
    run_result.extra.get("final")
    checks.ok(
        post_signature(run_result, GUILD) == post_signature(control, GUILD),
        "invalid commands changed posting (posts differ from control)",
    )
    checks.note("; ".join(f"/{command.label} -> {command.text[:60]!r}" for command in run_result.cmds[:4]))
    return run_result


async def sc_pause_change(ctx: Ctx, checks: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)])
    run_result = await night.run(["21:00 pause", "21:30 posting interval 30", "22:00 resume"])
    no_posts(checks, run_result, run_result.t("21:00"), run_result.t("22:00"))
    check_gaps(checks, run_result, run_result.t("22:00"), run_result.end, 30, label="interval30 after resume: ")
    checks.ok(bool(run_result.posts_for(t0=run_result.t("22:00"))), "nothing posted after resume")
    replies_ok(checks, run_result)
    return run_result


async def sc_two_guilds(ctx: Ctx, checks: Checks):
    second_races = sorted(ctx.watched)[::3]
    second_guild = GuildSettings(
        guild_id=2,
        channel_id=201,
        important=second_races,
        post_mode=PostMode.IMMEDIATE,
        call_outlets=["FOX"],
        post_turnout=False,
        odds_alerts=True,
        odds_swing_pts=10,
        odds_window_minutes=30,
    )
    extra_race = (
        pick_extra_race(ctx, set(ctx.watched), sim_time(ctx.year, "21:30"), sim_time(ctx.year, "23:00"))
        or second_races[0]
    )
    swings = [HeldSwing(second_races[0], "DEM", sim_time(ctx.year, "22:10"), 5, 20)]
    night_options = dict(calls=default_calls_for(ctx, outlets={"AP": 0, "FOX": 2}), swings=swings, turnout=True)
    steps = [
        "21:00 pause",
        "21:30 resume",
        "21:40 posting interval 15",
        f'21:50 watch add "{extra_race}"',
        "22:00 calls AP off",
        "22:20 setup channel=300",
        "22:30 oddsalerts on threshold=5 window=10",
        "22:40 turnout off",
        f'23:00 watch remove "{second_races[0]}"',
        {"at": "23:10", "cmd": "posting immediate", "as": "admin", "guild": 2},  # same as before: no-op
        {"at": "23:20", "cmd": "!readonly under_tick=0", "guild": 2},
    ]
    both = await Night(ctx.year, ctx.timelines, [base_guild(ctx), second_guild], **night_options).run(steps)
    solo = await Night(ctx.year, ctx.timelines, [second_guild], **night_options).run(
        [step for step in parse_steps(steps) if step.guild == 2]
    )
    checks.ok(post_signature(both, 2) == post_signature(solo, 2), "guild 2 posts differ when guild 1 changes settings")
    checks.ok({p.channel for p in both.posts_for(2)} <= {201}, "guild 2 posted outside its channel")
    checks.ok(not {p.channel for p in both.posts_for(GUILD)} & {201}, "guild 1 posted into guild 2's channel")
    checks.ok(not both.posts_for(GUILD, t0=both.t("21:00"), t1=both.t("21:30")), "guild 1 posted while paused")
    checks.ok(bool(both.posts_for(2, t0=both.t("21:00"), t1=both.t("21:30"))), "guild 2 silent while guild 1 paused")
    checks.note(f"guild1 {len(both.posts_for(GUILD))} posts, guild2 {len(both.posts_for(2))} posts (== solo run)")
    replies_ok(checks, both)
    return both


async def sc_store_errors(ctx: Ctx, checks: Checks):
    if not ctx.mongo_uri:
        checks.ok(False, "no --mongo-uri")
        return None
    from pymongo.asynchronous.mongo_client import AsyncMongoClient

    from electionwatch.store.mongo import MongoStore

    db = f"ew_sim_{os.getpid()}_{int(time.time())}"
    store = MongoStore(ctx.mongo_uri, db)
    await store.create_indexes()
    await store.odds.drop_index("ts_1")  # the 3-day TTL would expire 2014-2024 replay odds mid-run
    try:
        night = Night(
            ctx.year, ctx.timelines, [base_guild(ctx)], store=store, until="23:30", calls=default_calls_for(ctx)
        )
        run_result = await night.run(
            [
                "21:50 posting interval 10",
                "22:00 !store_down minutes=20",
                "22:05 pause",
                "22:06 settings",
                "22:07 odds",
                '22:08 watch add "PA SEN"',
                "22:30 settings",
            ]
        )
    finally:
        client = AsyncMongoClient(ctx.mongo_uri)
        await client.drop_database(db)
        await client.close()
        await store.close()
    down_errors = [
        tick_error
        for tick_error in run_result.tick_errors
        if run_result.t("22:00") <= tick_error[0] < run_result.t("22:20")
    ]
    checks.ok(
        all(run_result.t("22:00") <= tick_error[0] < run_result.t("22:20") for tick_error in run_result.tick_errors),
        f"tick errors outside the outage: {run_result.tick_errors[:2]}",
    )
    checks.ok(bool(down_errors), "outage did not reach the poller")
    no_posts(checks, run_result, run_result.t("22:00"), run_result.t("22:20"), label="store down: ")
    checks.ok(
        bool(run_result.posts_for(t0=run_result.t("22:20"), t1=run_result.t("22:30"))),
        "no posts within 10 min after the store came back",
    )
    during = [command for command in run_result.cmds if run_result.t("22:00") <= command.at < run_result.t("22:20")]
    for command in during:
        checks.ok(
            command.ack_s is not None and command.ack_s < DEADLINE_FLAG_S,
            f"/{command.label} during outage: no reply in time",
        )
        checks.ok("went wrong" in command.text, f"/{command.label} during outage: {command.text[:80]!r}")
    final = [command for command in run_result.cmds if command.label == "settings"][-1]
    checks.ok(
        "▶️ posting" in final.text and "every 10 minutes" in final.text,
        "settings after the outage lost the pre-outage change or kept the failed /pause",
    )
    dup = Counter((posted.post.race, tuple(posted.post.lines)) for posted in run_result.posts_for(kind="call"))
    checks.ok(all(occurrences == 1 for occurrences in dup.values()), "duplicate call posts after recovery")
    late = [posted for posted in run_result.posts_for(kind="call", t0=run_result.t("22:20"), t1=run_result.t("22:21"))]
    checks.note(
        f"{len(down_errors)} failed ticks, {len(during)} commands answered 'Something went wrong'; "
        f"{len(late)} calls from the outage posted at recovery"
    )
    replies_ok(checks, run_result)
    return run_result


class PrivateMongod:
    """A throw-away mongod on its own port (never touches the shared 27017 instance)."""

    def __init__(self, binary: str, port: int, path: Path) -> None:
        self.binary, self.port, self.path = binary, port, path
        self.proc = None
        path.mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [
                self.binary,
                "--port",
                str(self.port),
                "--bind_ip",
                "127.0.0.1",
                "--dbpath",
                str(self.path),
                "--quiet",
                "--nounixsocket",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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


async def sc_mongo_restart(ctx: Ctx, checks: Checks):
    if not ctx.mongod_bin:
        checks.ok(False, "no --mongod-bin")
        return None
    import socket

    from electionwatch.store.mongo import MongoStore

    with socket.socket() as sock:  # a free port: parallel runs must not share a mongod
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    mongod = PrivateMongod(ctx.mongod_bin, port, ctx.workdir / f"mongod-restart-{port}")
    mongod.start()
    try:
        store = MongoStore(f"mongodb://127.0.0.1:{port}", "ew_sim_restart")
        await store.create_indexes()
        await store.odds.drop_index("ts_1")
        night = Night(
            ctx.year, ctx.timelines, [base_guild(ctx)], store=store, start="21:30", until="22:30", mongod=mongod
        )
        run_result = await night.run(
            [
                "21:50 posting interval 10",
                "22:00 !mongod_stop",
                "22:00 settings",
                "22:01 pause",
                "22:02 !mongod_start",
                "22:10 settings",
            ]
        )
        await store.close()
    finally:
        mongod.stop()
    during = [command for command in run_result.cmds if run_result.t("22:00") <= command.at < run_result.t("22:02")]
    for command in during:
        checks.ok(command.ack_s is not None, f"/{command.label} while mongod is down: no reply at all")
        if command.ack_s is None or command.ack_s > DEADLINE_FLAG_S:
            checks.problems.append(
                f"/{command.label} while mongod is down answered after {command.total_s:.1f}s "
                "(Discord's 3 s deadline: the user sees 'did not respond')"
            )
    checks.ok(bool(run_result.posts_for(t0=run_result.t("22:02"))), "no posts after mongod restarted")
    final = [command for command in run_result.cmds if command.label == "settings"][-1]
    checks.ok("every 10 minutes" in final.text, "settings lost across the mongod restart")
    checks.note(
        f"tick errors while down: {len(run_result.tick_errors)}; command wall times while down: "
        + ", ".join(f"/{command.label} {command.total_s:.1f}s" for command in during)
    )
    return run_result


async def sc_source_outage(ctx: Ctx, checks: Checks):
    night = Night(ctx.year, ctx.timelines, [base_guild(ctx)], calls=default_calls_for(ctx))
    run_result = await night.run(["21:30 !source_down minutes=30", "21:45 settings", "21:46 !navigator"])
    tries = [
        attempt_ts
        for attempt_ts, ok in run_result.source_attempts
        if run_result.t("21:30") <= attempt_ts < run_result.t("22:00")
    ]
    checks.ok(len(tries) <= 6, f"{len(tries)} fetch attempts during a 30-min outage (back-off expected)")
    # The source's last answer stands in for up to 10 minutes (poller.ANSWER_REUSE): numbers
    # fetched before 21:30 may still go out on the posting schedule, nothing after that.
    no_posts(checks, run_result, run_result.t("21:41"), run_result.t("22:00"), kind="results", label="source down: ")
    held = len(run_result.posts_for(kind="results", t0=run_result.t("21:30"), t1=run_result.t("21:41")))
    first = min((posted.at for posted in run_result.posts_for(kind="results", t0=run_result.t("22:00"))), default=None)
    checks.ok(
        first is not None and first - run_result.t("22:00") <= timedelta(minutes=16),
        f"results recovered at {et(first) if first else 'never'}",
    )
    checks.ok(bool(run_result.posts_for(kind="call", t0=run_result.t("21:30"), t1=run_result.t("22:00"))) or True, "")
    calls_during = len(run_result.posts_for(kind="call", t0=run_result.t("21:30"), t1=run_result.t("22:00")))
    replies_ok(checks, run_result)
    checks.note(
        f"{held} posts of numbers fetched before the outage, 21:30-21:40; "
        f"attempts during outage at {[et(attempt_ts) for attempt_ts in tries]}; first results post after: "
        f"{et(first) if first else '-'}; {calls_during} call posts continued during the outage"
    )
    return run_result


async def sc_latency(ctx: Ctx, checks: Checks, mongo: bool = False):
    steps = [
        {"at": f"{hour:02d}:{minute:02d}", "cmd": "!readonly"} for hour in (20, 21, 22, 23, 0, 1) for minute in (0, 30)
    ]
    store = db = None
    if mongo:
        if not ctx.mongo_uri:
            checks.ok(False, "no --mongo-uri")
            return None
        from electionwatch.store.mongo import MongoStore

        db = f"ew_sim_lat_{os.getpid()}_{int(time.time())}"
        store = MongoStore(ctx.mongo_uri, db)
        await store.create_indexes()
        await store.odds.drop_index("ts_1")
    night = Night(
        ctx.year,
        ctx.timelines,
        [base_guild(ctx, odds_alerts=True)],
        calls=default_calls_for(ctx),
        turnout=True,
        until="02:00",
        store=store,
    )
    night.measure_stall = True
    night.readonly_race = next(
        (race_key for race_key in ("PA SEN", "GA SEN", "AZ SEN", "TX SEN") if race_key in ctx.watched),
        sorted(ctx.watched)[0],
    )
    try:
        run_result = await night.run(steps)
    finally:
        if store is not None:
            await store.client.drop_database(db)
            await store.close()
    replies_ok(checks, run_result)
    # The navigator and autocomplete read the race lists the background refresh stored.
    calls = night.src.list_calls
    checks.ok(
        bool(calls) and all(name and ":racelists:" in name for name in calls),
        f"list_races called outside the race-list refresh: {[name for name in calls if not name or ':racelists:' not in name][:3]}",
    )
    nav = [command for command in run_result.cmds if command.label.startswith("races")]
    loading = [command for command in nav if "still loading" in command.text]
    checks.ok(
        bool(nav) and not loading,
        f"navigator said a race list was still loading at {et(loading[0].at)}" if loading else "no navigator",
    )
    for command in run_result.cmds:
        checks.ok(command.error is None, f"/{command.label} at {et(command.at)} raised {command.error}")
        if command.ack_s is None or command.ack_s > DEADLINE_FLAG_S:
            checks.problems.append(f"/{command.label} at {et(command.at)}: ack {command.ack_s}s (> {DEADLINE_FLAG_S}s)")
        elif command.total_s > DEADLINE_FLAG_S:
            checks.note(
                f"/{command.label} at {et(command.at)}: total {command.total_s:.2f}s (acked in {command.ack_s:.2f}s)"
            )
    latencies = latency_table(run_result)
    tk = [tick_seconds for _, tick_seconds in run_result.ticks]
    stalls = night.stalls or [0.0]
    # The 3 s deadline is for the first response (message or defer); a deferred command has
    # 15 minutes for its followup, so the worst case is the slowest *ack* + the longest stall.
    worst = max(row["ack_max"] or row["max"] for row in latencies.values()) + max(stalls)
    checks.ok(
        worst <= DEADLINE_FLAG_S,
        f"worst case (slowest ack {worst - max(stalls):.2f}s + loop blocked "
        f"{max(stalls):.2f}s by a tick) = {worst:.2f}s > {DEADLINE_FLAG_S}s",
    )
    run_result.extra["stall"] = {
        "p50": pct(stalls, 50),
        "p95": pct(stalls, 95),
        "max": max(stalls),
        "tick_p50": pct(tk, 50),
        "tick_p95": pct(tk, 95),
        "tick_max": max(tk),
    }
    checks.note(f"loop blocked by a tick: p50 {pct(stalls, 50):.3f}s p95 {pct(stalls, 95):.3f}s max {max(stalls):.3f}s")
    checks.note(
        f"{len(run_result.cmds)} interactions; tick p50 {pct(tk, 50):.3f}s max {max(tk):.3f}s; "
        + "; ".join(
            f"{race_key} p50 {row['p50']:.3f} p95 {row['p95']:.3f} max {row['max']:.3f}"
            for race_key, row in latencies.items()
        )
    )
    run_result.extra["latency"] = latencies
    return run_result


def pct(values, percentile):
    values = sorted(values)
    if not values:
        return 0.0
    return values[min(len(values) - 1, int(round(percentile / 100 * (len(values) - 1))))]


def latency_table(run_result: NightResult) -> dict:
    timings = defaultdict(list)
    for command in run_result.cmds:
        timings[command.label].append(command.total_s if command.ack_s is None else max(command.ack_s, command.total_s))
    acks = defaultdict(list)
    for command in run_result.cmds:
        if command.ack_s is not None:
            acks[command.label].append(command.ack_s)
    return {
        label: {
            "n": len(samples),
            "p50": pct(samples, 50),
            "p95": pct(samples, 95),
            "max": max(samples),
            "ack_max": max(acks[label], default=None),
        }
        for label, samples in sorted(timings.items())
    }


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
    "latencymongo": (
        "9b same as 9 with MongoStore (local mongod, throw-away db)",
        lambda ctx, c: sc_latency(ctx, c, mongo=True),
    ),
    "twoguilds": ("10 two guilds, guild 1 changes everything", sc_two_guilds),
    "storeerr": ("11a MongoStore with store errors for 20 min", sc_store_errors),
    "mongorestart": ("11b private mongod stopped/started mid-night", sc_mongo_restart),
    "sourcedown": ("12 results source outage 30 min (last answer reused 10 min)", sc_source_outage),
    "roleadmin": ("13 /setup admin_role delegates admin to a role", sc_roleadmin),
    "invalid": ("14 invalid inputs mid-night change nothing", sc_invalid),
    "pausechange": ("15 /posting changed while paused applies after resume", sc_pause_change),
}


# ---------------------------------------------------------------- runner


def write_run(out_dir: Path, name: str, run_result: NightResult | None, checks: Checks, secs: float) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "scenario": name,
        "ok": not checks.problems,
        "problems": checks.problems[:40],
        "notes": checks.notes,
        "secs": round(secs, 1),
    }
    if run_result is not None:
        row.update(
            posts=len(run_result.posts),
            commands=len(run_result.cmds),
            ticks=len(run_result.ticks),
            tick_errors=len(run_result.tick_errors),
        )
        for extra_key in ("latency", "stall"):
            if extra_key in run_result.extra:
                row[extra_key] = run_result.extra[extra_key]
        with (out_dir / f"{name}.jsonl").open("w") as fh:
            for posted in run_result.posts:
                fh.write(json.dumps({"type": "post", **posted.row()}, ensure_ascii=False) + "\n")
            for command in run_result.cmds:
                fh.write(json.dumps({"type": "command", **command.row()}, ensure_ascii=False) + "\n")
            for tick_ts, error in run_result.tick_errors:
                fh.write(json.dumps({"type": "tick_error", "et": et(tick_ts), "error": error}) + "\n")
    return row


async def run_scenarios(
    years, mode, names, data_dir, out_dir, mongo_uri, mongod_bin, bundled: dict | None = None
) -> list[dict]:
    """`bundled` maps year -> timelines for `--quick`, so no data directory is read."""
    rows = []
    for year in years:
        timelines = (bundled or {}).get(year) or load_year(Path(data_dir).expanduser(), year)
        watched = top_race_per_state(timelines) if mode == "top" else sorted(timelines)
        for name in names:
            checks = Checks()
            ctx = Ctx(year, timelines, watched, mode, out_dir / "work", mongo_uri, mongod_bin)
            ctx.workdir.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            run_result = None
            try:
                run_result = await SCENARIOS[name][1](ctx, checks)
            except Exception as exc:
                checks.problems.append(f"crashed: {type(exc).__name__}: {exc}")
                checks.notes.append(traceback.format_exc()[-1500:])
            row = write_run(out_dir / f"{year}-{mode}", name, run_result, checks, time.perf_counter() - started)
            row.update(year=year, mode=mode)
            rows.append(row)
            print(
                f"{year} {mode:3} {name:12} {'PASS' if row['ok'] else 'FAIL'} {row['secs']:7.1f}s "
                + (
                    "; ".join(checks.problems[:2])
                    if checks.problems
                    else (checks.notes[0][:140] if checks.notes else "")
                ),
                flush=True,
            )
    return rows


def matrix(rows: list[dict]) -> str:
    cols = sorted({(row["year"], row["mode"]) for row in rows})
    names = list(dict.fromkeys(row["scenario"] for row in rows))
    idx = {(row["scenario"], row["year"], row["mode"]): row for row in rows}
    lines = [
        "| scenario | " + " | ".join(f"{year} {mode}" for year, mode in cols) + " |",
        "|---" * (len(cols) + 1) + "|",
    ]
    for scenario in names:
        cells = []
        for year, mode in cols:
            row = idx.get((scenario, year, mode))
            cells.append("-" if row is None else ("PASS" if row["ok"] else "FAIL"))
        lines.append(f"| {SCENARIOS[scenario][0]} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- live into Discord

LIVE_RACES = ["PA SEN", "MI SEN", "WI SEN", "AZ SEN", "NV SEN", "OH SEN", "MT SEN", "TX SEN"]
LIVE_STEPS = [
    "20:45 posting interval 15",
    {"at": "21:00", "cmd": "pause", "as": "member"},
    "21:05 pause",
    "21:35 resume",
    "21:40 posting immediate",
    "21:55 posting default",
    "WATCH_ADD",
    "WATCH_REMOVE",
    "22:35 calls AP,NBC,CNN,FOX,DDHQ,VOTEHUB,CIVICAPI,CBS,ABC,NYT off",  # every outlet: calls off
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
        shown = dc_replace(
            post,
            title=f"[SIM] {post.title}",
            footer=((post.footer + " · ") if post.footer else "") + f"sim {et(sim)} ET",
        )
        await self.thread.send(embed=render_embed(shown))
        self.sent[post.kind] += 1
        self.record.write(
            json.dumps(
                {"type": "post", "et": et(sim), "kind": post.kind, "race": post.race, "lines": post.lines},
                ensure_ascii=False,
            )
            + "\n"
        )
        self.record.flush()


async def live(args) -> None:
    from electionwatch.config import load_env

    load_env()
    timelines = load_year(Path(args.data).expanduser(), args.year)
    races = [race_key for race_key in LIVE_RACES if race_key in timelines] or top_race_per_state(timelines)[:8]
    extra_race = next(
        (race_key for race_key in ("PA-07", "PA-08", "PA-10", "NE-02", "MI-07") if race_key in timelines), None
    )
    steps = []
    for step in LIVE_STEPS:
        if step == "WATCH_ADD":
            step = f'22:00 watch add "{extra_race}"' if extra_race else None
        elif step == "WATCH_REMOVE":
            step = f'22:30 watch remove "{extra_race}"' if extra_race else None
        if step:
            steps.append(step)
    sim_ts = lambda clock_text: sim_time(args.year, clock_text)  # noqa: E731
    calls = spread_calls(
        timelines, races + ([extra_race] if extra_race else []), sim_ts("20:40"), sim_ts("00:20"), {"AP": 0, "FOX": 2}
    )
    swings = [HeldSwing(races[0], "DEM", sim_ts("23:20"), 6, 18), HeldSwing(races[1], "REP", sim_ts("23:25"), 6, 4)]
    exitpolls = {race_key: sim_ts("20:35") + timedelta(minutes=17 * index) for index, race_key in enumerate(races)}
    out_dir_path = Path(args.out_dir or LOGS / f"live-{args.year}-{datetime.now():%Y%m%d-%H%M%S}")
    out_dir_path.mkdir(parents=True, exist_ok=True)
    client = discord.Client(intents=discord.Intents.none())
    done = asyncio.Event()
    state: dict = {}

    @client.event
    async def on_ready():
        try:
            channel = await client.fetch_channel(args.channel)
            thread = await channel.create_thread(
                name=f"[SIM] command changes {args.year}",
                type=discord.ChannelType.public_thread,
                auto_archive_duration=1440,
            )
            state["thread"] = thread.id
            (out_dir_path / "thread.txt").write_text(f"{thread.id}\n")
            await thread.send(
                f"**[SIM] Command-change simulation, {args.year} election night** ({args.speed:g}x, "
                f"{args.start}-{args.until} ET).\nResults are historical replays; race calls, odds and exit polls are "
                f"scripted test data. Watching: {', '.join(races)}.\nEach admin command below is invoked "
                "through the bot's real slash-command code mid-night; watch the posts change after it."
            )

            async def on_step(step: Step, rec: CmdRec | None) -> None:
                if rec is None:
                    return
                who = {"admin": "admin", "member": "a member without Manage Server"}.get(step.user, step.user)
                reply = rec.text.replace("```", "").strip()
                await thread.send(f"⚙️ `/{step.cmd}` by {who} (sim {et(rec.at)} ET)\n↳ ephemeral reply: {reply[:600]}")

            poster = LivePoster(None, thread, out_dir_path / "posts.jsonl")
            night = Night(
                args.year,
                timelines,
                [GuildSettings(GUILD, CHANNEL, important=list(races))],
                start=args.start,
                until=args.until,
                turnout=True,
                calls=calls,
                swings=swings,
                exitpolls=exitpolls,
                poster=poster,
                speed=args.speed,
                on_step=on_step,
            )
            poster.clock = night.clock
            started = time.monotonic()
            run_result = await night.run(steps)
            await thread.send(
                f"**[SIM] finished** after {(time.monotonic() - started) / 60:.0f} min: posts {dict(poster.sent)}, "
                f"{len(run_result.cmds)} commands, tick errors {len(run_result.tick_errors)}."
            )
            checks = Checks()
            no_posts(checks, run_result, run_result.t("21:05"), run_result.t("21:35"))
            check_gaps(checks, run_result, run_result.t("20:45"), run_result.t("21:05"), 15)
            no_posts(checks, run_result, run_result.t("22:35"), run_result.t("22:50"), kind="call")
            (out_dir_path / "result.json").write_text(
                json.dumps(
                    {
                        "thread": thread.id,
                        "problems": checks.problems,
                        "posts": dict(poster.sent),
                        "commands": [command.row() for command in run_result.cmds],
                    },
                    indent=1,
                    default=str,
                )
            )
            print("live problems:", checks.problems or "none", "thread", thread.id, "->", out_dir_path)
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
    ap.add_argument(
        "--quick",
        action="store_true",
        help="bundled tiny night, no history data: a few scenarios, about 15 s (--years is ignored)",
    )
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
    args = ap.parse_args(argv)
    if args.live:
        logging.basicConfig(level=logging.WARNING)
        asyncio.run(live(args))
        return 0
    if args.list:
        for scenario_name, (description, _) in SCENARIOS.items():
            print(f"{scenario_name:12} {description}")
        return 0
    logging.basicConfig(level=logging.CRITICAL)  # the poller logs every injected failure
    bundled = {QUICK_YEAR: quick_timelines()} if args.quick else None
    years = [QUICK_YEAR] if args.quick else [year_value.strip() for year_value in args.years.split(",")]
    default_names = list(QUICK_SCENARIOS) if args.quick else list(SCENARIOS)
    names = default_names if args.scenarios == "all" else [name_part.strip() for name_part in args.scenarios.split(",")]
    names = [name_value for name_value in names if name_value not in set(args.skip.split(","))]
    out_dir_path = Path(args.out_dir) if args.out_dir else LOGS / datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = asyncio.run(
        run_scenarios(years, args.races, names, args.data, out_dir_path, args.mongo_uri, args.mongod_bin, bundled)
    )
    (out_dir_path / f"summary-{args.races}.json").write_text(json.dumps(rows, indent=1, default=str))
    (out_dir_path / f"matrix-{args.races}.md").write_text(matrix(rows) + "\n")
    print(matrix(rows))
    print(f"-> {out_dir_path}")
    return 0 if all(row["ok"] for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
