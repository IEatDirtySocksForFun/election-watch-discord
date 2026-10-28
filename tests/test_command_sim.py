"""Command changes during a running replay night (replay/command_sim.py) on a tiny synthetic
timeline: five races, a snapshot every 2 minutes from 8 PM to 1 AM ET. No network."""
from __future__ import annotations

from datetime import timedelta

import pytest

from electionwatch.models import CandidateResult, RaceResult
from electionwatch.racekeys import parse
from replay.command_sim import (SCENARIOS, Checks, Ctx, FakeInteraction, Night, Step, limit_problems,
                                parse_steps, sim_time)
from replay.timeline import Snapshot

YEAR = "2024"
RACES = ["AZ SEN", "GA SEN", "OH SEN", "PA SEN", "TX SEN"]


def _timelines() -> dict:
    out = {}
    t0 = sim_time(YEAR, "20:00")
    for n, race in enumerate(RACES):
        snaps = []
        for i in range(0, 150):
            ts = t0 + timedelta(minutes=2 * i + n)
            dem, rep = 1000 * i + 37 * n, 1010 * i + 11 * n
            r = RaceResult(parse(race), [CandidateResult("Dee Mock", "DEM", dem),
                                         CandidateResult("Ray Publican", "REP", rep)],
                           "replay", ts, expected_vote_pct=min(99.0, i * 0.7))
            snaps.append(Snapshot(ts, r.finalize(), True))
        out[race] = snaps
    return out


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    tl = _timelines()
    return Ctx(YEAR, tl, list(RACES), "top", tmp_path_factory.mktemp("sim"))


def test_dsl_parses_yaml_and_short_forms():
    steps = parse_steps('- at: "21:30"\n  cmd: posting mode=interval minutes=15\n  as: member\n- "22:00 resume"')
    assert steps == [Step("21:30", "posting mode=interval minutes=15", "member"), Step("22:00", "resume")]
    # 21:30 ET on 2024-11-05 is 02:30 UTC the next day; hours before noon are the next morning
    assert sim_time(YEAR, "21:30").isoformat() == "2024-11-06T02:30:00+00:00"
    assert sim_time(YEAR, "01:00") - sim_time(YEAR, "23:00") == timedelta(hours=2)


@pytest.mark.parametrize("name", ["pause", "posting", "nonadmin"])
async def test_scenario_passes_on_synthetic_night(ctx, name):
    c = Checks()
    res = await SCENARIOS[name][1](ctx, c)
    assert not c.problems, c.problems
    assert res.posts and res.cmds


async def test_checks_catch_a_command_that_does_nothing(ctx, monkeypatch):
    """Guard against vacuous checks: if /pause stops working, the pause scenario must fail."""
    from electionwatch.bot import discord as bot_discord

    async def broken_pause(interaction):
        await interaction.response.send_message("Election posts paused.", ephemeral=True)
    monkeypatch.setattr(bot_discord.pause_command, "_callback", broken_pause)
    c = Checks()
    await SCENARIOS["pause"][1](ctx, c)
    assert any("posts in" in p for p in c.problems), c.problems


async def test_admin_command_from_member_is_refused_and_recorded(ctx):
    night = Night(YEAR, ctx.timelines, [], until="20:05")
    res = await night.run([{"at": "20:00", "cmd": "pause", "as": "member"},
                           {"at": "20:01", "cmd": "posting interval 0"}])   # Discord's client blocks 0
    member, bad = res.cmds
    assert "required" in member.text and member.replies[0].ephemeral
    assert bad.rejected and not bad.replies
    assert not limit_problems(member.replies[0])
    assert isinstance(FakeInteraction(None, 1, "admin", "x").user.guild_permissions.manage_guild, bool)
