"""Fake discord.py Interaction pieces for the admin/config command matrix.

Only what electionwatch/bot/discord.py touches: ``guild_id``, ``user`` (a member with
``guild_permissions.manage_guild`` and ``roles``, or a DM user with neither), ``client.store``,
``command.name``, ``response`` (``send_message`` / ``defer`` / ``is_done``) and ``followup.send``.

Discord's own rules are enforced so a command that breaks a rule fails the test instead of
failing in production: a second initial response raises, and content over 2000 characters
raises ``discord.HTTPException`` (Discord answers 400 to it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import discord

DISCORD_LIMIT = 2000
GUILD_ID = 1001
ADMIN_ROLE_ID = 7001
OTHER_ROLE_ID = 7002


def http_exception(status: int = 400, message: str = "Bad Request") -> discord.HTTPException:
    return discord.HTTPException(SimpleNamespace(status=status, reason=message), message)


@dataclass
class Sent:
    via: str  # "response" | "followup" | "defer"
    content: str | None = None
    embed: Any = None
    view: Any = None
    ephemeral: bool = False


class FakeResponse:
    def __init__(self, interaction: "FakeInteraction", *, fail: Exception | None = None) -> None:
        self._interaction = interaction
        self._done = False
        self.fail = fail

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content=None, *, embed=None, view=None, ephemeral=False, **_):
        if self._done:
            raise AssertionError("interaction answered twice (InteractionResponded in discord.py)")
        if self.fail is not None:
            raise self.fail
        if content is not None and len(str(content)) > DISCORD_LIMIT:
            raise http_exception(400, "content must be 2000 or fewer in length")
        self._done = True
        self._interaction.sent.append(Sent("response", content, embed, view, ephemeral))

    async def defer(self, *, ephemeral=False, **_):
        if self._done:
            raise AssertionError("interaction answered twice (InteractionResponded in discord.py)")
        self._done = True
        self._interaction.sent.append(Sent("defer", ephemeral=ephemeral))


class FakeFollowup:
    def __init__(self, interaction: "FakeInteraction", *, fail: Exception | None = None) -> None:
        self._interaction = interaction
        self.fail = fail

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False, **_):
        if self.fail is not None:
            raise self.fail
        if content is not None and len(str(content)) > DISCORD_LIMIT:
            raise http_exception(400, "content must be 2000 or fewer in length")
        self._interaction.sent.append(Sent("followup", content, embed, view, ephemeral))


@dataclass
class FakeRole:
    id: int
    name: str = "role"


@dataclass
class FakeMember:
    """A guild member: has guild permissions and roles."""

    id: int
    manage_guild: bool = False
    roles: list = field(default_factory=list)

    @property
    def guild_permissions(self):
        return SimpleNamespace(manage_guild=self.manage_guild)


@dataclass
class FakeUser:
    """A DM (or user-install) user: discord.User has neither guild_permissions nor roles."""

    id: int


@dataclass
class FakeChannel:
    id: int
    name: str = "election-night"


class FakeInteraction:
    def __init__(self, client, user, guild_id: int | None = GUILD_ID, command: str = "cmd") -> None:
        self.client = client
        self.user = user
        self.guild_id = guild_id
        self.command = SimpleNamespace(name=command)
        self.sent: list[Sent] = []
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    @property
    def replies(self) -> list[Sent]:
        """Messages the user sees (a defer alone is not a reply)."""
        return [sent_message for sent_message in self.sent if sent_message.via != "defer"]

    @property
    def text(self) -> str:
        return "\n".join(sent_message.content or "" for sent_message in self.replies)


# The four callers of every command.
ROLES = ("manage", "role_admin", "plain", "dm")


def make_user(kind: str, user_id: int = 501):
    if kind == "manage":
        return FakeMember(user_id, manage_guild=True)
    if kind == "role_admin":
        return FakeMember(user_id, roles=[FakeRole(OTHER_ROLE_ID), FakeRole(ADMIN_ROLE_ID, "election-admin")])
    if kind == "plain":
        return FakeMember(user_id, roles=[FakeRole(OTHER_ROLE_ID)])
    if kind == "dm":
        return FakeUser(user_id)
    raise ValueError(kind)


def make_interaction(client, kind: str, user_id: int = 501, command: str = "cmd") -> FakeInteraction:
    return FakeInteraction(client, make_user(kind, user_id), None if kind == "dm" else GUILD_ID, command)


def stand_in_client(store):
    """What the admin commands need from ElectionBot: only ``.store``."""
    return SimpleNamespace(store=store)
