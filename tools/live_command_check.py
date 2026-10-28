"""Check the bot's command surface against the REAL Discord API (dev guild only).

    EW_ENV_FILES=.env python tools/live_command_check.py --guild 100000000000000001 \
        --channel 100000000000000002 --out replay/out/cmdtest

a) Logs in with the bot token (HTTP only, no gateway), copies the global commands to the
   dev guild and syncs them (exactly what ElectionBot.setup_hook does with DEV_GUILD_ID),
   then fetches the registered commands back and diffs them with the local schema.
b) Creates a thread "[TEST] command render <timestamp>" and posts, as ordinary bot
   messages, every embed / view / text payload the commands can produce (stress data
   included), so Discord itself validates the limits. Each send is recorded to JSONL with
   ok / HTTP status + Discord error code and message.

Ephemeral delivery cannot be exercised here: an ephemeral message only exists as the reply
to a real user's interaction, and this tool never automates a user account. It is proven at
payload level by tests/test_interaction_payloads.py.

Needs DISCORD_TOKEN (env or EW_ENV_FILES). The token is never printed or logged.
No LLM calls, no network besides discord.com.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
import httpx  # noqa: E402

from electionwatch.bot.command_data import format_odds, format_odds_overview, format_polls  # noqa: E402
from electionwatch.bot.discord import ElectionBot  # noqa: E402
from electionwatch.bot.logic import (  # noqa: E402
    change_calls, change_favorite, change_odds, change_posting, change_setup, change_watch,
    faq_text, settings_dump,
)
from electionwatch.bot.navigator import RaceNavigator  # noqa: E402
from electionwatch.bot.poster import render_embed  # noqa: E402
from electionwatch.config import load_env  # noqa: E402
from electionwatch.models import (  # noqa: E402
    CandidateResult, ExitPoll, Level, OddsSnapshot, Poll, PollResult, RaceCall, RaceKey, RaceResult,
)
from electionwatch.poller import format_call, format_exit_poll, format_result  # noqa: E402
from electionwatch.racekeys import ALL_JURISDICTIONS, STATES, TERRITORIES  # noqa: E402
from electionwatch.settings import GuildSettings, PostMode, UserSettings  # noqa: E402
from electionwatch.store.memory import MemoryStore  # noqa: E402

log = logging.getLogger("live_command_check")
NOW = datetime.now(timezone.utc).replace(microsecond=0)
LONG = "Bartholomew-Maximilian Featherstonehaugh-Wolfeschlegelsteinhausen Jr."   # 69 chars
# A real Washington-style ballot measure: adapters slug the full contest name for LOCAL keys.
LONG_LOCAL = ("Advisory Vote No. 10 Engrossed Second Substitute Senate Bill 5096 concerning "
              "an excise tax on capital gains")
SAMPLE_STATES = ["TX", "CA", "AK", "NY", "DC", "WA"]


# --------------------------------------------------------------------------- fake data


class FakeResults:
    """A results source whose list_races() returns many, long-named races of every level."""

    name = "live_check_results"
    states = set(STATES) | set(TERRITORIES)

    def available(self) -> bool:
        return True

    async def list_races(self, state, http):
        from electionwatch.sources.results._common import local_key

        races = [(RaceKey(state, Level.SENATE, "SEN"), f"{ALL_JURISDICTIONS[state]} U.S. Senate"),
                 (RaceKey(state, Level.SENATE, "SEN", special=True), f"{ALL_JURISDICTIONS[state]} U.S. Senate (special)"),
                 (RaceKey(state, Level.GOVERNOR, "GOV"), f"{ALL_JURISDICTIONS[state]} Governor"),
                 (RaceKey(state, Level.GOVERNOR, "AG"), f"{ALL_JURISDICTIONS[state]} Attorney General")]
        races += [(RaceKey(state, Level.HOUSE, "HOUSE", f"{n:02d}"),
                   f"{ALL_JURISDICTIONS[state]} Congressional District {n} – {LONG} vs {LONG}")
                  for n in range(1, 39)]
        races += [(RaceKey(state, Level.STATE, "HD", f"{n:03d}"), f"State House District {n}")
                  for n in range(1, 31)]
        races += [(local_key(state, f"{LONG_LOCAL} {n}"), f"{LONG_LOCAL} {n}") for n in range(3)]
        races += [(local_key(state, "Harris County Judge"), "Harris County Judge")]
        return races


def odds_snapshots(key: RaceKey, outcomes: int, long_names: bool) -> list[OddsSnapshot]:
    snaps = []
    for market in ("kalshi", "polymarket"):
        for n in range(outcomes):
            name = f"{LONG} #{n}" if long_names else ("DEM", "REP", "IND")[n % 3] + (str(n) if n > 2 else "")
            snaps.append(OddsSnapshot(
                key, market, name, max(0.001, 1 / (n + 2)), NOW - timedelta(minutes=n),
                market_id=f"m{n}",
                url=f"https://{market}.com/markets/{key.state.lower()}-{key.office.lower()}-2026-outcome-{n}"
                    "?utm_source=election-watch"))
    return snaps


def polls(key: RaceKey, n_polls: int, candidates: int, long_names: bool) -> list[Poll]:
    out = []
    for p in range(n_polls):
        results = [PollResult(f"{LONG} {c}" if long_names else f"Candidate {c}",
                              ("DEM", "REP", "LIB", None)[c % 4], 40.0 / (c + 1))
                   for c in range(candidates)]
        out.append(Poll(key, f"{LONG} Research & Polling Partners {p}" if long_names else f"Pollster {p}",
                        results, date(2026, 10, 30) - timedelta(days=p), start_date=date(2026, 10, 20) - timedelta(days=p),
                        sample_size=1000 + p, population="LV",
                        sponsor=f"{LONG} Committee for Good Government {p}" if long_names else None,
                        partisan="REP" if p % 3 == 0 else None, source="live_check",
                        url=f"https://example.org/polls/{key.state.lower()}/{p}/" + "x" * (60 if long_names else 0)))
    return out


def result(key: RaceKey, candidates: int, long_names: bool) -> RaceResult:
    cands = [CandidateResult((LONG * 4)[:250] + f" {c}" if long_names else f"Candidate Number {c}",
                             ("DEM", "REP", "LIB", "GRN", "IND", None)[c % 6], 1_000_000 // (c + 1),
                             incumbent=c == 0, winner=c == 0)
             for c in range(candidates)]
    r = RaceResult(key, cands, "live_check_results" + (" " + LONG * 3 if long_names else ""), NOW,
                   precincts_reporting=8123, precincts_total=9001, expected_vote_pct=87.4,
                   total_votes=sum(c.votes for c in cands), registered_voters=12_345_678, turnout_pct=61.2,
                   url="https://results.example.gov/2026/general/" + ("very-long-path/" * 8 if long_names else ""),
                   title=(f"{LONG_LOCAL} " * 4) if long_names else None)
    r.finalize() if hasattr(r, "finalize") else None
    return r


class CheckBot(ElectionBot):
    async def setup_hook(self) -> None:   # no sync-on-login and no polling loop: we drive it
        return None


# --------------------------------------------------------------------------- schema


def _norm_option(o: dict) -> dict:
    keep = {k: o[k] for k in ("type", "name", "description", "min_value", "max_value",
                              "min_length", "max_length", "autocomplete") if o.get(k) not in (None, False)}
    keep["required"] = bool(o.get("required", False))
    if o.get("choices"):
        keep["choices"] = [(c["name"], c["value"]) for c in o["choices"]]
    if o.get("channel_types"):
        keep["channel_types"] = sorted(o["channel_types"])
    if o.get("options"):
        keep["options"] = [_norm_option(x) for x in o["options"]]
    return keep


def _norm_command(c: dict) -> dict:
    return {"name": c["name"], "description": c["description"], "type": c.get("type", 1),
            "options": [_norm_option(o) for o in c.get("options", [])]}


async def check_schema(bot: CheckBot, guild: discord.Object) -> dict:
    local = {c.name: _norm_command(c.to_dict(bot.tree)) for c in bot.tree.get_commands()}
    bot.tree.copy_global_to(guild=guild)
    report: dict = {"local_commands": sorted(local)}
    try:
        synced = await bot.tree.sync(guild=guild)
        report["sync"] = {"ok": True, "count": len(synced)}
    except discord.HTTPException as e:
        report["sync"] = {"ok": False, "status": e.status, "code": e.code, "error": e.text}
        return report
    remote_cmds = await bot.tree.fetch_commands(guild=guild)
    remote = {c.name: _norm_command(c.to_dict()) for c in remote_cmds}
    diffs = {}
    for name in sorted(set(local) | set(remote)):
        if local.get(name) != remote.get(name):
            diffs[name] = {"local": local.get(name), "remote": remote.get(name)}
    report["remote_commands"] = sorted(remote)
    report["diff"] = diffs
    report["global_commands_registered"] = sorted(c.name for c in await bot.tree.fetch_commands())
    return report


# --------------------------------------------------------------------------- payloads


def wrap_yaml(text: str) -> str:
    return f"```yaml\n{text}\n```"


async def build_payloads(bot: CheckBot):
    """Yield (label, send-kwargs) for everything the commands can render."""
    tx_sen = RaceKey("TX", Level.SENATE, "SEN")

    # Short replies of the admin/user commands (content only).
    gs = GuildSettings(guild_id=1)
    yield "setup reply", {"content": change_setup(gs, 100000000000000002, 100000000000000001)}
    yield "posting reply", {"content": change_posting(gs, "interval", 30)}
    yield "calls reply", {"content": change_calls(gs, "AP,CBS,FOX,NBC,CNN,ABC,DDHQ,VOTEHUB,NYT", True)}
    yield "oddsalerts reply", {"content": change_odds(gs, True, 0.1, 1440)}
    yield "favorite reply", {"content": change_favorite(UserSettings(1), "add", "TX SEN")}
    yield "watch add reply", {"content": change_watch(gs, "add", "TX SEN")}
    yield "error reply", {"content": "Something went wrong running that command. Try again in a moment."}

    # /settings: default and stuffed settings.
    yield "settings default", {"content": wrap_yaml(settings_dump(GuildSettings(guild_id=100000000000000001)))}
    big = GuildSettings(guild_id=100000000000000001, channel_id=100000000000000002, admin_role_id=1,
                        post_mode=PostMode.INTERVAL, interval_minutes=1440)
    for state in STATES:
        big.important += [f"{state} SEN", f"{state} GOV"] + [f"{state}-{n:02d}" for n in range(1, 5)]
    big.important = big.important[:300]
    yield "settings 300 races", {"content": wrap_yaml(settings_dump(big))}

    # /watch list with 300 races.
    yield "watch list 300", {"content": change_watch(big, "list")}
    yield "watch list default", {"content": change_watch(GuildSettings(guild_id=2), "list")}

    # /faq: no state, every state, every territory (the command answers with the error text).
    from electionwatch.faq.loader import load_outlets, load_states

    states, outlets = load_states(), load_outlets()
    yield "faq (no state)", {"content": wrap_yaml(faq_text(states, outlets, None))}
    for code in list(STATES) + list(TERRITORIES):
        try:
            text = wrap_yaml(faq_text(states, outlets, code))
        except ValueError as exc:
            text = str(exc)
        yield f"faq {code}", {"content": text}

    # /odds overview + per race.
    keys = [RaceKey(s, Level.SENATE, "SEN") for s in STATES] + [
        RaceKey(s, Level.HOUSE, "HOUSE", f"{n:02d}") for s in STATES for n in range(1, 6)]
    keys = keys[:300]
    yield "odds overview 300 races, long names", {"embed": format_odds_overview(
        [(k, odds_snapshots(k, 6, True)) for k in keys])}
    yield "odds overview 15 races", {"embed": format_odds_overview(
        [(k, odds_snapshots(k, 2, False)) for k in keys[:15]])}
    yield "odds overview empty", {"embed": format_odds_overview([(k, []) for k in keys[:3]])}
    yield "odds overview no races", {"embed": format_odds_overview([])}
    moves = {(m, f"{LONG} #{n}"): {1: 12.3, 24: -45.6} for m in ("kalshi", "polymarket") for n in range(40)}
    yield "odds race 40 long outcomes", {"embed": format_odds(tx_sen, odds_snapshots(tx_sen, 40, True), moves)}
    yield "odds race 3 outcomes", {"embed": format_odds(tx_sen, odds_snapshots(tx_sen, 3, False))}
    yield "odds race none", {"embed": format_odds(tx_sen, [])}

    # /polls.
    yield "polls 10 long names", {"embed": format_polls(tx_sen, polls(tx_sen, 12, 12, True), count=10)}
    yield "polls 10 short", {"embed": format_polls(tx_sen, polls(tx_sen, 12, 4, False), count=10)}
    yield "polls 10 no internals", {"embed": format_polls(tx_sen, polls(tx_sen, 30, 3, False), count=10,
                                                           internals=False)}
    yield "polls none", {"embed": format_polls(tx_sen, [], count=5)}
    yield "polls no source", {"embed": format_polls(tx_sen, [], sources_available=False)}

    # Result / call / exit-poll embeds.
    for label, key, n, long_names, turnout in (
        ("result 50 candidates", tx_sen, 50, False, True),
        ("result long names", RaceKey("WA", Level.LOCAL, "LOCAL", "x" * 150), 8, True, True),
        ("result 1 candidate", RaceKey("AK", Level.HOUSE, "HOUSE", "AL"), 1, False, False),
    ):
        r = result(key, n, long_names)
        await bot.store.upsert_result(r)
        yield label, {"embed": render_embed(format_result(r, turnout=turnout))}
    yield "call", {"embed": render_embed(format_call(RaceCall(tx_sen, "AP", "Candidate Number 0", "REP", NOW,
                                                              url="https://apnews.com/")))}
    yield "call long names", {"embed": render_embed(format_call(RaceCall(
        RaceKey("WA", Level.LOCAL, "LOCAL", "y" * 200), "VOTEHUB", LONG * 5, "Working Families Party", NOW)))}
    yield "call retracted", {"embed": render_embed(format_call(RaceCall(tx_sen, "FOX", "Candidate", "DEM", NOW,
                                                                        retracted=True)))}
    yield "exit poll", {"embed": render_embed(format_exit_poll(ExitPoll(
        tx_sen, "CNN", "https://cnn.com/election/2026/exit-polls/texas/senate",
        "Women: D 55 R 44\n" + "Independents: " + "x" * 3000)))}

    # The race navigator: every tab for a sample of states, plus the race-select step. It reads
    # the stored race lists: run the background refresh until every state is listed.
    while await bot.race_lists.refresh():
        pass
    for state in SAMPLE_STATES:
        for level in ("congressional", "senate", "state", "local"):
            view = RaceNavigator(bot, 1, UserSettings(1, home_state=state, default_level=level))
            await view.refresh()
            yield f"navigator {state} {level}", {"embed": await view.embed(), "view": view}
            if state in ("TX", "WA"):
                view.selected_race = None
                view._build_controls()
                yield f"navigator {state} {level} race select", {"embed": await view.embed(), "view": view}

    # Paging, territories and the favorites tab (added after the first live check).
    view = RaceNavigator(bot, 1, UserSettings(1, home_state="TX", default_level="congressional"))
    await view.refresh()
    view.selected_race = None
    for page in range(2):
        view.page = page
        view._build_controls()
        yield f"navigator TX congressional page {page + 1}", {"embed": await view.embed(), "view": view}
    for territory in ("PR", "GU"):
        view = RaceNavigator(bot, 1, UserSettings(1, home_state=territory, default_level="senate"))
        await view.refresh()
        yield f"navigator {territory}", {"embed": await view.embed(), "view": view}
    favorites = ["PA SEN", "PA-07", "GA GOV", "TX SEN", "AK-AL"] + [f"CA-{n:02d}" for n in range(1, 31)]
    for label, favs in (("favorites tab", favorites), ("favorites tab empty", [])):
        view = RaceNavigator(bot, 1, UserSettings(1, default_level="favorites", favorites=favs))
        await view.refresh()
        view.selected_race = None
        view._build_controls()
        yield f"navigator {label}", {"embed": await view.embed(), "view": view}

    # civicAPI result and call, from the saved real responses.
    from electionwatch.sources.calls.civicapi import parse_call, parse_race

    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "civicapi"
    race = json.loads((fixtures / "race_tx_sen_2024.json").read_text())
    yield "civicapi result", {"embed": render_embed(format_result(parse_race(race, 107793, tx_sen, NOW)))}
    call = parse_call(race | {"called_at": "2024-11-06T02:10:00.000Z"}, 107793, tx_sen)
    yield "civicapi call", {"embed": render_embed(format_call(call))}

    # /earlyvote against the live civicAPI (the client spaces its requests itself).
    from electionwatch.bot.early_vote import early_vote_embed
    from electionwatch.sources.early_vote import EarlyVoteClient

    async with httpx.AsyncClient(headers={"User-Agent": "election-watch-discord/0.1 (+https://github.com/IEatDirtySocksForFun/election-watch-discord)"}) as http:
        ev = EarlyVoteClient(http)
        for state, breakdown in ((None, None), ("GA", None), ("NC", "party"), ("NC", "county"),
                                 ("GA", "age"), ("TX", "method"), ("NY", None), ("IL", None)):
            try:
                embed = await early_vote_embed(ev, state, breakdown)
            except Exception as exc:  # noqa: BLE001 - recorded, the run goes on
                yield f"earlyvote {state} {breakdown} ERROR {type(exc).__name__}", {"content": f"error: {exc}"[:1900]}
                continue
            yield f"earlyvote {state or 'overview'} {breakdown or ''}".strip(), {"embed": embed}


# --------------------------------------------------------------------------- main


async def run(a) -> int:
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record_path = out / f"sends-{stamp}.jsonl"
    summary_path = out / f"summary-{stamp}.json"
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN is not set (env or EW_ENV_FILES)", file=sys.stderr)
        return 2
    summary: dict = {"started": stamp, "guild": a.guild, "channel": a.channel}
    async with httpx.AsyncClient() as http_client:
        bot = CheckBot(MemoryStore(), {"results": [FakeResults()]}, http_client)
        async with bot:
            await bot.login(token)
            summary["bot_user"] = str(bot.user)
            if not a.skip_sync:
                summary["schema"] = await check_schema(bot, discord.Object(id=a.guild))
                print("schema:", json.dumps(summary["schema"].get("sync")),
                      "diffs:", list(summary["schema"].get("diff", {})))
            channel = await bot.fetch_channel(a.channel)
            thread = await channel.create_thread(
                name=f"[TEST] command render {stamp}"[:100], type=discord.ChannelType.public_thread,
                auto_archive_duration=1440)
            summary["thread_id"] = thread.id
            summary["thread_url"] = f"https://discord.com/channels/{a.guild}/{thread.id}"
            print("thread:", summary["thread_url"])
            ok = failed = 0
            none = discord.AllowedMentions.none()
            with record_path.open("w", encoding="utf-8") as rec:
                index = 0
                async for label, kwargs in build_payloads(bot):
                    index += 1
                    if index > a.max_messages:
                        break
                    row = {"i": index, "label": label}
                    t0 = time.monotonic()
                    try:
                        message = await thread.send(allowed_mentions=none, **kwargs)
                        row.update(ok=True, message_id=message.id)
                        ok += 1
                    except discord.HTTPException as e:
                        row.update(ok=False, status=e.status, code=e.code, error=e.text[:2000])
                        failed += 1
                        print(f"FAIL {label}: HTTP {e.status} code {e.code}: {e.text[:300]}")
                    finally:
                        view = kwargs.get("view")
                        if view is not None:
                            view.stop()
                    row["secs"] = round(time.monotonic() - t0, 2)
                    rec.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rec.flush()
            summary.update(sent_ok=ok, sent_failed=failed, record=str(record_path))
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"sent ok={ok} failed={failed}; record {record_path}; summary {summary_path}")
    return 1 if failed or summary.get("schema", {}).get("diff") or not summary.get("schema", {}).get(
        "sync", {"ok": True}).get("ok") else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--guild", type=int, required=True)
    ap.add_argument("--channel", type=int, required=True)
    ap.add_argument("--out", default=str(ROOT.parent.parent / "logs" / "cmdtest"))
    ap.add_argument("--max-messages", type=int, default=250)
    ap.add_argument("--skip-sync", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    sys.exit(asyncio.run(run(a)))


if __name__ == "__main__":
    main()
