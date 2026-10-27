"""Run a recorded/synthetic election night through the real Poller, minute by minute.

    python -m replay.run --year 2024 --races "TX SEN,PA SEN" --speed max
    python -m replay.run --year 2020 --data ~/Downloads/election-test-data --mode interval --interval 15

Uses MemoryStore + RecordingPoster (no Discord, no Mongo, no network), a ReplaySource for
results, a scripted race call per race and synthetic odds with one forced swing per race.
Writes replay/out/<run>.md: every post with its simulated time, then a summary that checks
posts per race, the interval rule and duplicates. Exit code 1 if a check fails.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from electionwatch.poster import Post, RecordingPoster
from electionwatch.settings import DEFAULT_INTERVAL_MIN, GuildSettings, PostMode
from electionwatch.store.memory import MemoryStore

from replay.clock import FakeClock
from replay.scripted import (ScriptedCallSource, ScriptedOddsSource, default_calls,
                             default_swings)
from replay.source import ReplaySource
from replay.timeline import load_year

DEFAULT_DATA = Path.home() / "Downloads" / "election-test-data"
OUT = Path(__file__).resolve().parent / "out"
GUILD, CHANNEL = 1, 100


@dataclass
class Report:
    posts: list[tuple[datetime, Post]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    per_race: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    ticks: int = 0
    start: datetime | None = None
    end: datetime | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def check(posts: list[tuple[datetime, Post]], gs: GuildSettings) -> Report:
    rep = Report(posts=posts)
    last_results: dict[str, tuple[datetime, list[str]]] = {}
    seen_once: set[tuple] = set()
    minutes = (0 if gs.post_mode is PostMode.IMMEDIATE else
               DEFAULT_INTERVAL_MIN if gs.post_mode is PostMode.DEFAULT else max(1, gs.interval_minutes))
    for at, p in posts:
        rep.per_race[p.race][p.kind] += 1
        if p.kind == "results":
            prev = last_results.get(p.race)
            if prev:
                gap = (at - prev[0]).total_seconds() / 60
                if gap < minutes:
                    rep.problems.append(f"{at:%H:%M} {p.race}: results {gap:.0f} min after the last "
                                        f"one (rule: >= {minutes})")
                if prev[1] == p.lines:
                    rep.problems.append(f"{at:%H:%M} {p.race}: results post identical to the previous one")
            last_results[p.race] = (at, p.lines)
        elif p.kind in ("call", "exitpoll"):
            ident = (p.kind, p.race, tuple(p.lines))
            if ident in seen_once:
                rep.problems.append(f"{at:%H:%M} {p.race}: duplicate {p.kind} post")
            seen_once.add(ident)
    return rep


def render(rep: Report, title: str, gs: GuildSettings) -> str:
    out = [f"# Replay: {title}", "",
           f"Simulated {rep.start:%Y-%m-%d %H:%M} to {rep.end:%Y-%m-%d %H:%M} UTC, {rep.ticks} ticks; "
           f"post mode {gs.post_mode.value}"
           + (f" ({gs.interval_minutes} min)" if gs.post_mode is PostMode.INTERVAL else ""), "",
           "## Posts", ""]
    for at, p in rep.posts:
        out.append(f"### {at:%H:%M} UTC · {p.kind} · {p.race} · {p.title}")
        out += [f"    {ln}" for ln in p.lines]
        if p.footer:
            out.append(f"    _{p.footer}_")
        out.append("")
    out += ["## Summary", "", "| race | " + " | ".join(KINDS) + " |", "|---" * (len(KINDS) + 1) + "|"]
    for race, c in sorted(rep.per_race.items()):
        out.append(f"| {race} | " + " | ".join(str(c.get(k, 0)) for k in KINDS) + " |")
    out += ["", "Interval rule respected, no duplicates: " + ("yes" if rep.ok else "NO"), ""]
    out += [f"- {p}" for p in rep.problems]
    return "\n".join(out) + "\n"


KINDS = ("results", "call", "turnout", "exitpoll", "odds_swing")


async def replay(timelines, gs: GuildSettings, speed: float | None = None, max_hours: float = 12,
                 swings: bool = True, calls: bool = True, poller_cls=None) -> Report:
    if poller_cls is None:
        from electionwatch.poller import Poller as poller_cls  # noqa: N813
    start_ts = min(v[0].ts for v in timelines.values())
    start = start_ts.replace(second=0, microsecond=0) - timedelta(minutes=1)
    clock = FakeClock(start)
    src = ReplaySource(timelines, clock)
    end = min(src.span()[1] + timedelta(minutes=30), start + timedelta(hours=max_hours))
    sources = {
        "results": [src],
        "calls": [ScriptedCallSource(default_calls(src), clock)] if calls else [],
        "odds": [ScriptedOddsSource(src, clock, default_swings(src) if swings else [])],
        "exitpolls": [],
    }
    store = MemoryStore()
    await store.save_guild(gs)
    poster = RecordingPoster()
    posts: list[tuple[datetime, Post]] = []
    ticks = 0
    async with httpx.AsyncClient() as http:
        poller = poller_cls(store, sources, poster, clock, http)
        while clock() <= end:
            n = len(poster.sent)
            await poller.tick()
            ticks += 1
            posts += [(clock(), p) for _, _, p in poster.sent[n:]]
            if speed:
                time.sleep(60 / speed)
            clock.advance(1)
    rep = check(posts, gs)
    rep.ticks, rep.start, rep.end = ticks, start, end
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay an election night through the Poller.")
    ap.add_argument("--year", required=True, help="2018, 2020, 2022, 2024 (timelines/<year>/)")
    ap.add_argument("--races", default="", help='comma-separated race keys, e.g. "TX SEN,PA SEN" (default all)')
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--speed", default="max", help="'max' or a multiplier (60 = one sim minute per second)")
    ap.add_argument("--mode", choices=[m.value for m in PostMode], default="default")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN)
    ap.add_argument("--max-hours", type=float, default=12)
    ap.add_argument("--no-swing", action="store_true")
    ap.add_argument("--no-calls", action="store_true")
    ap.add_argument("--out", default=None, help="output .md (default replay/out/<year>-<races>.md)")
    a = ap.parse_args(argv)
    races = [r.strip() for r in a.races.split(",") if r.strip()] or None
    timelines = load_year(Path(a.data).expanduser(), a.year, races)
    if not timelines:
        print(f"no timelines under {a.data}/timelines/{a.year}", file=sys.stderr)
        return 2
    try:
        from electionwatch.poller import Poller
    except ImportError as e:
        print(f"electionwatch.poller is not available yet ({e}); merge pkg/core first", file=sys.stderr)
        return 2
    gs = GuildSettings(guild_id=GUILD, channel_id=CHANNEL, important=sorted(timelines),
                       post_mode=PostMode(a.mode), interval_minutes=a.interval, odds_alerts=True)
    speed = None if a.speed == "max" else float(a.speed)
    rep = asyncio.run(replay(timelines, gs, speed, a.max_hours, not a.no_swing, not a.no_calls, Poller))
    name = f"{a.year}-" + ("-".join(k.replace(' ', '') for k in sorted(timelines)) if races else "all")
    out = Path(a.out) if a.out else OUT / f"{name}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rep, f"{a.year} {', '.join(sorted(timelines))}", gs))
    print(f"{len(rep.posts)} posts in {rep.ticks} simulated minutes -> {out}")
    for p in rep.problems:
        print("  PROBLEM", p)
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
