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
from replay.scripted import ScriptedCallSource, ScriptedOddsSource, default_calls, default_swings
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
    report = Report(posts=posts)
    last_results: dict[str, tuple[datetime, tuple]] = {}
    seen_once: set[tuple] = set()
    minutes = (
        0
        if gs.post_mode is PostMode.IMMEDIATE
        else DEFAULT_INTERVAL_MIN
        if gs.post_mode is PostMode.DEFAULT
        else max(1, gs.interval_minutes)
    )
    for at, post in posts:
        report.per_race[post.race][post.kind] += 1
        if post.kind == "results":
            previous = last_results.get(post.race)
            if previous:
                gap = (at - previous[0]).total_seconds() / 60
                if gap < minutes:
                    report.problems.append(
                        f"{at:%H:%M} {post.race}: results {gap:.0f} min after the last one (rule: >= {minutes})"
                    )
                if previous[1] == (post.lines, post.footer, post.fields):
                    report.problems.append(f"{at:%H:%M} {post.race}: results post identical to the previous one")
            last_results[post.race] = (at, (post.lines, post.footer, post.fields))
        elif post.kind in ("call", "exitpoll"):
            post_id = (post.kind, post.race, tuple(post.lines))
            if post_id in seen_once:
                report.problems.append(f"{at:%H:%M} {post.race}: duplicate {post.kind} post")
            seen_once.add(post_id)
    return report


def render(report: Report, title: str, gs: GuildSettings) -> str:
    out = [
        f"# Replay: {title}",
        "",
        f"Simulated {report.start:%Y-%m-%d %H:%M} to {report.end:%Y-%m-%d %H:%M} UTC, {report.ticks} ticks; "
        f"post mode {gs.post_mode.value}"
        + (f" ({gs.interval_minutes} min)" if gs.post_mode is PostMode.INTERVAL else ""),
        "",
        "## Posts",
        "",
    ]
    for at, post in report.posts:
        out.append(f"### {at:%H:%M} UTC · {post.kind} · {post.race} · {post.title}")
        out += [f"    {line}" for line in post.lines]
        if post.footer:
            out.append(f"    _{post.footer}_")
        out.append("")
    out += ["## Summary", "", "| race | " + " | ".join(KINDS) + " |", "|---" * (len(KINDS) + 1) + "|"]
    for race, counts in sorted(report.per_race.items()):
        out.append(f"| {race} | " + " | ".join(str(counts.get(kind, 0)) for kind in KINDS) + " |")
    out += ["", "Interval rule respected, no duplicates: " + ("yes" if report.ok else "NO"), ""]
    out += [f"- {problem}" for problem in report.problems]
    return "\n".join(out) + "\n"


KINDS = ("results", "call", "turnout", "exitpoll", "odds_swing")


async def replay(
    timelines,
    gs: GuildSettings,
    speed: float | None = None,
    max_hours: float = 36,
    swings: bool = True,
    calls: bool = True,
    poller_cls=None,
) -> Report:
    if poller_cls is None:
        from electionwatch.poller import Poller as poller_cls  # noqa: N813
    start_ts = min(series[0].ts for series in timelines.values())
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
            sent_count = len(poster.sent)
            await poller.tick()
            ticks += 1
            posts += [(clock(), post) for _, _, post in poster.sent[sent_count:]]
            if speed:
                time.sleep(60 / speed)
            clock.advance(1)
            # fast-forward quiet stretches (Dixville Notch at midnight, then nothing for hours)
            next_ts = src.next_change(clock())
            if next_ts is not None and next_ts - clock() > timedelta(minutes=90):
                clock.advance((next_ts - clock() - timedelta(minutes=1)).total_seconds() / 60)
    report = check(posts, gs)
    report.ticks, report.start, report.end = ticks, start, end
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay an election night through the Poller.")
    ap.add_argument("--year", required=True, help="2018, 2020, 2022, 2024 (timelines/<year>/)")
    ap.add_argument("--races", default="", help='comma-separated race keys, e.g. "TX SEN,PA SEN" (default all)')
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--speed", default="max", help="'max' or a multiplier (60 = one sim minute per second)")
    ap.add_argument("--mode", choices=[m.value for m in PostMode], default="default")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN)
    ap.add_argument("--max-hours", type=float, default=36)
    ap.add_argument("--no-swing", action="store_true")
    ap.add_argument("--no-calls", action="store_true")
    ap.add_argument("--out", default=None, help="output .md (default replay/out/<year>-<races>.md)")
    args = ap.parse_args(argv)
    races = [race.strip() for race in args.races.split(",") if race.strip()] or None
    timelines = load_year(Path(args.data).expanduser(), args.year, races)
    if not timelines:
        print(f"no timelines under {args.data}/timelines/{args.year}", file=sys.stderr)
        return 2
    try:
        from electionwatch.poller import Poller
    except ImportError as exc:
        print(f"electionwatch.poller is not available yet ({exc}); merge pkg/core first", file=sys.stderr)
        return 2
    gs = GuildSettings(
        guild_id=GUILD,
        channel_id=CHANNEL,
        important=sorted(timelines),
        post_mode=PostMode(args.mode),
        interval_minutes=args.interval,
        odds_alerts=True,
    )
    speed = None if args.speed == "max" else float(args.speed)
    report = asyncio.run(replay(timelines, gs, speed, args.max_hours, not args.no_swing, not args.no_calls, Poller))
    name = f"{args.year}-" + ("-".join(race_key.replace(" ", "") for race_key in sorted(timelines)) if races else "all")
    out_path = Path(args.out) if args.out else OUT / f"{name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(report, f"{args.year} {', '.join(sorted(timelines))}", gs))
    print(f"{len(report.posts)} posts in {report.ticks} simulated minutes -> {out_path}")
    for problem in report.problems:
        print("  PROBLEM", problem)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
