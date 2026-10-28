"""Command changes during a running replay night (replay/command_sim.py) on the bundled tiny
night (`quick_timelines`: five races, a snapshot every 2 minutes from 8 PM to 1 AM ET, hand-made
numbers). No network, no history data."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from replay.command_sim import (
    QUICK_RACES,
    QUICK_SCENARIOS,
    QUICK_YEAR,
    SCENARIOS,
    Checks,
    Ctx,
    FakeInteraction,
    Night,
    Step,
    limit_problems,
    main,
    parse_steps,
    quick_timelines,
    sim_time,
)

YEAR = QUICK_YEAR
RACES = QUICK_RACES


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    return Ctx(YEAR, quick_timelines(), list(RACES), "top", tmp_path_factory.mktemp("sim"))


def test_dsl_parses_yaml_and_short_forms():
    steps = parse_steps('- at: "21:30"\n  cmd: posting mode=interval minutes=15\n  as: member\n- "22:00 resume"')
    assert steps == [Step("21:30", "posting mode=interval minutes=15", "member"), Step("22:00", "resume")]
    # 21:30 ET on 2024-11-05 is 02:30 UTC the next day; hours before noon are the next morning
    assert sim_time(YEAR, "21:30").isoformat() == "2024-11-06T02:30:00+00:00"
    assert sim_time(YEAR, "01:00") - sim_time(YEAR, "23:00") == timedelta(hours=2)


@pytest.mark.parametrize("name", ["pause", "posting", "nonadmin"])
async def test_scenario_passes_on_synthetic_night(ctx, name):
    checks = Checks()
    run_result = await SCENARIOS[name][1](ctx, checks)
    assert not checks.problems, checks.problems
    assert run_result.posts and run_result.cmds


async def test_checks_catch_a_command_that_does_nothing(ctx, monkeypatch):
    """Guard against vacuous checks: if /pause stops working, the pause scenario must fail."""
    from electionwatch.bot import discord as bot_discord

    async def broken_pause(interaction):
        await interaction.response.send_message("Election posts paused.", ephemeral=True)

    monkeypatch.setattr(bot_discord.pause_command, "_callback", broken_pause)
    checks = Checks()
    await SCENARIOS["pause"][1](ctx, checks)
    assert any("posts in" in problem for problem in checks.problems), checks.problems


async def test_admin_command_from_member_is_refused_and_recorded(ctx):
    night = Night(YEAR, ctx.timelines, [], until="20:05")
    run_result = await night.run(
        [{"at": "20:00", "cmd": "pause", "as": "member"}, {"at": "20:01", "cmd": "posting interval 0"}]
    )  # Discord's client blocks 0
    member, bad = run_result.cmds
    assert "required" in member.text and member.replies[0].ephemeral
    assert bad.rejected and not bad.replies
    assert not limit_problems(member.replies[0])
    assert isinstance(FakeInteraction(None, 1, "admin", "x").user.guild_permissions.manage_guild, bool)


def test_quick_mode_needs_no_history_data(tmp_path):
    """`--quick` is what `make sim` runs: bundled night, a few scenarios, no fetched timelines."""
    assert set(QUICK_SCENARIOS) <= set(SCENARIOS)
    assert not {"storeerr", "latencymongo", "mongorestart"} & set(QUICK_SCENARIOS)
    code = main(
        [
            "--quick",
            "--scenarios",
            "pause,posting",
            "--out-dir",
            str(tmp_path),
            "--data",
            str(tmp_path / "no-such-directory"),
        ]
    )
    assert code == 0
    rows = json.loads((tmp_path / "summary-top.json").read_text())
    assert [row["scenario"] for row in rows] == ["pause", "posting"]
    assert all(row["ok"] for row in rows), rows
