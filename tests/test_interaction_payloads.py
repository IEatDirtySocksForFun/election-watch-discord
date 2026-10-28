"""Raw interaction payloads, produced by real discord.py objects.

Fake ``interaction.response`` objects can only say *that* ``ephemeral=True`` was passed.
These tests feed gateway-shaped INTERACTION_CREATE payloads into the bot's real
``ConnectionState.parse_interaction_create`` -> ``CommandTree`` / ``ViewStore`` dispatch
(option parsing, Literal/Range transformers, resolved Role/TextChannel objects, the error
handler) and record the HTTP requests discord.py would send. Assertions are on the JSON body
Discord would receive: response ``type`` and ``flags & 64`` (EPHEMERAL).

No network: the webhook adapter used for interaction callbacks/followups is replaced by a
recorder (``discord.webhook.async_.async_context``).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import discord
import httpx
import pytest
from discord.webhook.async_ import AsyncWebhookAdapter, async_context
from fake_civicapi_early_vote import fixture_client, mock_http_client

from electionwatch.bot.discord import ElectionBot
from electionwatch.models import (
    CandidateResult,
    Level,
    OddsSnapshot,
    Poll,
    PollResult,
    RaceKey,
    RaceResult,
)
from electionwatch.store.memory import MemoryStore

GUILD = 100000000000000001
CHANNEL = 100000000000000002
APP = 424242424242424242
OWNER = 111111111111111111
STRANGER = 222222222222222222
ROLE = 333333333333333333
TARGET_CHANNEL = 444444444444444444
MSG_ID = 555555555555555555
MANAGE_GUILD = 1 << 5
MOD_ROLE = 666666666666666666  # cached guild role carrying Manage Server
EPHEMERAL = 64

CALLBACK = "/interactions/{webhook_id}/{webhook_token}/callback"
FOLLOWUP = "/webhooks/{webhook_id}/{webhook_token}"
ORIGINAL = "/webhooks/{webhook_id}/{webhook_token}/messages/@original"

# Interaction callback types (https://discord.com/developers/docs/interactions/receiving-and-responding)
CHANNEL_MESSAGE_WITH_SOURCE = 4
DEFERRED_CHANNEL_MESSAGE = 5
DEFERRED_UPDATE_MESSAGE = 6
UPDATE_MESSAGE = 7
AUTOCOMPLETE_RESULT = 8

_ids = itertools.count(900000000000000000)


# --------------------------------------------------------------------------- recorder


@dataclass
class Sent:
    method: str
    path: str
    payload: dict | None


class RecordingAdapter(AsyncWebhookAdapter):
    """Captures the route + JSON body of every interaction HTTP call instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Sent] = []

    async def request(self, route, session, *, payload=None, multipart=None, files=None, **_):
        if payload is None and multipart:
            for part in multipart:
                if part.get("name") == "payload_json":
                    payload = json.loads(part["value"])
        # Must survive JSON serialisation exactly as discord.py would send it.
        payload = json.loads(json.dumps(payload)) if payload is not None else None
        self.sent.append(Sent(route.method, route.path, payload))
        if route.path == CALLBACK:
            return {"interaction": {"id": str(route.webhook_id), "type": 2}}
        if route.path in (FOLLOWUP, ORIGINAL) and route.method in ("POST", "PATCH"):
            return _message_payload((payload or {}).get("flags", 0) or 0)
        return None


def _user(user_id: int) -> dict:
    return {
        "id": str(user_id),
        "username": f"user{user_id % 1000}",
        "discriminator": "0",
        "global_name": None,
        "avatar": None,
    }


def _member(user_id: int, permissions: int) -> dict:
    # discord.py's Member.guild_permissions (what is_admin reads) is computed from the
    # CACHED guild roles, not from the interaction's "permissions" field: give admins the
    # cached Manage Server role as well.
    roles = [str(MOD_ROLE)] if permissions & MANAGE_GUILD else []
    return {
        "user": _user(user_id),
        "roles": roles,
        "joined_at": "2024-01-01T00:00:00+00:00",
        "deaf": False,
        "mute": False,
        "flags": 0,
        "permissions": str(permissions),
    }


def _message_payload(flags: int = EPHEMERAL) -> dict:
    return {
        "id": str(MSG_ID),
        "channel_id": str(CHANNEL),
        "author": _user(APP),
        "content": "",
        "embeds": [],
        "attachments": [],
        "mentions": [],
        "mention_roles": [],
        "pinned": False,
        "tts": False,
        "type": 20,
        "timestamp": "2026-11-03T20:00:00+00:00",
        "edited_timestamp": None,
        "flags": flags,
        "components": [],
        "application_id": str(APP),
        "webhook_id": str(APP),
    }


def _role(role_id: int, name: str, permissions: int, position: int) -> dict:
    return {
        "id": str(role_id),
        "name": name,
        "color": 0,
        "hoist": False,
        "position": position,
        "permissions": str(permissions),
        "managed": False,
        "mentionable": False,
        "flags": 0,
    }


def _text_channel(channel_id: int, name: str) -> dict:
    return {
        "id": str(channel_id),
        "type": 0,
        "guild_id": str(GUILD),
        "name": name,
        "position": 0,
        "permission_overwrites": [],
        "nsfw": False,
        "parent_id": None,
    }


GUILD_CREATE = {
    # What the gateway delivers at startup with the default (guilds) intent.
    "id": str(GUILD),
    "name": "Test guild",
    "owner_id": "1",
    "member_count": 3,
    "features": [],
    "emojis": [],
    "stickers": [],
    "members": [],
    "threads": [],
    "presences": [],
    "voice_states": [],
    "roles": [
        _role(GUILD, "@everyone", 0, 0),
        _role(MOD_ROLE, "Mods", MANAGE_GUILD, 2),
        _role(ROLE, "Election admins", 0, 1),
    ],
    "channels": [_text_channel(CHANNEL, "general"), _text_channel(TARGET_CHANNEL, "elections")],
}


def _base(itype: int, *, user_id: int = OWNER, permissions: int = MANAGE_GUILD, dm: bool = False) -> dict:
    payload = {
        "id": str(next(_ids)),
        "application_id": str(APP),
        "type": itype,
        "token": "interaction-token",
        "version": 1,
        "attachment_size_limit": 8 * 1024 * 1024,
        "locale": "en-US",
        "app_permissions": "0",
        "entitlements": [],
        "authorizing_integration_owners": {},
    }
    if dm:
        payload["channel"] = {"id": "1", "type": 1, "recipients": [_user(user_id)]}
        payload["channel_id"] = "1"
        payload["user"] = _user(user_id)
        payload["context"] = 1
    else:
        payload["guild_id"] = str(GUILD)
        payload["guild_locale"] = "en-US"
        payload["channel"] = {
            "id": str(CHANNEL),
            "type": 0,
            "guild_id": str(GUILD),
            "name": "general",
            "position": 0,
            "permission_overwrites": [],
            "nsfw": False,
            "parent_id": None,
        }
        payload["channel_id"] = str(CHANNEL)
        payload["member"] = _member(user_id, permissions)
        payload["context"] = 0
    return payload


def command_payload(
    name: str, options: list[dict] | None = None, resolved: dict | None = None, *, autocomplete: bool = False, **kw
) -> dict:
    payload = _base(4 if autocomplete else 2, **kw)
    payload["data"] = {"id": "999", "name": name, "type": 1, "options": options or [], "resolved": resolved or {}}
    return payload


def component_payload(custom_id: str, component_type: int, values: list[str] | None = None, **kw) -> dict:
    payload = _base(3, **kw)
    payload["data"] = {"custom_id": custom_id, "component_type": component_type}
    if values is not None:
        payload["data"]["values"] = values
    payload["message"] = _message_payload()
    return payload


def opt(name: str, value, kind: int = 3) -> dict:
    return {"name": name, "type": kind, "value": value}


# --------------------------------------------------------------------------- fake sources

TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
LONG_SLUG = "advisory-vote-no-10-engrossed-second-substitute-senate-bill-5096-concerning-an-excise-tax-on-capital-gains"
NOW = datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)


class FakeResults:
    name = "fake_results"
    states = {"TX", "CA"}

    def available(self) -> bool:
        return True

    async def list_races(self, state, http):
        races = [
            (RaceKey(state, Level.SENATE, "SEN"), f"{state} Senate"),
            (RaceKey(state, Level.GOVERNOR, "GOV"), f"{state} Governor"),
        ]
        races += [
            (
                RaceKey(state, Level.HOUSE, "HOUSE", f"{district_number:02d}"),
                f"{state}-{district_number:02d} " + "x" * 120,
            )
            for district_number in range(1, 31)
        ]
        # Adapters slug the full contest name into LOCAL keys: these can exceed 100 characters.
        races += [(RaceKey(state, Level.LOCAL, "LOCAL", LONG_SLUG), "Advisory Vote No. 10")]
        return races


class FakeOdds:
    name = "fake_odds"

    def __init__(self) -> None:
        self.fail = False

    def available(self) -> bool:
        return True

    async def fetch_odds(self, keys, http):
        if self.fail:
            raise RuntimeError("odds source down")
        return [
            OddsSnapshot(key, market, outcome, prob, NOW, url="https://example.com/m")
            for key in keys
            for market in ("kalshi", "polymarket")
            for outcome, prob in (("DEM", 0.41), ("REP", 0.59))
        ]


class FakePolls:
    name = "fake_polls"

    def available(self) -> bool:
        return True

    async def fetch_polls(self, keys, http):
        return [
            Poll(
                key,
                f"Pollster {index}",
                [PollResult("Candidate A", "DEM", 47.0), PollResult("Candidate B", "REP", 49.0)],
                date(2026, 10, 1) + timedelta(days=index),
                sample_size=800,
                population="LV",
                partisan="REP" if index % 3 == 0 else None,
                url="https://example.com/p",
            )
            for key in keys
            for index in range(12)
        ]


class ExplodingStore(MemoryStore):
    async def save_guild(self, gs):
        raise RuntimeError("mongo down token=hunter2")


@pytest.fixture
async def harness():
    recorder = RecordingAdapter()
    reset = async_context.set(recorder)
    store = MemoryStore()
    await store.upsert_result(
        RaceResult(
            TX_SEN,
            [CandidateResult("Alice Example", "DEM", 1000, winner=False), CandidateResult("Bob Example", "REP", 1200)],
            "fake_results",
            NOW,
        )
    )
    odds = FakeOdds()
    async with httpx.AsyncClient() as client:
        bot = ElectionBot(
            store, {"results": [FakeResults()], "odds": [odds], "polls": [FakePolls()]}, client, clock=lambda: NOW
        )
        await bot._async_setup_hook()
        # As after READY: the logged-in application user.
        bot._connection.user = discord.ClientUser(state=bot._connection, data=_user(APP) | {"bot": True})
        bot._connection._add_guild_from_data(json.loads(json.dumps(GUILD_CREATE)))
        test_harness = Harness(bot, recorder, odds)
        # What the background refresh stores; /odds and /polls read only the store.
        await bot.poller._odds(NOW, [], [TX_SEN, RaceKey("CA", Level.SENATE, "SEN")])
        await bot.poller._polls(NOW, [TX_SEN])
        await bot.race_lists.refresh()  # the /races browser reads the stored lists
        early_vote_http = mock_http_client()  # /earlyvote reads recorded civicAPI responses
        bot.early_vote = fixture_client(early_vote_http)
        try:
            yield test_harness
        finally:
            for view in list(bot._connection._view_store.persistent_views) + test_harness.views():
                view.stop()
            await early_vote_http.aclose()
            await bot.close()
            async_context.reset(reset)


class Harness:
    def __init__(self, bot: ElectionBot, recorder: RecordingAdapter, odds: FakeOdds) -> None:
        self.bot, self.recorder, self.odds = bot, recorder, odds

    def views(self) -> list:
        return [
            entry.view
            for entries in self.bot._connection._view_store._views.values()
            for entry in entries.values()
            if entry.view is not None
        ]

    async def dispatch(self, payload: dict) -> list[Sent]:
        """Run a payload through the real gateway handler and wait for every task it spawns."""
        start = len(self.recorder.sent)
        before = asyncio.all_tasks()
        self.bot._connection.parse_interaction_create(payload)
        for _ in range(10):
            pending = [
                task
                for task in asyncio.all_tasks() - before
                if task.get_name().startswith(("CommandTree-invoker", "discord-ui-view-dispatch")) and not task.done()
            ]
            if not pending:
                await asyncio.sleep(0)
                pending = [
                    task
                    for task in asyncio.all_tasks() - before
                    if task.get_name().startswith(("CommandTree-invoker", "discord-ui-view-dispatch"))
                    and not task.done()
                ]
                if not pending:
                    break
            await asyncio.wait(pending, timeout=5)
        return self.recorder.sent[start:]


# --------------------------------------------------------------------------- assertions


def _embed_size(embed: dict) -> int:
    size = len(embed.get("title", "")) + len(embed.get("description", ""))
    size += len(embed.get("footer", {}).get("text", ""))
    size += sum(len(embed_field["name"]) + len(embed_field["value"]) for embed_field in embed.get("fields", []))
    return size


def check_message_limits(message_payload: dict) -> None:
    assert len(message_payload.get("content") or "") <= 2000
    embeds = message_payload.get("embeds") or []
    assert len(embeds) <= 10
    for embed in embeds:
        assert len(embed.get("title", "")) <= 256
        assert len(embed.get("description", "")) <= 4096
        assert len(embed.get("fields", [])) <= 25
        for field in embed.get("fields", []):
            assert 1 <= len(field["name"]) <= 256 and 1 <= len(field["value"]) <= 1024
        assert len(embed.get("footer", {}).get("text", "")) <= 2048
        assert _embed_size(embed) <= 6000
    rows = message_payload.get("components") or []
    assert len(rows) <= 5
    for row in rows:
        assert row["type"] == 1 and 1 <= len(row["components"]) <= 5
        for component in row["components"]:
            assert len(component["custom_id"]) <= 100
            if component["type"] == 3:
                assert len(row["components"]) == 1
                assert 1 <= len(component["options"]) <= 25
                assert len(component.get("placeholder", "")) <= 150
                for option in component["options"]:
                    assert 1 <= len(option["label"]) <= 100 and 1 <= len(option["value"]) <= 100
            else:
                assert len(component.get("label", "")) <= 80


def assert_ephemeral_reply(sent: list[Sent]) -> None:
    """One acknowledgement; every user-visible message it creates is ephemeral."""
    assert sent, "the interaction was never acknowledged"
    first = sent[0]
    assert (first.method, first.path) == ("POST", CALLBACK)
    callbacks = [sent_request for sent_request in sent if sent_request.path == CALLBACK]
    assert len(callbacks) == 1, "an interaction can only be acknowledged once"
    for sent_request in sent:
        if sent_request.path == CALLBACK:
            assert sent_request.payload["type"] in (CHANNEL_MESSAGE_WITH_SOURCE, DEFERRED_CHANNEL_MESSAGE), (
                sent_request.payload
            )
            assert sent_request.payload["data"]["flags"] & EPHEMERAL, sent_request.payload
            check_message_limits(sent_request.payload["data"])
        elif sent_request.path == FOLLOWUP:
            assert sent_request.method == "POST"
            assert sent_request.payload["flags"] & EPHEMERAL, sent_request.payload
            check_message_limits(sent_request.payload)
        elif sent_request.path == ORIGINAL:
            assert sent_request.method == "PATCH"
            check_message_limits(sent_request.payload)
        else:  # pragma: no cover - nothing else is expected
            raise AssertionError(f"unexpected request {sent_request.method} {sent_request.path}")
    if first.payload["type"] == DEFERRED_CHANNEL_MESSAGE:
        assert any(sent_request.path in (FOLLOWUP, ORIGINAL) for sent_request in sent[1:]), (
            "deferred but never answered"
        )


def text_of(sent: list[Sent]) -> str:
    parts = []
    for sent_request in sent:
        message_payload = sent_request.payload.get("data", sent_request.payload) if sent_request.payload else {}
        parts.append(message_payload.get("content") or "")
        for embed in message_payload.get("embeds") or []:
            parts.append(json.dumps(embed, ensure_ascii=False))
    return "\n".join(parts)


# --------------------------------------------------------------------------- command matrix

TEXT_CHANNEL_RESOLVED = {
    "channels": {str(TARGET_CHANNEL): _text_channel(TARGET_CHANNEL, "elections") | {"permissions": "0"}},
    "roles": {str(ROLE): _role(ROLE, "Election admins", 0, 1)},
}

# (case id, command, options, resolved, interaction kwargs, text expected in the reply)
CASES = [
    (
        "setup-ok",
        "setup",
        [opt("channel", str(TARGET_CHANNEL), 7), opt("admin_role", str(ROLE), 8)],
        TEXT_CHANNEL_RESOLVED,
        {},
        f"<#{TARGET_CHANNEL}>",
    ),
    (
        "setup-no-role",
        "setup",
        [opt("channel", str(TARGET_CHANNEL), 7)],
        TEXT_CHANNEL_RESOLVED,
        {},
        "Election posts will go to",
    ),
    (
        "setup-not-admin",
        "setup",
        [opt("channel", str(TARGET_CHANNEL), 7)],
        TEXT_CHANNEL_RESOLVED,
        {"permissions": 0},
        "Manage Server",
    ),
    # In a DM the guild channel cannot be resolved: the transformer fails before the command
    # body runs, so the generic (still ephemeral) error reply is sent.
    ("setup-dm", "setup", [opt("channel", str(TARGET_CHANNEL), 7)], TEXT_CHANNEL_RESOLVED, {"dm": True}, ""),
    ("pause-dm", "pause", [], None, {"dm": True}, "Use this command in a server"),
    ("watch-add", "watch", [opt("action", "add"), opt("race", "TX SEN")], None, {}, "Watching TX SEN"),
    ("watch-list", "watch", [opt("action", "list")], None, {"permissions": 0}, "Watched races"),
    ("watch-remove", "watch", [opt("action", "remove"), opt("race", "Texas Senate")], None, {}, "Removed"),
    ("watch-missing-race", "watch", [opt("action", "add")], None, {}, "race is required"),
    ("watch-bad-race", "watch", [opt("action", "add"), opt("race", "Narnia SEN")], None, {}, ""),
    (
        "watch-not-admin",
        "watch",
        [opt("action", "add"), opt("race", "TX SEN")],
        None,
        {"permissions": 0},
        "Manage Server",
    ),
    ("posting-interval", "posting", [opt("mode", "interval"), opt("minutes", 30, 4)], None, {}, "every 30 minutes"),
    ("posting-immediate", "posting", [opt("mode", "immediate")], None, {}, "immediate"),
    ("calls-on", "calls", [opt("outlets", "AP, cbs"), opt("enabled", "on")], None, {}, "AP, CBS"),
    ("calls-unknown", "calls", [opt("outlets", "XYZ"), opt("enabled", "off")], None, {}, "unknown outlets"),
    (
        "oddsalerts-on",
        "oddsalerts",
        [opt("enabled", "on"), opt("threshold", 5.5, 10), opt("window", 30, 4)],
        None,
        {},
        "threshold 5.5 points",
    ),
    ("oddsalerts-off", "oddsalerts", [opt("enabled", "off")], None, {}, "Odds alerts off"),
    (
        "oddsalerts-race",
        "oddsalerts",
        [opt("race", "PA-08"), opt("threshold", 5, 10), opt("window", 30, 4)],
        None,
        {},
        "Odds alerts for PA-08: threshold 5 points, window 30 minutes",
    ),
    (
        "oddsalerts-category",
        "oddsalerts",
        [opt("enabled", "off"), opt("category", "mayor")],
        None,
        {},
        "Odds alerts for Mayor (mayor): off",
    ),
    ("oddsalerts-list", "oddsalerts", [], None, {"permissions": 0}, "Server default"),
    ("oddsalerts-change-not-admin", "oddsalerts", [opt("threshold", 5, 10)], None, {"permissions": 0}, "Manage Server"),
    ("odds-overview", "odds", [], None, {}, "Watched race odds"),
    ("odds-race", "odds", [opt("race", "TX SEN")], None, {}, "Odds"),
    ("odds-dm", "odds", [], None, {"dm": True}, "Watched race odds"),
    ("odds-bad-race", "odds", [opt("race", "Narnia SEN")], None, {}, ""),
    ("polls-default", "polls", [opt("race", "TX SEN")], None, {}, "Showing 5 most recent polls"),
    (
        "polls-10-no-internals",
        "polls",
        [opt("race", "TX SEN"), opt("count", 10, 4), opt("internals", False, 5)],
        None,
        {},
        "internals excluded",
    ),
    ("polls-bad-race", "polls", [opt("race", "Narnia SEN")], None, {}, ""),
    ("refresh", "refresh", [opt("what", "odds")], None, {}, "Refreshed"),
    ("refresh-races", "refresh", [opt("what", "races")], None, {}, "Race lists are refreshing"),
    ("refresh-not-admin", "refresh", [], None, {"permissions": 0}, "Manage Server"),
    ("limits", "limits", [], None, {"permissions": 0}, "Data refresh"),
    ("limits-set", "limits", [opt("user_cooldown", 30, 4)], None, {}, "Limits set"),
    ("limits-set-not-admin", "limits", [opt("user_cooldown", 30, 4)], None, {"permissions": 0}, "Manage Server"),
    ("turnout-on", "turnout", [opt("enabled", "on")], None, {}, "turnout on"),
    ("exitpolls-off", "exitpolls", [opt("enabled", "off")], None, {}, "exit polls off"),
    ("pause", "pause", [], None, {}, "paused"),
    ("resume", "resume", [], None, {}, "resumed"),
    ("pause-not-admin", "pause", [], None, {"permissions": 0}, "Manage Server"),
    ("settings", "settings", [], None, {"permissions": 0}, "guild_id"),
    ("settings-dm", "settings", [], None, {"dm": True}, "Use this command in a server"),
    ("races", "races", [], None, {}, "Texas"),
    ("races-dm", "races", [], None, {"dm": True}, "Texas"),
    ("favorite-add", "favorite", [opt("action", "add"), opt("race", "TX SEN")], None, {}, "Favorited TX SEN"),
    ("favorite-remove", "favorite", [opt("action", "remove"), opt("race", "TX SEN")], None, {}, "Removed favorite"),
    ("favorite-bad", "favorite", [opt("action", "add"), opt("race", "Narnia SEN")], None, {}, ""),
    ("faq-none", "faq", [], None, {}, "State FAQs"),
    ("faq-tx", "faq", [opt("state", "TX")], None, {}, "```yaml"),
    ("faq-territory", "faq", [opt("state", "PR")], None, {}, ""),
    ("faq-bad", "faq", [opt("state", "ZZ")], None, {}, "unknown state"),
    ("earlyvote-overview", "earlyvote", [], None, {"dm": True}, "Early vote · United States"),
    ("earlyvote-state", "earlyvote", [opt("state", "nc")], None, {"permissions": 0}, "North Carolina"),
    ("earlyvote-county", "earlyvote", [opt("state", "Georgia"), opt("breakdown", "county")], None, {}, "Top counties"),
    ("earlyvote-no-data", "earlyvote", [opt("state", "NY")], None, {}, "no early-vote data"),
    ("earlyvote-bad", "earlyvote", [opt("state", "ZZ")], None, {}, "Unknown state"),
    ("earlyvote-posts-on", "earlyvote_posts", [opt("enabled", "on")], None, {}, "early vote on"),
    ("earlyvote-posts-not-admin", "earlyvote_posts", [opt("enabled", "on")], None, {"permissions": 0}, "Manage Server"),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
async def test_every_command_reply_is_ephemeral_on_the_wire(harness, case):
    _, name, options, resolved, kwargs, expected = case
    sent = await harness.dispatch(command_payload(name, options, resolved, **kwargs))
    assert_ephemeral_reply(sent)
    assert expected in text_of(sent)
    assert "hunter2" not in text_of(sent)


async def test_matrix_covers_every_registered_command(harness):
    registered = {command.name for command in harness.bot.tree.get_commands()}
    assert registered == {case[1] for case in CASES}


async def test_resolved_role_and_channel_reach_the_settings(harness):
    await harness.dispatch(
        command_payload(
            "setup", [opt("channel", str(TARGET_CHANNEL), 7), opt("admin_role", str(ROLE), 8)], TEXT_CHANNEL_RESOLVED
        )
    )
    gs = await harness.bot.store.get_guild(GUILD)
    assert (gs.channel_id, gs.admin_role_id) == (TARGET_CHANNEL, ROLE)
    # The configured role now grants admin to a member without Manage Server.
    member = _base(2, permissions=0)
    member["member"]["roles"] = [str(ROLE)]
    member["data"] = {"id": "999", "name": "pause", "type": 1, "options": [], "resolved": {}}
    sent = await harness.dispatch(member)
    assert_ephemeral_reply(sent)
    assert "paused" in text_of(sent)


async def test_deferred_commands_answer_with_an_ephemeral_followup(harness):
    for name, options in (("odds", []), ("polls", [opt("race", "TX SEN")]), ("races", [])):
        sent = await harness.dispatch(command_payload(name, options))
        assert sent[0].payload == {"type": DEFERRED_CHANNEL_MESSAGE, "data": {"flags": EPHEMERAL}}
        assert [(sent_request.method, sent_request.path) for sent_request in sent[1:]] == [("POST", FOLLOWUP)]
        assert sent[1].payload["flags"] & EPHEMERAL


# --------------------------------------------------------------------------- error paths


async def test_exception_before_reply_goes_through_the_error_handler_ephemerally(harness):
    harness.bot.store.__class__ = ExplodingStore
    sent = await harness.dispatch(command_payload("pause"))
    assert_ephemeral_reply(sent)
    assert sent[0].payload["type"] == CHANNEL_MESSAGE_WITH_SOURCE
    assert "Something went wrong" in text_of(sent)
    assert "hunter2" not in text_of(sent)


async def test_exception_after_defer_is_reported_as_ephemeral_followup(harness):
    async def boom(key):
        raise RuntimeError("odds exploded token=hunter2")

    harness.bot.command_data.odds_embed = boom
    sent = await harness.dispatch(command_payload("odds", [opt("race", "TX SEN")]))
    assert_ephemeral_reply(sent)
    assert sent[0].payload["type"] == DEFERRED_CHANNEL_MESSAGE
    assert sent[-1].path == FOLLOWUP and "Something went wrong" in sent[-1].payload["content"]
    assert "hunter2" not in text_of(sent)


async def test_setup_with_uncached_channel_fails_ephemerally(harness):
    # The TextChannel transformer resolves from the guild cache; a channel the bot never saw
    # (e.g. missing guilds intent) fails conversion -> error handler, still ephemeral.
    unknown = 777777777777777777
    resolved = {"channels": {str(unknown): _text_channel(unknown, "ghost") | {"permissions": "0"}}}
    sent = await harness.dispatch(command_payload("setup", [opt("channel", str(unknown), 7)], resolved))
    assert_ephemeral_reply(sent)
    assert "Something went wrong" in text_of(sent)


async def test_unknown_command_is_answered_ephemerally(harness):
    sent = await harness.dispatch(command_payload("no-such-command"))
    assert_ephemeral_reply(sent)
    assert "Something went wrong" in text_of(sent)


async def test_failing_odds_source_still_renders_an_ephemeral_embed(harness):
    harness.odds.fail = True
    harness.bot.rates.force({"odds"})
    await harness.bot.poller._odds(NOW, [], [RaceKey("NV", Level.SENATE, "SEN")])  # refresh fails
    sent = await harness.dispatch(command_payload("odds", [opt("race", "NV SEN")]))
    assert_ephemeral_reply(sent)
    assert "queued for the next odds refresh" in text_of(sent)


# --------------------------------------------------------------------------- autocomplete


@pytest.mark.parametrize("command", ["watch", "favorite", "odds", "polls"])
@pytest.mark.parametrize("typed", ["", "tx", "x" * 50, "advisory"])
async def test_race_autocomplete_payload(harness, command, typed):
    options = [opt("race", typed) | {"focused": True}]
    if command in ("watch", "favorite"):
        options.insert(0, opt("action", "add"))
    sent = await harness.dispatch(command_payload(command, options, autocomplete=True))
    assert len(sent) == 1 and sent[0].path == CALLBACK
    assert sent[0].payload["type"] == AUTOCOMPLETE_RESULT
    choices = sent[0].payload["data"]["choices"]
    assert len(choices) <= 25
    for choice in choices:
        assert 1 <= len(choice["name"]) <= 100 and 1 <= len(choice["value"]) <= 100
    if typed == "tx":
        assert choices


# --------------------------------------------------------------------------- navigator


def components(payload: dict) -> list[dict]:
    message_payload = payload.get("data", payload)
    return [component for row in message_payload.get("components") or [] for component in row["components"]]


def button(payload: dict, label: str) -> dict:
    return next(
        component for component in components(payload) if component["type"] == 2 and component.get("label") == label
    )


def selects(payload: dict) -> list[dict]:
    return [component for component in components(payload) if component["type"] == 3]


async def test_race_navigator_round_trip_uses_correct_component_responses(harness):
    sent = await harness.dispatch(command_payload("races"))
    assert_ephemeral_reply(sent)
    message = sent[-1].payload
    assert message["flags"] & EPHEMERAL
    assert [component["label"] for component in components(message) if component["type"] == 2][:4] == [
        "Congressional",
        "Senate",
        "State",
        "Local",
    ]
    assert len(selects(message)) == 3  # 51 states in groups of 25

    # Tab button: deferred update (6, no new message), then the original message is edited.
    sent = await harness.dispatch(component_payload(button(message, "Congressional")["custom_id"], 2))
    assert [(select.method, select.path) for select in sent] == [("POST", CALLBACK), ("PATCH", ORIGINAL)]
    assert sent[0].payload == {"type": DEFERRED_UPDATE_MESSAGE}
    message = sent[1].payload
    check_message_limits(message)
    assert "Texas · Congressional" in text_of(sent) or "TX-01" in text_of(sent)

    # State select: deferred update + edit.
    california = next(
        select for select in selects(message) if any(option["value"] == "CA" for option in select["options"])
    )
    sent = await harness.dispatch(component_payload(california["custom_id"], 3, ["CA"]))
    assert [(select.method, select.path) for select in sent] == [("POST", CALLBACK), ("PATCH", ORIGINAL)]
    assert sent[0].payload == {"type": DEFERRED_UPDATE_MESSAGE}
    message = sent[1].payload
    assert "California" in text_of(sent)
    assert (await harness.bot.store.get_user(OWNER)).home_state == "CA"

    # Odds / Polls buttons: deferred update, the data arrives as a NEW ephemeral followup.
    for tool in ("Odds", "Polls"):
        sent = await harness.dispatch(component_payload(button(message, tool)["custom_id"], 2))
        assert [(select.method, select.path) for select in sent] == [("POST", CALLBACK), ("POST", FOLLOWUP)]
        assert sent[0].payload["type"] == DEFERRED_UPDATE_MESSAGE
        assert sent[1].payload["flags"] & EPHEMERAL
        assert tool in text_of(sent[1:])
        check_message_limits(sent[1].payload)

    # Choose race: UPDATE_MESSAGE (7) with a race select capped at 25 options.
    sent = await harness.dispatch(component_payload(button(message, "Choose race")["custom_id"], 2))
    assert len(sent) == 1 and sent[0].payload["type"] == UPDATE_MESSAGE
    message = sent[0].payload
    check_message_limits(message["data"])
    race_select = next(select for select in selects(message) if select.get("placeholder", "").startswith("Race"))
    # More than 25 races: 23 per page plus a "next" option.
    assert len(race_select["options"]) == 24 and race_select["options"][-1]["value"] == "next"

    # Race select: UPDATE_MESSAGE (7) showing that race.
    sent = await harness.dispatch(component_payload(race_select["custom_id"], 3, [race_select["options"][3]["value"]]))
    assert len(sent) == 1 and sent[0].payload["type"] == UPDATE_MESSAGE
    check_message_limits(sent[0].payload["data"])
    assert button(sent[0].payload, "Odds")


async def test_navigator_selects_a_race_whose_key_exceeds_100_characters(harness):
    # Found live: Discord rejected the race select (400 "Must be between 1 and 100 in length")
    # when a LOCAL key was used as the option value.
    sent = await harness.dispatch(command_payload("races"))
    message = sent[-1].payload
    sent = await harness.dispatch(component_payload(button(message, "Local")["custom_id"], 2))
    message = sent[-1].payload
    sent = await harness.dispatch(component_payload(button(message, "Choose race")["custom_id"], 2))
    check_message_limits(sent[0].payload["data"])
    race_select = next(
        select for select in selects(sent[0].payload) if select.get("placeholder", "").startswith("Race")
    )
    option = next(option for option in race_select["options"] if option["label"] == "Advisory Vote No. 10")
    sent = await harness.dispatch(component_payload(race_select["custom_id"], 3, [option["value"]]))
    assert sent[0].payload["type"] == UPDATE_MESSAGE
    view = next(long_race_key for long_race_key in harness.views() if long_race_key.selected_race)
    assert view.selected_race == f"TX LOCAL {LONG_SLUG}"


async def test_navigator_shows_stored_result_embed(harness):
    sent = await harness.dispatch(command_payload("races"))
    embed = sent[-1].payload["embeds"][0]
    assert "Alice Example" in embed["description"]


@pytest.mark.parametrize("which", ["tab", "state", "odds", "choose"])
async def test_navigator_refuses_other_users_ephemerally(harness, which):
    sent = await harness.dispatch(command_payload("races"))
    message = sent[-1].payload
    if which == "tab":
        payload = component_payload(button(message, "State")["custom_id"], 2, user_id=STRANGER)
    elif which == "state":
        payload = component_payload(selects(message)[0]["custom_id"], 3, ["AL"], user_id=STRANGER)
    elif which == "odds":
        payload = component_payload(button(message, "Odds")["custom_id"], 2, user_id=STRANGER)
    else:
        payload = component_payload(button(message, "Choose race")["custom_id"], 2, user_id=STRANGER)
    sent = await harness.dispatch(payload)
    assert len(sent) == 1
    assert sent[0].payload["type"] == CHANNEL_MESSAGE_WITH_SOURCE
    assert sent[0].payload["data"]["flags"] & EPHEMERAL
    assert "belongs to another user" in sent[0].payload["data"]["content"]


# --------------------------------------------------------------------------- schema

_NAME = re.compile(r"^[-_\w]{1,32}$", re.UNICODE)


def _check_option(option: dict, where: str) -> None:
    assert _NAME.match(option["name"]) and option["name"] == option["name"].lower(), where
    assert 1 <= len(option["description"]) <= 100, where
    choices = option.get("choices") or []
    assert len(choices) <= 25, where
    for choice in choices:
        assert 1 <= len(choice["name"]) <= 100, where
        assert isinstance(choice["value"], (str, int, float)), where
        if isinstance(choice["value"], str):
            assert len(choice["value"]) <= 100, where
    assert not (option.get("autocomplete") and choices), f"{where}: autocomplete with choices"
    if "min_value" in option and "max_value" in option:
        assert option["min_value"] <= option["max_value"], where


async def test_command_schema_follows_discord_rules(harness):
    tree = harness.bot.tree
    payloads = [command.to_dict(tree) for command in tree.get_commands()]
    assert 1 <= len(payloads) <= 100
    assert len({parameter["name"] for parameter in payloads}) == len(payloads)
    for payload in payloads:
        name = payload["name"]
        assert _NAME.match(name) and name == name.lower(), name
        assert payload["type"] == 1
        assert 1 <= len(payload["description"]) <= 100, name
        options = payload.get("options", [])
        assert len(options) <= 25, name
        required = [option.get("required", False) for option in options]
        assert required == sorted(required, reverse=True), f"{name}: required option after optional"
        assert len({option["name"] for option in options}) == len(options), name
        for option in options:
            _check_option(option, f"/{name} {option['name']}")
        # Command name + description + options' names/descriptions/choices <= 4000 characters.
        total = (
            len(name)
            + len(payload["description"])
            + sum(
                len(option["name"])
                + len(option["description"])
                + sum(len(command["name"]) + len(str(command["value"])) for command in option.get("choices", []))
                for option in options
            )
        )
        assert total <= 4000, name
    by_name = {parameter["name"]: parameter for parameter in payloads}
    channel = next(option for option in by_name["setup"]["options"] if option["name"] == "channel")
    assert channel["type"] == 7 and set(channel["channel_types"]) <= {0, 5}  # text / announcement
    minutes = next(option for option in by_name["posting"]["options"] if option["name"] == "minutes")
    assert (minutes["min_value"], minutes["max_value"]) == (1, 1440)
    count = next(option for option in by_name["polls"]["options"] if option["name"] == "count")
    assert (count["min_value"], count["max_value"]) == (1, 10)
    json.dumps(payloads)


async def test_repeating_a_data_command_for_the_same_race_hits_the_cooldown(harness):
    first = await harness.dispatch(command_payload("odds", [opt("race", "TX SEN")]))
    assert "Kalshi" in text_of(first)
    again = await harness.dispatch(command_payload("odds", [opt("race", "TX SEN")]))
    assert_ephemeral_reply(again)
    assert "You can ask for this again in 2 min" in text_of(again)
    other = await harness.dispatch(command_payload("odds", [opt("race", "CA SEN")]))
    assert "Kalshi" in text_of(other)
