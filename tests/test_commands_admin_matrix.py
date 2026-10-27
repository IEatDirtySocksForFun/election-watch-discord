"""Exhaustive matrix for the admin/config slash commands.

Every command is invoked through ``command.callback(interaction, ...)`` with the fakes in
tests/fake_interaction_admin.py. Every invocation goes through ``invoke()``, which asserts
the command answered exactly once and that every reply is ephemeral.

Use-case checklist
==================
Permissions (every command x {Manage Server, configured admin role, plain member, DM})
  [x] admin-only commands (setup, watch add/remove, posting, calls, oddsalerts, turnout,
      exitpolls, pause, resume) succeed for Manage Server and for the admin role
  [x] ... refuse a plain member with an ephemeral "Manage Server" message, store unchanged
  [x] ... refuse a DM with an ephemeral "use it in a server" message, store unchanged
  [x] /watch list and /settings work for plain members; refuse DMs
  [x] /favorite works for everyone, including DMs
  [x] a refused or read-only command creates no guild document
  [x] /watch list does not write the guild (no lost update over a concurrent admin change)
  [x] a member without a ``roles`` attribute (discord.User in a guild) does not crash is_admin
/setup
  [x] without admin_role: channel set, role untouched; with admin_role: both set
  [x] the configured role then grants a non-Manage-Server member every admin command
  [x] changing the channel keeps the admin role; a role admin may run /setup
/posting
  [x] every mode x minutes {None, 1, 30, 1440}; minutes stored only for interval
  [x] logic rejects minutes 0 / 1441 / -1 and bad modes without changing anything
  [x] the command reports out-of-range minutes ephemerally and saves nothing
  [x] interval without minutes keeps the previous interval (or 5); switching modes back and
      forth keeps interval_minutes sane
/oddsalerts
  [x] on/off x threshold {None, 0.1, 50, 100} x window {None, 1, 1440}
  [x] logic rejects threshold 0 / -1 / 100.01 / nan / inf and window 0 / 1441, atomically
/calls
  [x] every outlet singly, all outlets, comma / space / mixed-case separators, duplicates
  [x] unknown outlet, empty / blank / separators-only strings rejected, nothing saved
  [x] on sets the outlet list; off stops only the named outlets
/watch
  [x] every accepted key format (house, at-large, senate, specials, every statewide office,
      state legislature, local, long names, all states + DC, territories PR/GU/VI/AS/MP)
  [x] invalid keys: garbage, empty, blank, unknown state, unknown office, 3-digit district,
      unicode look-alikes, emoji, 500-char strings, over-long local slugs
  [x] duplicates (including equivalent spellings) stored once
  [x] removing the last race falls back to defaults; removing a non-watched race changes
      nothing and says so
  [x] list fits in one message with 500 watched races
  [x] every reply (including errors echoing user input) fits in 2000 characters
/pause /resume /turnout /exitpolls
  [x] idempotent, persisted, on/off messages
/settings
  [x] valid YAML inside a ```yaml block, <= 2000 chars, default and huge watch lists
  [x] post_mode / paused stay visible with a huge watch list
/favorite
  [x] add / remove / duplicate / invalid / remove-not-favorited; per-user isolation; DMs
Persistence
  [x] every setting round-trips through MemoryStore and MongoStore (local mongod; skipped
      when unreachable)
Errors
  [x] handle_app_command_error: response not done, done, HTTPException while reporting,
      no command attribute, no secret leak; _error clips long messages; a store failure in a
      command propagates to the tree's error handler which answers exactly once
Poller interplay (Poller + RecordingPoster + fake sources)
  [x] no channel -> no posts; /pause -> no results/call/exit-poll posts; /resume -> posts
  [x] /posting immediate vs default vs interval drive post_due
  [x] /calls off suppresses call posts; /calls outlets filter which outlet's calls post
  [x] /turnout off removes the turnout line; /exitpolls off suppresses exit polls
  [x] /watch add / remove changes which races post
Real ElectionBot as interaction.client
  [x] the commands work with a real ElectionBot(MemoryStore(), {}, httpx.AsyncClient())
"""
from __future__ import annotations

import copy
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import yaml

from electionwatch.bot import discord as bot_discord
from electionwatch.bot.discord import (
    ElectionBot,
    _error,
    _guild,
    _user,
    calls_command,
    exitpolls_command,
    favorite_command,
    handle_app_command_error,
    odds_alerts_command,
    pause_command,
    posting_command,
    resume_command,
    settings_command,
    setup_command,
    turnout_command,
    watch_command,
)
from electionwatch.bot.logic import (
    OUTLETS,
    change_calls,
    change_favorite,
    change_odds,
    change_posting,
    change_setup,
    change_watch,
    is_admin,
    settings_dump,
)
from electionwatch.config import load_races
from electionwatch.models import CandidateResult, ExitPoll, RaceCall, RaceResult, to_doc
from electionwatch.poller import Poller
from electionwatch.poster import RecordingPoster
from electionwatch.racekeys import STATES, STATEWIDE, TERRITORIES, parse
from electionwatch.settings import DEFAULT_INTERVAL_MIN, GuildSettings, PostMode, UserSettings, post_due
from electionwatch.sources.base import CallSource, ExitPollSource, ResultsSource
from electionwatch.store.memory import MemoryStore
from electionwatch.store.mongo import MongoStore

from fake_interaction_admin import (  # noqa: E402  (tests/ is on sys.path under pytest)
    ADMIN_ROLE_ID,
    DISCORD_LIMIT,
    GUILD_ID,
    OTHER_ROLE_ID,
    ROLES,
    FakeChannel,
    FakeInteraction,
    FakeMember,
    FakeRole,
    FakeUser,
    http_exception,
    make_interaction,
    stand_in_client,
)

DEFAULT_KEYS = [str(race.key) for race in load_races()]
NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- fixtures


async def _mongo_store():
    from pymongo.asynchronous.mongo_client import AsyncMongoClient

    uri = os.environ.get("EW_TEST_ADMIN_MONGO_URI", "mongodb://127.0.0.1:27017")
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=800)
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        pytest.skip("no local mongod reachable")
    name = f"ew_admin_test_{uuid.uuid4().hex}"
    return MongoStore(uri, name, client=client), client, name


@pytest.fixture(params=["memory", "mongo"])
async def store(request):
    if request.param == "memory":
        yield MemoryStore()
        return
    mongo, client, name = await _mongo_store()
    try:
        yield mongo
    finally:
        await client.drop_database(name)
        await client.close()


@pytest.fixture
def memory_store():
    return MemoryStore()


def baseline() -> GuildSettings:
    return GuildSettings(
        GUILD_ID,
        channel_id=10,
        admin_role_id=ADMIN_ROLE_ID,
        important=["TX SEN", "GA GOV"],
        post_mode=PostMode.INTERVAL,
        interval_minutes=20,
        post_calls=True,
        call_outlets=["AP", "NBC", "CNN", "FOX", "DDHQ", "VOTEHUB"],
        post_turnout=True,
        post_exit_polls=False,
        odds_alerts=False,
        paused=True,
    )


async def invoke(command, interaction: FakeInteraction, **kwargs):
    """Run a command callback; it must answer exactly once and only ephemerally."""
    await command.callback(interaction, **kwargs)
    assert len(interaction.replies) == 1, f"expected exactly one reply, got {interaction.sent}"
    assert all(item.ephemeral for item in interaction.sent), interaction.sent
    for item in interaction.replies:
        assert item.content is None or len(item.content) <= DISCORD_LIMIT
    return interaction.replies[0]


async def guild_doc(store, guild_id=GUILD_ID):
    gs = await store.get_guild(guild_id)
    return None if gs is None else to_doc(gs)


async def user_doc(store, user_id):
    us = await store.get_user(user_id)
    return None if us is None else to_doc(us)


def yaml_block(content: str) -> dict:
    assert content.startswith("```yaml\n") and content.endswith("\n```"), content[:40]
    return yaml.safe_load(content[len("```yaml\n"):-len("\n```")])


# --------------------------------------------------------------------------- permission matrix

REFUSE_PLAIN = "Manage Server permission or the configured admin role is required"
REFUSE_DM = "Use this command in a server"

# (id, command, kwargs, admin_only, guild_only, check(guild_settings, user_settings, reply))
SPECS = [
    ("setup", setup_command, {"channel": FakeChannel(555)}, True, True,
     lambda gs, us, r: gs.channel_id == 555 and gs.admin_role_id == ADMIN_ROLE_ID and "<#555>" in r),
    ("setup+role", setup_command, {"channel": FakeChannel(556), "admin_role": FakeRole(ADMIN_ROLE_ID)},
     True, True, lambda gs, us, r: gs.channel_id == 556 and f"<@&{ADMIN_ROLE_ID}>" in r),
    ("watch add", watch_command, {"action": "add", "race": "nc-1"}, True, True,
     lambda gs, us, r: gs.important == ["TX SEN", "GA GOV", "NC-01"] and r == "Watching NC-01"),
    ("watch remove", watch_command, {"action": "remove", "race": "Texas Senate"}, True, True,
     lambda gs, us, r: gs.important == ["GA GOV"] and "Removed TX SEN" in r),
    ("watch list", watch_command, {"action": "list"}, False, True,
     lambda gs, us, r: r == "Watched races: TX SEN, GA GOV"),
    ("posting interval", posting_command, {"mode": "interval", "minutes": 30}, True, True,
     lambda gs, us, r: gs.post_mode is PostMode.INTERVAL and gs.interval_minutes == 30),
    ("posting immediate", posting_command, {"mode": "immediate"}, True, True,
     lambda gs, us, r: gs.post_mode is PostMode.IMMEDIATE),
    ("posting default", posting_command, {"mode": "default"}, True, True,
     lambda gs, us, r: gs.post_mode is PostMode.DEFAULT),
    ("calls on", calls_command, {"outlets": "ap cnn", "enabled": "on"}, True, True,
     lambda gs, us, r: gs.post_calls and gs.call_outlets == ["AP", "CNN"]),
    ("calls off", calls_command, {"outlets": "FOX", "enabled": "off"}, True, True,
     lambda gs, us, r: "FOX" not in gs.call_outlets),
    ("oddsalerts on", odds_alerts_command, {"enabled": "on", "threshold": 5.0, "window": 30}, True, True,
     lambda gs, us, r: gs.odds_alerts and gs.odds_swing_pts == 5.0 and gs.odds_window_minutes == 30),
    ("oddsalerts off", odds_alerts_command, {"enabled": "off"}, True, True,
     lambda gs, us, r: not gs.odds_alerts),
    ("turnout off", turnout_command, {"enabled": "off"}, True, True,
     lambda gs, us, r: gs.post_turnout is False and r == "turnout off"),
    ("exitpolls on", exitpolls_command, {"enabled": "on"}, True, True,
     lambda gs, us, r: gs.post_exit_polls is True and r == "exit polls on"),
    ("pause", pause_command, {}, True, True, lambda gs, us, r: gs.paused is True),
    ("resume", resume_command, {}, True, True, lambda gs, us, r: gs.paused is False),
    ("settings", settings_command, {}, False, True,
     lambda gs, us, r: yaml_block(r)["guild_id"] == GUILD_ID),
    ("favorite add", favorite_command, {"action": "add", "race": "tx sen"}, False, False,
     lambda gs, us, r: us.favorites == ["GA GOV", "TX SEN"] and r == "Favorited TX SEN"),
    ("favorite remove", favorite_command, {"action": "remove", "race": "GA GOV"}, False, False,
     lambda gs, us, r: us.favorites == []),
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("spec", SPECS, ids=[spec[0] for spec in SPECS])
async def test_permission_matrix(store, spec, role):
    _, command, kwargs, admin_only, guild_only, check = spec
    await store.save_guild(baseline())
    await store.save_user(UserSettings(501, favorites=["GA GOV"]))
    before = (await guild_doc(store), await user_doc(store, 501))
    interaction = make_interaction(stand_in_client(store), role)

    reply = await invoke(command, interaction, **copy.deepcopy(kwargs))

    allowed = not guild_only or role != "dm"
    if admin_only:
        allowed = role in ("manage", "role_admin")
    if allowed:
        assert REFUSE_PLAIN not in reply.content and REFUSE_DM not in reply.content
        gs = await store.get_guild(GUILD_ID)
        us = await store.get_user(501)
        assert check(gs, us, reply.content), reply.content
        if not admin_only and guild_only:          # read-only commands
            assert (await guild_doc(store), await user_doc(store, 501)) == before
    else:
        assert (REFUSE_DM if role == "dm" else REFUSE_PLAIN) in reply.content
        assert (await guild_doc(store), await user_doc(store, 501)) == before


@pytest.mark.parametrize("role", ["plain", "dm"])
@pytest.mark.parametrize("spec", SPECS, ids=[spec[0] for spec in SPECS])
async def test_refused_or_read_only_commands_create_no_guild_document(memory_store, spec, role):
    _, command, kwargs, admin_only, guild_only, _check = spec
    if not guild_only:
        pytest.skip("favorite is per user")
    interaction = make_interaction(stand_in_client(memory_store), role)
    await invoke(command, interaction, **copy.deepcopy(kwargs))
    assert await memory_store.get_guild(GUILD_ID) is None


async def test_watch_list_does_not_overwrite_a_concurrent_admin_change(memory_store):
    """A read-only /watch list must not write back the copy it read (lost update)."""
    await memory_store.save_guild(baseline())

    real_get = memory_store.get_guild

    async def get_then_admin_changes(guild_id):
        gs = await real_get(guild_id)
        # An admin's /pause lands between this read and any write.
        changed = await real_get(guild_id)
        changed.paused = False
        changed.important.append("NC-01")
        await memory_store.save_guild(changed)
        return gs

    memory_store.get_guild = get_then_admin_changes
    await invoke(watch_command, make_interaction(stand_in_client(memory_store), "plain"), action="list")
    memory_store.get_guild = real_get
    gs = await memory_store.get_guild(GUILD_ID)
    assert gs.paused is False and "NC-01" in gs.important


def test_is_admin_with_a_user_object_that_has_no_roles():
    gs = GuildSettings(GUILD_ID, admin_role_id=ADMIN_ROLE_ID)
    assert is_admin(FakeUser(1), gs) is False
    assert is_admin(FakeUser(1), GuildSettings(GUILD_ID)) is False
    assert is_admin(FakeMember(1, manage_guild=True), gs) is True
    assert is_admin(FakeMember(1, roles=[FakeRole(ADMIN_ROLE_ID)]), gs) is True
    assert is_admin(FakeMember(1, roles=[FakeRole(OTHER_ROLE_ID)]), gs) is False
    # A role id that only matches some *other* attribute is not enough.
    assert is_admin(FakeMember(1, roles=[FakeRole(OTHER_ROLE_ID, name=str(ADMIN_ROLE_ID))]), gs) is False


async def test_admin_command_in_a_guild_from_a_user_without_roles_is_refused(memory_store):
    await memory_store.save_guild(baseline())
    interaction = FakeInteraction(stand_in_client(memory_store), FakeUser(501), GUILD_ID)
    reply = await invoke(pause_command, interaction)
    assert REFUSE_PLAIN in reply.content


# --------------------------------------------------------------------------- /setup


async def test_setup_without_role_then_role_grants_access_to_ordinary_member(store):
    client = stand_in_client(store)
    manager = make_interaction(client, "manage")
    reply = await invoke(setup_command, manager, channel=FakeChannel(42))
    gs = await store.get_guild(GUILD_ID)
    assert gs.channel_id == 42 and gs.admin_role_id is None
    assert reply.content == "Election posts will go to <#42>."

    # Before the role is configured, the role holder is an ordinary member.
    reply = await invoke(pause_command, make_interaction(client, "role_admin"))
    assert REFUSE_PLAIN in reply.content
    assert (await store.get_guild(GUILD_ID)).paused is False

    await invoke(setup_command, make_interaction(client, "manage"), channel=FakeChannel(42),
                 admin_role=FakeRole(ADMIN_ROLE_ID))
    assert (await store.get_guild(GUILD_ID)).admin_role_id == ADMIN_ROLE_ID

    for command, kwargs in [
        (pause_command, {}), (resume_command, {}), (turnout_command, {"enabled": "off"}),
        (exitpolls_command, {"enabled": "off"}), (posting_command, {"mode": "immediate"}),
        (calls_command, {"outlets": "AP", "enabled": "on"}),
        (odds_alerts_command, {"enabled": "on"}), (watch_command, {"action": "add", "race": "NC-01"}),
        (setup_command, {"channel": FakeChannel(43)}),
    ]:
        reply = await invoke(command, make_interaction(client, "role_admin"), **kwargs)
        assert REFUSE_PLAIN not in reply.content, command.name
    gs = await store.get_guild(GUILD_ID)
    # Changing the channel without a role keeps the configured role.
    assert gs.channel_id == 43 and gs.admin_role_id == ADMIN_ROLE_ID
    assert gs.post_turnout is False and gs.post_exit_polls is False and gs.odds_alerts is True
    assert gs.post_mode is PostMode.IMMEDIATE and gs.call_outlets == ["AP"]
    assert "NC-01" in gs.important

    # A member with neither keeps being refused.
    reply = await invoke(pause_command, make_interaction(client, "plain"))
    assert REFUSE_PLAIN in reply.content


async def test_setup_switching_admin_role(memory_store):
    client = stand_in_client(memory_store)
    await invoke(setup_command, make_interaction(client, "manage"), channel=FakeChannel(1),
                 admin_role=FakeRole(OTHER_ROLE_ID))
    # Everybody in make_user("plain") has OTHER_ROLE_ID, so plain now administers.
    reply = await invoke(pause_command, make_interaction(client, "plain"))
    assert reply.content == "Election posts paused."
    await invoke(setup_command, make_interaction(client, "manage"), channel=FakeChannel(1),
                 admin_role=FakeRole(ADMIN_ROLE_ID))
    reply = await invoke(resume_command, make_interaction(client, "plain"))
    assert REFUSE_PLAIN in reply.content


def test_change_setup_logic_rejects_missing_channel():
    gs = GuildSettings(1)
    for bad in (0, None):
        with pytest.raises(ValueError):
            change_setup(gs, bad)
    assert gs.channel_id is None


# --------------------------------------------------------------------------- /posting


@pytest.mark.parametrize("minutes", [None, 1, 30, 1440])
@pytest.mark.parametrize("mode", ["default", "interval", "immediate"])
async def test_posting_every_mode_and_minutes(memory_store, mode, minutes):
    await memory_store.save_guild(GuildSettings(GUILD_ID, interval_minutes=12))
    reply = await invoke(posting_command, make_interaction(stand_in_client(memory_store), "manage"),
                         mode=mode, minutes=minutes)
    gs = await memory_store.get_guild(GUILD_ID)
    assert gs.post_mode is PostMode(mode)
    if mode == "interval":
        expected = minutes or 12
        assert gs.interval_minutes == expected
        assert reply.content == f"Posting mode: interval every {expected} minutes"
    else:
        assert gs.interval_minutes == 12
        assert reply.content.startswith(f"Posting mode: {mode}")


@pytest.mark.parametrize("minutes", [0, -1, 1441, 10**9])
def test_change_posting_rejects_out_of_range_minutes_atomically(minutes):
    gs = GuildSettings(1, post_mode=PostMode.IMMEDIATE, interval_minutes=9)
    for mode in ("default", "interval", "immediate"):
        with pytest.raises(ValueError, match="1 and 1440"):
            change_posting(gs, mode, minutes)
    assert gs.post_mode is PostMode.IMMEDIATE and gs.interval_minutes == 9


@pytest.mark.parametrize("mode", ["", "fast", "intervals", "every", "None"])
def test_change_posting_rejects_unknown_mode(mode):
    gs = GuildSettings(1)
    with pytest.raises(ValueError, match="mode must be"):
        change_posting(gs, mode, 5)
    assert gs.post_mode is PostMode.DEFAULT and gs.interval_minutes == DEFAULT_INTERVAL_MIN


@pytest.mark.parametrize("mode", ["INTERVAL", "Immediate", "DeFaUlT"])
def test_change_posting_is_case_insensitive(mode):
    gs = GuildSettings(1)
    change_posting(gs, mode, 7)
    assert gs.post_mode is PostMode(mode.lower())


@pytest.mark.parametrize("minutes", [0, 1441])
async def test_posting_command_reports_out_of_range_minutes_and_saves_nothing(memory_store, minutes):
    await memory_store.save_guild(baseline())
    before = await guild_doc(memory_store)
    reply = await invoke(posting_command, make_interaction(stand_in_client(memory_store), "manage"),
                         mode="interval", minutes=minutes)
    assert "1440" in reply.content
    assert await guild_doc(memory_store) == before


def test_posting_mode_switching_keeps_interval_sane():
    gs = GuildSettings(1)
    change_posting(gs, "interval")
    assert gs.interval_minutes == DEFAULT_INTERVAL_MIN
    change_posting(gs, "interval", 30)
    for mode in ("immediate", "default", "immediate", "default"):
        change_posting(gs, mode)
        assert gs.interval_minutes == 30
    change_posting(gs, "interval")
    assert gs.post_mode is PostMode.INTERVAL and gs.interval_minutes == 30
    change_posting(gs, "default", 99)       # minutes are ignored outside interval mode
    assert gs.interval_minutes == 30
    change_posting(gs, "interval", 1)
    change_posting(gs, "interval")
    assert gs.interval_minutes == 1
    # A corrupt stored interval is repaired when interval mode is chosen without minutes.
    gs.interval_minutes = 0
    change_posting(gs, "interval")
    assert gs.interval_minutes == 1


def test_post_due_follows_each_mode():
    last = NOW - timedelta(minutes=3)
    assert post_due(GuildSettings(1, post_mode=PostMode.IMMEDIATE), last, NOW, True)
    assert not post_due(GuildSettings(1, post_mode=PostMode.IMMEDIATE), last, NOW, False)
    assert not post_due(GuildSettings(1), last, NOW, True)                # default = 5 min
    assert post_due(GuildSettings(1), NOW - timedelta(minutes=5), NOW, True)
    interval = GuildSettings(1, post_mode=PostMode.INTERVAL, interval_minutes=2)
    assert post_due(interval, last, NOW, True)
    interval.interval_minutes = 1440
    assert not post_due(interval, NOW - timedelta(minutes=1439), NOW, True)
    assert post_due(interval, None, NOW, True)
    assert not post_due(GuildSettings(1, paused=True, post_mode=PostMode.IMMEDIATE), None, NOW, True)


# --------------------------------------------------------------------------- /oddsalerts


@pytest.mark.parametrize("window", [None, 1, 1440])
@pytest.mark.parametrize("threshold", [None, 0.1, 50.0, 100.0])
@pytest.mark.parametrize("enabled", ["on", "off"])
async def test_oddsalerts_every_choice_and_boundary(memory_store, enabled, threshold, window):
    await memory_store.save_guild(GuildSettings(GUILD_ID, odds_swing_pts=7.0, odds_window_minutes=45))
    reply = await invoke(odds_alerts_command, make_interaction(stand_in_client(memory_store), "manage"),
                         enabled=enabled, threshold=threshold, window=window)
    gs = await memory_store.get_guild(GUILD_ID)
    assert gs.odds_alerts is (enabled == "on")
    assert gs.odds_swing_pts == (7.0 if threshold is None else threshold)
    assert gs.odds_window_minutes == (45 if window is None else window)
    assert reply.content.startswith(f"Odds alerts {enabled};")
    assert f"within {gs.odds_window_minutes} minutes" in reply.content


@pytest.mark.parametrize(
    "threshold, window",
    [(0, None), (-1, None), (100.01, None), (math.nan, None), (math.inf, None), (-math.inf, None),
     (None, 0), (None, -5), (None, 1441), (5.0, 1441), (0, 30)],
)
def test_change_odds_rejects_out_of_range_atomically(threshold, window):
    gs = GuildSettings(1, odds_alerts=False, odds_swing_pts=7.0, odds_window_minutes=45)
    with pytest.raises(ValueError):
        change_odds(gs, True, threshold, window)
    assert gs.odds_alerts is False and gs.odds_swing_pts == 7.0 and gs.odds_window_minutes == 45


@pytest.mark.parametrize("threshold, window", [(0, None), (None, 1441), (101.0, 10)])
async def test_oddsalerts_command_reports_out_of_range_and_saves_nothing(memory_store, threshold, window):
    await memory_store.save_guild(baseline())
    before = await guild_doc(memory_store)
    await invoke(odds_alerts_command, make_interaction(stand_in_client(memory_store), "manage"),
                 enabled="on", threshold=threshold, window=window)
    assert await guild_doc(memory_store) == before


# --------------------------------------------------------------------------- /calls

SEPARATOR_CASES = [
    ("ap,cnn", ["AP", "CNN"]),
    ("AP, CNN", ["AP", "CNN"]),
    ("ap  cnn", ["AP", "CNN"]),
    ("Ap,Cnn ,nbc", ["AP", "CNN", "NBC"]),
    ("AP,,CNN", ["AP", "CNN"]),
    (" ,ap, ", ["AP"]),
    ("AP ap Ap aP", ["AP"]),
    ("fox\tnbc", ["FOX", "NBC"]),
    ("votehub,ddhq,NYT", ["DDHQ", "NYT", "VOTEHUB"]),
]


@pytest.mark.parametrize("outlet", sorted(OUTLETS))
@pytest.mark.parametrize("spelling", [str.upper, str.lower, str.title])
async def test_calls_each_outlet_singly(memory_store, outlet, spelling):
    reply = await invoke(calls_command, make_interaction(stand_in_client(memory_store), "manage"),
                         outlets=spelling(outlet), enabled="on")
    gs = await memory_store.get_guild(GUILD_ID)
    assert gs.call_outlets == [outlet] and gs.post_calls is True
    assert outlet in reply.content


async def test_calls_all_outlets(memory_store):
    await invoke(calls_command, make_interaction(stand_in_client(memory_store), "manage"),
                 outlets=" ".join(sorted(OUTLETS, reverse=True)), enabled="on")
    assert (await memory_store.get_guild(GUILD_ID)).call_outlets == sorted(OUTLETS)


@pytest.mark.parametrize("text, expected", SEPARATOR_CASES)
def test_change_calls_separators(text, expected):
    gs = GuildSettings(1)
    change_calls(gs, text, True)
    assert gs.call_outlets == expected and gs.post_calls is True


@pytest.mark.parametrize("text", ["", "   ", ",", ", ,", "MSNBC", "AP MSNBC", "AP;CNN", "A P", "ÁP"])
async def test_calls_rejects_unknown_or_empty_and_saves_nothing(memory_store, text):
    await memory_store.save_guild(baseline())
    before = await guild_doc(memory_store)
    reply = await invoke(calls_command, make_interaction(stand_in_client(memory_store), "manage"),
                         outlets=text, enabled="on")
    assert reply.content.startswith("unknown outlets")
    assert await guild_doc(memory_store) == before


def test_calls_off_stops_only_the_named_outlets():
    gs = GuildSettings(1)
    others = [outlet for outlet in gs.call_outlets if outlet != "FOX"]
    message = change_calls(gs, "fox", False)
    assert sorted(gs.call_outlets) == sorted(others)
    assert gs.post_calls is True
    assert "FOX" in message
    # Turning off an outlet that was not on changes nothing.
    change_calls(gs, "CBS", False)
    assert sorted(gs.call_outlets) == sorted(others)
    # Turning off every remaining outlet switches race calls off.
    change_calls(gs, " ".join(others), False)
    assert gs.post_calls is False and gs.call_outlets == []
    # On sets exactly the listed outlets again.
    change_calls(gs, "ap cnn", True)
    assert gs.post_calls is True and gs.call_outlets == ["AP", "CNN"]


@pytest.mark.parametrize("enabled", ["on", "off"])
async def test_calls_on_off_messages_are_ephemeral_and_persist(store, enabled):
    await store.save_guild(GuildSettings(GUILD_ID))
    reply = await invoke(calls_command, make_interaction(stand_in_client(store), "manage"),
                         outlets="AP", enabled=enabled)
    gs = await store.get_guild(GUILD_ID)
    assert ("AP" in gs.call_outlets) is (enabled == "on")
    assert "AP" in reply.content


# --------------------------------------------------------------------------- /watch

VALID_KEYS = [
    ("TX SEN", "TX SEN"), ("tx sen", "TX SEN"), ("Tx Sen", "TX SEN"), ("TX-SEN", "TX SEN"),
    ("  TX SEN  ", "TX SEN"), ("Texas Senate", "TX SEN"), ("texas u.s. senate", "TX SEN"),
    ("NC-01", "NC-01"), ("NC-1", "NC-01"), ("nc-1", "NC-01"), ("nc-01", "NC-01"),
    ("North Carolina Congressional District 1", "NC-01"), ("north carolina house district 12", "NC-12"),
    ("AK-AL", "AK-AL"), ("ak-al", "AK-AL"), ("AK-01", "AK-AL"), ("AK-1", "AK-AL"), ("WY-AL", "WY-AL"),
    ("PR-01", "PR-AL"), ("MT-01", "MT-01"),
    ("OH SEN S", "OH SEN S"), ("OH SEN SPECIAL", "OH SEN S"), ("oh sen special", "OH SEN S"),
    ("Ohio Senate Special", "OH SEN S"), ("NC-01 S", "NC-01 S"), ("FL GOV S", "FL GOV S"),
    ("GA GOV", "GA GOV"), ("Georgia Governor", "GA GOV"), ("Georgia Governor race", "GA GOV"),
    ("TX SD-05", "TX SD-05"), ("TX SD 5", "TX SD-05"), ("tx sd5", "TX SD-05"), ("TX HD-121", "TX HD-121"),
    ("AK SD-A", "AK SD-A"), ("VT HD-CHT-6-7", "VT HD-CHT-6-7"), ("NH HD-12A", "NH HD-12A"),
    ("TX LOCAL harris-county-judge", "TX LOCAL harris-county-judge"),
    ("TX LOCAL Harris County Judge", "TX LOCAL harris-county-judge"),
    ("DC-AL", "DC-AL"), ("DC SEN", "DC SEN"), ("District of Columbia Senate", "DC SEN"),
    ("NC-99", "NC-99"),     # districts are not range-checked by racekeys.parse (contract)
] + [(f"{code} {office}", f"{code} {office}") for code in ("TX", "ca") for office in STATEWIDE] \
  + [(f"{code} SEN", f"{code} SEN") for code in STATES] \
  + [(f"{code}-AL", f"{code}-AL") for code in TERRITORIES] \
  + [(f"{code.lower()} gov", f"{code} GOV") for code in TERRITORIES] \
  + [("Puerto Rico Governor", "PR GOV"), ("Guam Governor", "GU GOV")]
VALID_KEYS = [(text, expected.replace("ca ", "CA ")) for text, expected in VALID_KEYS]

INVALID_KEYS = [
    "garbage", "ZZ SEN", "zz-01", "TX", "TX XYZ", "TX SEN EXTRA", "TX SEN S S", "NC-100", "NC-001",
    "TX LOCAL", "TX SD", "nc 01", "NC 1", "Texas", "Narnia Senate", "ＴＸ SEN", "🇺🇸 SEN", "Техас SEN",
    "TX​SEN", "A" * 500, "TX SEN " + "x" * 500, "TX LOCAL " + "x" * 500,
    "TX LOCAL " + "y" * 3000, "TX HD-" + "9" * 500, "\x00", "<@everyone> SEN",
]


@pytest.mark.parametrize("text, expected", VALID_KEYS, ids=[t for t, _ in VALID_KEYS])
async def test_watch_add_accepts_every_key_format(memory_store, text, expected):
    await memory_store.save_guild(GuildSettings(GUILD_ID, important=["GA GOV"]))
    reply = await invoke(watch_command, make_interaction(stand_in_client(memory_store), "manage"),
                         action="add", race=text)
    assert reply.content == f"Watching {expected}"
    assert (await memory_store.get_guild(GUILD_ID)).important[-1] == expected


@pytest.mark.parametrize("text", INVALID_KEYS, ids=[t[:24] for t in INVALID_KEYS])
@pytest.mark.parametrize("action", ["add", "remove"])
async def test_watch_rejects_invalid_keys_and_saves_nothing(memory_store, text, action):
    await memory_store.save_guild(baseline())
    before = await guild_doc(memory_store)
    reply = await invoke(watch_command, make_interaction(stand_in_client(memory_store), "manage"),
                         action=action, race=text)
    assert not reply.content.startswith(("Watching", "Removed"))
    assert await guild_doc(memory_store) == before


@pytest.mark.parametrize("race", [None, ""])
@pytest.mark.parametrize("action", ["add", "remove"])
async def test_watch_add_remove_require_a_race(memory_store, action, race):
    await memory_store.save_guild(baseline())
    before = await guild_doc(memory_store)
    reply = await invoke(watch_command, make_interaction(stand_in_client(memory_store), "manage"),
                         action=action, race=race)
    assert reply.content == "race is required"
    assert await guild_doc(memory_store) == before


def test_change_watch_rejects_unknown_action():
    gs = GuildSettings(1)
    with pytest.raises(ValueError):
        change_watch(gs, "toggle", "TX SEN")
    assert change_watch(gs, "LIST").startswith("Watched races:")


async def test_watch_duplicates_and_equivalent_spellings_are_stored_once(store):
    client = stand_in_client(store)
    for spelling in ("NC-1", "nc-01", "NC-01", "North Carolina Congressional District 1"):
        reply = await invoke(watch_command, make_interaction(client, "manage"), action="add", race=spelling)
        assert reply.content == "Watching NC-01"
    assert (await store.get_guild(GUILD_ID)).important == DEFAULT_KEYS + ["NC-01"]


async def test_watch_remove_last_race_falls_back_to_defaults(store):
    client = stand_in_client(store)
    await store.save_guild(GuildSettings(GUILD_ID, important=["NC-01"]))
    reply = await invoke(watch_command, make_interaction(client, "manage"), action="remove", race="NC-1")
    assert reply.content == "Removed NC-01; using default races"
    assert (await store.get_guild(GUILD_ID)).important == []
    reply = await invoke(watch_command, make_interaction(client, "plain"), action="list")
    assert reply.content == "Watched races: " + ", ".join(DEFAULT_KEYS)


async def test_watch_remove_a_race_that_is_not_watched_changes_nothing(store):
    client = stand_in_client(store)
    await store.save_guild(GuildSettings(GUILD_ID, important=["GA GOV"]))
    reply = await invoke(watch_command, make_interaction(client, "manage"), action="remove", race="NC-01")
    assert "not watched" in reply.content and "NC-01" in reply.content
    assert (await store.get_guild(GUILD_ID)).important == ["GA GOV"]
    # On default races the list is not materialized by a no-op remove either.
    await store.save_guild(GuildSettings(GUILD_ID))
    await invoke(watch_command, make_interaction(client, "manage"), action="remove", race="NC-01")
    assert (await store.get_guild(GUILD_ID)).important == []


async def test_watch_list_with_500_races_fits_one_message(memory_store):
    keys = [f"TX HD-{n:03d}" for n in range(1, 151)] + [f"CA SD-{n:02d}" for n in range(1, 41)] \
        + [f"{code}-{n:02d}" for code in ("CA", "TX", "FL", "NY", "PA", "IL", "OH", "GA") for n in range(1, 39)] \
        + [f"TX LOCAL county-{n}" for n in range(6)]
    keys = keys[:500]
    assert len(keys) == 500
    await memory_store.save_guild(GuildSettings(GUILD_ID, important=keys))
    reply = await invoke(watch_command, make_interaction(stand_in_client(memory_store), "plain"), action="list")
    assert len(reply.content) <= DISCORD_LIMIT
    assert reply.content.endswith("(500 total)")
    assert reply.content.startswith("Watched races: TX HD-001, TX HD-002")


async def test_watch_list_of_long_local_slugs_fits_one_message(memory_store):
    keys = [f"TX LOCAL {'a' * 90}-{n}" for n in range(60)]
    await memory_store.save_guild(GuildSettings(GUILD_ID, important=keys))
    reply = await invoke(watch_command, make_interaction(stand_in_client(memory_store), "plain"), action="list")
    assert reply.content.endswith("(60 total)")


def test_change_watch_list_edge_cases():
    assert change_watch(GuildSettings(1), "list") == "Watched races: " + ", ".join(DEFAULT_KEYS)
    # One giant entry still clips below the limit.
    text = change_watch(GuildSettings(1, important=["X" * 3000]), "list")
    assert len(text) <= DISCORD_LIMIT


# --------------------------------------------------------------------------- toggles


@pytest.mark.parametrize(
    "command, field, label",
    [(turnout_command, "post_turnout", "turnout"), (exitpolls_command, "post_exit_polls", "exit polls")],
)
@pytest.mark.parametrize("sequence", [("on",), ("off",), ("on", "on"), ("off", "off"), ("off", "on"), ("on", "off")])
async def test_turnout_and_exitpolls_on_off(store, command, field, label, sequence):
    client = stand_in_client(store)
    for value in sequence:
        reply = await invoke(command, make_interaction(client, "manage"), enabled=value)
        assert reply.content == f"{label} {value}"
        assert getattr(await store.get_guild(GUILD_ID), field) is (value == "on")


@pytest.mark.parametrize("sequence", [
    ("pause",), ("resume",), ("pause", "pause"), ("resume", "resume"),
    ("pause", "resume"), ("resume", "pause", "pause"), ("pause", "resume", "resume"),
])
async def test_pause_resume_idempotent_as_manager(store, sequence):
    client = stand_in_client(store)
    messages = {"pause": "Election posts paused.", "resume": "Election posts resumed."}
    commands = {"pause": pause_command, "resume": resume_command}
    for name in sequence:
        reply = await invoke(commands[name], make_interaction(client, "manage"))
        assert reply.content == messages[name]
        assert (await store.get_guild(GUILD_ID)).paused is (name == "pause")


# --------------------------------------------------------------------------- /settings


@pytest.mark.parametrize("gs", [
    GuildSettings(GUILD_ID),
    baseline(),
    GuildSettings(GUILD_ID, important=[f"TX HD-{n:03d}" for n in range(1, 151)], paused=True,
                  post_mode=PostMode.INTERVAL, interval_minutes=1440),
    GuildSettings(GUILD_ID, important=[f"TX HD-{n:03d}" for n in range(1, 151)] * 4, paused=True,
                  post_mode=PostMode.IMMEDIATE),
    GuildSettings(GUILD_ID, important=[f"TX LOCAL {'z' * 90}-{n}" for n in range(500)], paused=True),
    GuildSettings(GUILD_ID, call_outlets=sorted(OUTLETS), odds_swing_pts=0.1, odds_window_minutes=1440),
], ids=["default", "baseline", "150 races", "600 races", "500 long local races", "all outlets"])
async def test_settings_is_valid_yaml_and_fits_one_message(store, gs):
    await store.save_guild(gs)
    reply = await invoke(settings_command, make_interaction(stand_in_client(store), "plain"))
    assert len(reply.content) <= DISCORD_LIMIT
    data = yaml_block(reply.content)
    assert isinstance(data, dict)
    # The scalar settings must survive any truncation of the watch list.
    assert data["guild_id"] == GUILD_ID
    assert data["post_mode"] == gs.post_mode.value
    assert data["paused"] is gs.paused
    assert data["interval_minutes"] == gs.interval_minutes
    assert data["post_calls"] is gs.post_calls and data["odds_alerts"] is gs.odds_alerts
    assert data["call_outlets"] == gs.call_outlets
    shown = data.get("important") or []
    assert shown == gs.important[: len(shown)]


async def test_settings_for_a_guild_never_configured(memory_store):
    reply = await invoke(settings_command, make_interaction(stand_in_client(memory_store), "plain"))
    data = yaml_block(reply.content)
    assert data["guild_id"] == GUILD_ID and data["channel_id"] is None and data["post_mode"] == "default"
    assert await memory_store.get_guild(GUILD_ID) is None


def test_settings_dump_round_trips_every_field():
    gs = baseline()
    assert yaml.safe_load(settings_dump(gs)) == to_doc(gs)


# --------------------------------------------------------------------------- /favorite


@pytest.mark.parametrize("role", ROLES)
async def test_favorite_add_remove_duplicate(store, role):
    client = stand_in_client(store)
    for text in ("TX SEN", "tx sen", "Texas Senate"):
        reply = await invoke(favorite_command, make_interaction(client, role), action="add", race=text)
        assert reply.content == "Favorited TX SEN"
    await invoke(favorite_command, make_interaction(client, role), action="add", race="nc-1")
    assert (await store.get_user(501)).favorites == ["TX SEN", "NC-01"]
    reply = await invoke(favorite_command, make_interaction(client, role), action="remove", race="TX-SEN")
    assert reply.content == "Removed favorite TX SEN"
    assert (await store.get_user(501)).favorites == ["NC-01"]
    reply = await invoke(favorite_command, make_interaction(client, role), action="remove", race="GA GOV")
    assert (await store.get_user(501)).favorites == ["NC-01"]
    assert "GA GOV" in reply.content
    # Favorites never touch guild settings.
    assert await store.get_guild(GUILD_ID) is None


@pytest.mark.parametrize("text", INVALID_KEYS + [""], ids=[t[:24] for t in INVALID_KEYS + [""]])
async def test_favorite_invalid_keys_save_nothing(memory_store, text):
    await memory_store.save_user(UserSettings(501, favorites=["GA GOV"]))
    reply = await invoke(favorite_command, make_interaction(stand_in_client(memory_store), "dm"),
                         action="add", race=text)
    assert not reply.content.startswith("Favorited")
    assert (await memory_store.get_user(501)).favorites == ["GA GOV"]


def test_change_favorite_rejects_unknown_action():
    us = UserSettings(1)
    with pytest.raises(ValueError):
        change_favorite(us, "toggle", "TX SEN")
    assert us.favorites == []


async def test_favorites_are_per_user(store):
    client = stand_in_client(store)
    await invoke(favorite_command, make_interaction(client, "plain", user_id=1), action="add", race="TX SEN")
    await invoke(favorite_command, make_interaction(client, "dm", user_id=2), action="add", race="GA GOV")
    await invoke(favorite_command, make_interaction(client, "manage", user_id=2), action="add", race="NC-01")
    await invoke(favorite_command, make_interaction(client, "plain", user_id=1), action="remove", race="GA GOV")
    assert (await store.get_user(1)).favorites == ["TX SEN"]
    assert (await store.get_user(2)).favorites == ["GA GOV", "NC-01"]
    assert await store.get_user(3) is None


# --------------------------------------------------------------------------- persistence


async def test_every_setting_round_trips_through_the_store(store):
    client = stand_in_client(store)
    steps = [
        (setup_command, {"channel": FakeChannel(77), "admin_role": FakeRole(ADMIN_ROLE_ID)}),
        (watch_command, {"action": "add", "race": "NC-1"}),
        (watch_command, {"action": "add", "race": "OH SEN SPECIAL"}),
        (watch_command, {"action": "remove", "race": "TX SEN"}),
        (posting_command, {"mode": "interval", "minutes": 1440}),
        (calls_command, {"outlets": "nyt, ddhq", "enabled": "on"}),
        (odds_alerts_command, {"enabled": "on", "threshold": 0.1, "window": 1}),
        (turnout_command, {"enabled": "off"}),
        (exitpolls_command, {"enabled": "off"}),
        (pause_command, {}),
    ]
    for command, kwargs in steps:
        reply = await invoke(command, make_interaction(client, "manage"), **kwargs)
        assert REFUSE_PLAIN not in reply.content
    await invoke(favorite_command, make_interaction(client, "dm"), action="add", race="AK-AL")

    gs = await store.get_guild(GUILD_ID)
    assert gs == GuildSettings(
        GUILD_ID, channel_id=77, admin_role_id=ADMIN_ROLE_ID, important=["NC-01", "OH SEN S"],
        post_mode=PostMode.INTERVAL, interval_minutes=1440, post_calls=True,
        call_outlets=["DDHQ", "NYT"], post_turnout=False, post_exit_polls=False, odds_alerts=True,
        odds_swing_pts=0.1, odds_window_minutes=1, paused=True,
    )
    assert isinstance(gs.post_mode, PostMode)
    assert await store.all_guilds() == [gs]
    assert (await store.get_user(501)).favorites == ["AK-AL"]
    reply = await invoke(settings_command, make_interaction(client, "plain"))
    assert yaml_block(reply.content) == to_doc(gs)


async def test_store_returns_copies_not_live_objects(store):
    """A command mutating its copy without saving must not leak into the store."""
    await store.save_guild(baseline())
    interaction = make_interaction(stand_in_client(store), "manage")
    gs = await _guild(interaction, admin=True)
    gs.important.append("NC-01")
    gs.paused = False
    assert (await store.get_guild(GUILD_ID)).important == ["TX SEN", "GA GOV"]


# --------------------------------------------------------------------------- helpers + errors


async def test_guild_helper_defaults_and_refusals(memory_store):
    client = stand_in_client(memory_store)
    gs = await _guild(make_interaction(client, "plain"))
    assert gs == GuildSettings(GUILD_ID)
    interaction = make_interaction(client, "plain")
    assert await _guild(interaction, admin=True) is None
    assert interaction.replies[0].ephemeral and REFUSE_PLAIN in interaction.text
    interaction = make_interaction(client, "dm")
    assert await _guild(interaction) is None
    assert interaction.replies[0].ephemeral and REFUSE_DM in interaction.text


async def test_user_helper_defaults(memory_store):
    client = stand_in_client(memory_store)
    assert await _user(make_interaction(client, "dm", user_id=9)) == UserSettings(9)
    await memory_store.save_user(UserSettings(9, favorites=["TX SEN"]))
    assert (await _user(make_interaction(client, "plain", user_id=9))).favorites == ["TX SEN"]


@pytest.mark.parametrize("length", [0, 1, 1999, 2000, 2001, 6000])
async def test_error_helper_is_ephemeral_and_fits(length):
    interaction = make_interaction(stand_in_client(MemoryStore()), "plain")
    await _error(interaction, ValueError("e" * length))
    assert len(interaction.replies) == 1 and interaction.replies[0].ephemeral
    assert len(interaction.replies[0].content) <= DISCORD_LIMIT


@pytest.mark.parametrize("done", [False, True])
async def test_error_handler_answers_once(done, caplog):
    interaction = make_interaction(stand_in_client(MemoryStore()), "plain", command="watch")
    if done:
        await interaction.response.defer(ephemeral=True)
    with caplog.at_level(logging.ERROR):
        await handle_app_command_error(interaction, RuntimeError("mongodb://user:hunter2@host"))
    assert len(interaction.replies) == 1
    reply = interaction.replies[0]
    assert reply.via == ("followup" if done else "response") and reply.ephemeral
    assert "hunter2" not in reply.content
    assert "/watch failed" in caplog.text
    # The log line names the error type only; the message (which may hold a URI) is in the traceback.
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("done", [False, True])
async def test_error_handler_survives_http_exception_while_reporting(done, caplog):
    interaction = make_interaction(stand_in_client(MemoryStore()), "plain", command="pause")
    if done:
        await interaction.response.defer(ephemeral=True)
        interaction.followup.fail = http_exception(404, "Unknown interaction")
    else:
        interaction.response.fail = http_exception(404, "Unknown interaction")
    with caplog.at_level(logging.ERROR):
        await handle_app_command_error(interaction, RuntimeError("boom"))
    assert interaction.replies == []
    assert "Could not tell the user that /pause failed" in caplog.text


async def test_error_handler_without_command_attribute():
    class Bare:
        pass

    interaction = make_interaction(stand_in_client(MemoryStore()), "plain")
    bare = Bare()
    bare.response, bare.followup = interaction.response, interaction.followup
    await handle_app_command_error(bare, RuntimeError("x"))
    assert len(interaction.replies) == 1


class _FailingStore(MemoryStore):
    async def save_guild(self, gs):
        raise ConnectionError("mongodb://u:secret@db down")


@pytest.mark.parametrize("command, kwargs", [
    (setup_command, {"channel": FakeChannel(1)}), (watch_command, {"action": "add", "race": "NC-01"}),
    (posting_command, {"mode": "immediate"}), (calls_command, {"outlets": "AP", "enabled": "on"}),
    (odds_alerts_command, {"enabled": "on"}), (turnout_command, {"enabled": "on"}),
    (exitpolls_command, {"enabled": "on"}), (pause_command, {}), (resume_command, {}),
])
async def test_store_failure_propagates_to_the_tree_error_handler(command, kwargs):
    interaction = make_interaction(stand_in_client(_FailingStore()), "manage", command=command.name)
    with pytest.raises(ConnectionError) as info:
        await command.callback(interaction, **kwargs)
    assert interaction.sent == []           # nothing sent yet, so the handler can respond
    await handle_app_command_error(interaction, info.value)
    assert len(interaction.replies) == 1 and interaction.replies[0].ephemeral
    assert "secret" not in interaction.text


# --------------------------------------------------------------------------- poller interplay

TX = parse("TX SEN")
NC = parse("NC-01")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


class Results(ResultsSource):
    name = "admin_results"
    states = frozenset({"TX", "NC"})

    def __init__(self) -> None:
        self.votes = 1_000

    async def fetch(self, keys, client):
        return [
            RaceResult(
                key,
                [CandidateResult("Alex Red", "REP", self.votes), CandidateResult("Bo Blue", "DEM", 900)],
                self.name,
                NOW,
                total_votes=self.votes + 900,
                registered_voters=10_000,
                turnout_pct=round((self.votes + 900) / 100, 1),
            ).finalize()
            for key in keys
            if key in (TX, NC)
        ]


class Calls(CallSource):
    name = "admin_calls"
    outlet = "AP"

    def __init__(self, calls) -> None:
        self.calls = calls

    async def fetch_calls(self, keys, client):
        return [call for call in self.calls if call.key in keys]


class Exits(ExitPollSource):
    name = "admin_exits"

    async def fetch_exit_polls(self, keys, client):
        return [ExitPoll(key, "CNN", "https://example.test/exit", "Men: 55% Red") for key in keys if key == TX]


def _call(key, outlet, winner="Alex Red"):
    return RaceCall(key, outlet, winner, "REP", NOW)


class Harness:
    def __init__(self, store, calls=()) -> None:
        self.store = store
        self.client = stand_in_client(store)
        self.clock = Clock()
        self.results = Results()
        self.poster = RecordingPoster()
        self.sources = {"results": [self.results], "calls": [Calls(list(calls))], "exitpolls": [Exits()]}
        self.poller = Poller(store, self.sources, self.poster, self.clock, object())

    async def run(self, command, **kwargs):
        reply = await invoke(command, make_interaction(self.client, "manage"), **kwargs)
        assert REFUSE_PLAIN not in reply.content
        return reply

    async def tick(self):
        start = len(self.poster.sent)
        await self.poller.tick()
        return [post for _gid, _cid, post in self.poster.sent[start:]]


def kinds(posts):
    return sorted((post.kind, post.race) for post in posts)


async def test_poller_without_setup_posts_nothing(store):
    harness = Harness(store, [_call(TX, "AP")])
    await harness.run(watch_command, action="add", race="NC-01")
    assert await harness.tick() == []


async def test_poller_pause_and_resume(store):
    harness = Harness(store, [_call(TX, "AP")])
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(posting_command, mode="immediate")
    await harness.run(pause_command)
    assert await harness.tick() == []
    harness.results.votes += 1
    harness.clock.advance(1)
    assert await harness.tick() == []
    await harness.run(pause_command)   # idempotent
    assert await harness.tick() == []

    await harness.run(resume_command)
    harness.results.votes += 1
    harness.clock.advance(1)
    posts = await harness.tick()
    # The AP call arrived while paused: it was recorded then and is not replayed afterwards.
    assert kinds(posts) == [("exitpoll", "TX SEN"), ("results", "TX SEN")]
    assert all(channel == 10 for _gid, channel, _post in harness.poster.sent)


async def test_poller_posting_modes(store):
    harness = Harness(store)
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(exitpolls_command, enabled="off")

    async def bump_and_tick(minutes):
        harness.results.votes += 1
        harness.clock.advance(minutes)
        return [post for post in await harness.tick() if post.kind == "results"]

    assert len(await harness.tick()) == 1                       # first post is always due
    assert await bump_and_tick(1) == []                         # default: 5 minutes
    assert len(await bump_and_tick(4)) == 1

    await harness.run(posting_command, mode="immediate")
    assert len(await bump_and_tick(1)) == 1
    assert len(await bump_and_tick(1)) == 1
    harness.clock.advance(1)
    assert await harness.tick() == []                           # unchanged results never post

    await harness.run(posting_command, mode="interval", minutes=15)
    assert await bump_and_tick(10) == []
    assert len(await bump_and_tick(5)) == 1
    await harness.run(posting_command, mode="interval", minutes=1)
    assert len(await bump_and_tick(1)) == 1


async def test_poller_calls_toggle_and_outlet_filter(store):
    calls = [_call(TX, "AP"), _call(TX, "CNN")]
    harness = Harness(store, calls)
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(calls_command, outlets="CNN", enabled="on")
    posts = [post for post in await harness.tick() if post.kind == "call"]
    assert [line for post in posts for line in post.lines if line.startswith("Outlet")] == ["Outlet: CNN"]

    # /calls off: a new CNN call (changed winner) is not posted.
    harness.sources["calls"][0].calls = [_call(TX, "CNN", "Bo Blue")]
    await harness.run(calls_command, outlets="CNN", enabled="off")
    assert [post for post in await harness.tick() if post.kind == "call"] == []

    # Back on for AP only: AP's new call posts, CNN's does not.
    harness.sources["calls"][0].calls = [_call(TX, "AP", "Bo Blue"), _call(TX, "CNN", "Alex Red")]
    await harness.run(calls_command, outlets="ap", enabled="on")
    posts = [post for post in await harness.tick() if post.kind == "call"]
    assert [line for post in posts for line in post.lines if line.startswith("Outlet")] == ["Outlet: AP"]


async def test_poller_turnout_toggle(store):
    harness = Harness(store)
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(posting_command, mode="immediate")
    posts = [post for post in await harness.tick() if post.kind == "results"]
    assert any(line.startswith("Turnout:") for line in posts[0].lines)
    await harness.run(turnout_command, enabled="off")
    harness.results.votes += 1
    harness.clock.advance(1)
    posts = [post for post in await harness.tick() if post.kind == "results"]
    assert posts and not any(line.startswith("Turnout:") for line in posts[0].lines)


async def test_poller_exitpolls_toggle(store):
    harness = Harness(store)
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(exitpolls_command, enabled="off")
    assert [post for post in await harness.tick() if post.kind == "exitpoll"] == []
    await harness.run(exitpolls_command, enabled="on")
    harness.clock.advance(1)
    assert kinds(post for post in await harness.tick() if post.kind == "exitpoll") == [("exitpoll", "TX SEN")]
    harness.clock.advance(1)
    assert [post for post in await harness.tick() if post.kind == "exitpoll"] == []   # once per outlet


async def test_poller_watch_changes_which_races_post(store):
    harness = Harness(store, [_call(NC, "AP")])
    await harness.run(setup_command, channel=FakeChannel(10))
    await harness.run(posting_command, mode="immediate")
    await harness.run(exitpolls_command, enabled="off")
    assert kinds(await harness.tick()) == [("results", "TX SEN")]          # defaults only

    await harness.run(watch_command, action="add", race="nc-1")
    harness.clock.advance(1)
    assert kinds(await harness.tick()) == [("call", "NC-01"), ("results", "NC-01")]

    await harness.run(watch_command, action="remove", race="TX SEN")
    harness.results.votes += 1
    harness.clock.advance(1)
    assert kinds(await harness.tick()) == [("results", "NC-01")]


# --------------------------------------------------------------------------- real ElectionBot


async def test_commands_with_a_real_election_bot():
    async with httpx.AsyncClient() as http:
        bot = ElectionBot(MemoryStore(), {}, http)
        try:
            names = {command.name for command in bot.tree.get_commands()}
            for command in (setup_command, watch_command, posting_command, calls_command,
                            odds_alerts_command, turnout_command, exitpolls_command, pause_command,
                            resume_command, settings_command, favorite_command):
                assert command.name in names
            await invoke(setup_command, FakeInteraction(bot, FakeMember(1, manage_guild=True)),
                         channel=FakeChannel(5))
            await invoke(pause_command, FakeInteraction(bot, FakeMember(1, manage_guild=True)))
            reply = await invoke(settings_command, FakeInteraction(bot, FakeMember(2)))
            assert yaml_block(reply.content)["paused"] is True
            await invoke(favorite_command, FakeInteraction(bot, FakeUser(3), None), action="add", race="TX SEN")
            assert (await bot.store.get_user(3)).favorites == ["TX SEN"]
            assert bot.poller.store is bot.store
        finally:
            await bot.close()


def test_command_parameter_ranges_match_logic_limits():
    """The Discord-side Range bounds must equal the logic-side validation bounds."""
    params = {p.name: p for p in bot_discord.posting_command.parameters}
    assert (params["minutes"].min_value, params["minutes"].max_value) == (1, 1440)
    assert not params["minutes"].required
    params = {p.name: p for p in bot_discord.odds_alerts_command.parameters}
    assert (params["threshold"].min_value, params["threshold"].max_value) == (0.1, 100.0)
    assert (params["window"].min_value, params["window"].max_value) == (1, 1440)
    choices = {p.name: [c.value for c in p.choices] for p in bot_discord.posting_command.parameters if p.choices}
    assert choices == {"mode": ["default", "interval", "immediate"]}
    for command in (bot_discord.calls_command, bot_discord.odds_alerts_command,
                    bot_discord.turnout_command, bot_discord.exitpolls_command):
        enabled = next(p for p in command.parameters if p.name == "enabled")
        assert [c.value for c in enabled.choices] == ["on", "off"]
    action = next(p for p in bot_discord.watch_command.parameters if p.name == "action")
    assert [c.value for c in action.choices] == ["add", "remove", "list"]
    action = next(p for p in bot_discord.favorite_command.parameters if p.name == "action")
    assert [c.value for c in action.choices] == ["add", "remove"]
