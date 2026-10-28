"""Replay a past election night into a real Discord channel, sped up.

    python -m replay.discord_live --year 2022 --races "$(cat races.txt)" \
        --post "PA SEN,PA-07,senate"   (or "top-per-state") --speed 10 --guild 123 --channel 456 --report full.html

Every race in --races is tracked (as one hidden guild, recorded for the report); only the
--post subset is posted to the channel (the word "senate" adds every US Senate race).
Posts are titled "[SIM <year>]" so nobody mistakes them for live results. The first message
links/attaches the full-run report. Posting is sequential: when Discord rate-limits, the
simulated clock waits instead of dropping posts, so busy stretches run slower than --speed.

Needs DISCORD_TOKEN (env or EW_ENV_FILES). No Mongo: uses MemoryStore.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import discord
import httpx

from electionwatch.bot.poster import render_embed
from electionwatch.config import load_env
from electionwatch.models import Level
from electionwatch.poster import Post
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings
from electionwatch.store.memory import MemoryStore
from replay.clock import FakeClock
from replay.run import DEFAULT_DATA
from replay.scripted import ScriptedCallSource, ScriptedOddsSource, default_calls, default_swings
from replay.source import ReplaySource
from replay.timeline import load_year

log = logging.getLogger("replay.discord_live")
HIDDEN_GUILD = -1  # tracks every race; its posts are only counted


class SplitPoster:
    """Real guild -> Discord channel (sequential, labelled). Hidden guild -> counted only."""

    def __init__(self, channel, year: str, clock: FakeClock, record: Path | None = None):
        self.channel, self.year, self.clock = channel, year, clock
        self.sent: Counter = Counter()
        self.hidden: Counter = Counter()
        self.failed: Counter = Counter()
        self.record = record.open("a", encoding="utf-8") if record else None

    def _log(self, **row) -> None:
        if self.record:
            self.record.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.record.flush()

    async def post(self, guild_id: int, channel_id: int, post: Post) -> None:
        if guild_id == HIDDEN_GUILD:
            self.hidden[post.kind] += 1
            return
        sim = self.clock()
        post = dataclasses.replace(
            post,
            title=f"[SIM {self.year}] {post.title}",
            footer=((post.footer + " · ") if post.footer else "") + f"sim {sim:%b %d %H:%M} UTC",
        )
        t0 = time.monotonic()
        try:
            await self.channel.send(embed=render_embed(post))
        except Exception as e:  # recorded for the run analysis, then re-raised to the poller
            self.failed[type(e).__name__] += 1
            self._log(
                sim=f"{sim:%H:%M}",
                kind=post.kind,
                race=post.race,
                ok=False,
                error=f"{type(e).__name__}: {e}"[:300],
                lines=post.lines,
            )
            raise
        self.sent[post.kind] += 1
        self._log(
            sim=f"{sim:%H:%M}",
            kind=post.kind,
            race=post.race,
            ok=True,
            secs=round(time.monotonic() - t0, 2),
            lines=post.lines,
        )


def top_race_per_state(timelines: dict) -> list[str]:
    """Each state's headline race: Senate (regular before special), else Governor, else its
    first House seat, else anything statewide."""
    by_state: dict[str, list[str]] = {}
    for race_key in timelines:
        by_state.setdefault(parse(race_key).state, []).append(race_key)

    def rank(race_key: str):
        key = parse(race_key)
        order = {Level.SENATE: 0, Level.GOVERNOR: 1, Level.HOUSE: 2}.get(key.level, 3)
        return (order, key.special, key.office != "GOV", race_key)

    return [min(race_keys, key=rank) for _, race_keys in sorted(by_state.items())]


def post_subset(timelines: dict, spec: str) -> list[str]:
    picked = []
    for spec_part in (part.strip() for part in spec.split(",") if part.strip()):
        if spec_part.lower() == "top-per-state":
            picked += top_race_per_state(timelines)
        elif spec_part.lower() == "senate":
            picked += [race_key for race_key in timelines if parse(race_key).level is Level.SENATE]
        elif spec_part in timelines:
            picked.append(spec_part)
    return sorted(set(picked), key=picked.index)


async def run(args) -> None:
    races = [race.strip() for race in args.races.split(",") if race.strip()]
    timelines = load_year(Path(args.data).expanduser(), args.year, races)
    posting = post_subset(timelines, args.post)
    start_ts = min(series[0].ts for series in timelines.values())
    clock = FakeClock(start_ts.replace(second=0, microsecond=0) - timedelta(minutes=1))
    src = ReplaySource(timelines, clock)
    end = min(src.span()[1] + timedelta(minutes=30), clock() + timedelta(hours=args.max_hours))
    sources = {
        "results": [src],
        "calls": [ScriptedCallSource(default_calls(src), clock)],
        "odds": [ScriptedOddsSource(src, clock, default_swings(src))],
        "exitpolls": [],
    }
    store = MemoryStore()
    await store.save_guild(
        GuildSettings(guild_id=args.guild, channel_id=args.channel, important=posting, odds_alerts=True)
    )
    await store.save_guild(
        GuildSettings(guild_id=HIDDEN_GUILD, channel_id=0, important=sorted(timelines), odds_alerts=True)
    )

    client = discord.Client(intents=discord.Intents.none())
    done = asyncio.Event()

    @client.event
    async def on_ready():
        try:
            await night(client, args, timelines, posting, clock, end, sources, store, src)
        finally:
            done.set()

    async with client:
        task = asyncio.create_task(client.start(os.environ["DISCORD_TOKEN"]))
        await done.wait()
        await client.close()
        task.cancel()


async def night(client, a, timelines, posting, clock, end, sources, store, src) -> None:
    from electionwatch.poller import Poller

    channel = await client.fetch_channel(a.channel)
    if a.thread:
        channel = await channel.create_thread(
            name=a.thread, type=discord.ChannelType.public_thread, auto_archive_duration=1440
        )
    poster = SplitPoster(channel, a.year, clock, Path(a.record) if a.record else None)
    hours = (end - clock()).total_seconds() / 3600
    intro = (
        f"**[SIM {a.year}] Election-night replay starting** ({a.speed:g}x, ~{hours / a.speed * 60:.0f} min).\n"
        f"Tracking **{len(timelines)}** races; posting **{len(posting)}** here "
        f"({', '.join(posting[:6])}{' …' if len(posting) > 6 else ''}).\n"
        "Results are historical; race calls and odds are scripted test data."
    )
    if a.report_url:
        intro += f"\nFull run (all {len(timelines)} races): {a.report_url}"
    files = [discord.File(a.report)] if a.report else []
    await channel.send(intro, files=files)

    t0, ticks, slow = time.monotonic(), 0, 0.0
    async with httpx.AsyncClient() as http:
        poller = Poller(store, sources, poster, clock, http)
        while clock() <= end:
            tick_start = time.monotonic()
            await poller.tick()
            ticks += 1
            spent = time.monotonic() - tick_start
            slow = max(slow, spent)
            await asyncio.sleep(max(0.0, 60 / a.speed - spent))
            clock.advance(1)
            # Skip quiet stretches (e.g. Dixville Notch's midnight vote, then nothing until the
            # first polls close): jump to a minute before the next snapshot, posting nothing.
            nxt = src.next_change(clock())
            if nxt is not None and nxt - clock() > timedelta(minutes=a.idle_skip):
                skipped = nxt - clock() - timedelta(minutes=1)
                clock.advance(skipped.total_seconds() / 60)
                log.info("no new results for %s: skipping to %s", skipped, f"{clock():%H:%M}")
            if ticks % 60 == 0:
                log.info(
                    "sim %s, %d posts sent, slowest tick %.1fs", f"{clock():%H:%M}", sum(poster.sent.values()), slow
                )
    real = (time.monotonic() - t0) / 60
    await channel.send(
        f"**[SIM {a.year}] Replay finished** after {real:.0f} min real time ({ticks} simulated minutes).\n"
        f"Posted here: {dict(poster.sent)} · failed sends: {dict(poster.failed) or 0} · "
        f"all tracked races: {dict(poster.hidden)} · "
        f"slowest tick incl. Discord sends: {slow:.1f}s"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--year", required=True)
    ap.add_argument("--races", required=True, help="every race to track (comma-separated keys)")
    ap.add_argument("--post", default="senate", help='races to post, e.g. "PA SEN,PA-07,senate"')
    ap.add_argument("--speed", type=float, default=10)
    ap.add_argument("--guild", type=int, required=True)
    ap.add_argument("--channel", type=int, required=True)
    ap.add_argument("--data", default=str(DEFAULT_DATA))
    ap.add_argument("--max-hours", type=float, default=36)
    ap.add_argument("--report", default=None, help="file to attach to the first message")
    ap.add_argument("--report-url", default=None, help="link to the full-run report")
    ap.add_argument(
        "--idle-skip", type=float, default=90, help="fast-forward when no race has a new snapshot for this many minutes"
    )
    ap.add_argument("--thread", default=None, help="post in a new public thread of the channel with this name")
    ap.add_argument("--record", default=None, help="append one JSON line per Discord send here")
    ap.add_argument("--log-file", default=None, help="also write all log output (incl. discord.py) here")
    a = ap.parse_args()
    handlers = [logging.StreamHandler()] + ([logging.FileHandler(a.log_file)] if a.log_file else [])
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_env()
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
