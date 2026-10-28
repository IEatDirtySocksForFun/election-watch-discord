"""Shared fakes for the data-command and navigator matrix tests (no network, no gateway).

* ``FakeInteraction`` records every response / defer / followup / edit, with content, embed,
  view and the ephemeral flag, and enforces Discord's acknowledgement rules: the initial
  response may happen once, a followup or ``edit_original_response`` only after it.
* ``check_embed`` / ``check_view`` / ``check_content`` / ``check_choices`` validate payloads
  against Discord's hard limits. Every recorded message goes through ``check_message``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import discord

from electionwatch.bot.command_data import CommandData
from electionwatch.racelists import CACHE, RaceLists, cache_ident, dump_list

# Discord hard limits.
TITLE, DESCRIPTION, FIELDS, FIELD_NAME, FIELD_VALUE = 256, 4096, 25, 256, 1024
FOOTER, AUTHOR, EMBED_TOTAL, CONTENT = 2048, 256, 6000, 2000
ROWS, PER_ROW, SELECT_OPTIONS, OPTION_TEXT, BUTTON_LABEL, CUSTOM_ID = 5, 5, 25, 100, 80, 100
PLACEHOLDER, CHOICES, CHOICE_TEXT = 150, 25, 100


def embed_total(embed: discord.Embed) -> int:
    total = len(embed.title or "") + len(embed.description or "")
    total += sum(len(embed_field.name or "") + len(embed_field.value or "") for embed_field in embed.fields)
    total += len(embed.footer.text or "") if embed.footer else 0
    total += len(embed.author.name or "") if embed.author else 0
    return total


def check_embed(embed: discord.Embed) -> None:
    assert isinstance(embed, discord.Embed)
    assert len(embed.title or "") <= TITLE, len(embed.title)
    assert len(embed.description or "") <= DESCRIPTION, len(embed.description)
    assert len(embed.fields) <= FIELDS, len(embed.fields)
    for embed_field in embed.fields:
        assert embed_field.name and len(embed_field.name) <= FIELD_NAME, (len(embed_field.name or ""), embed_field.name)
        assert embed_field.value and len(embed_field.value) <= FIELD_VALUE, len(embed_field.value or "")
    if embed.footer and embed.footer.text:
        assert len(embed.footer.text) <= FOOTER
    if embed.author and embed.author.name:
        assert len(embed.author.name) <= AUTHOR
    assert embed_total(embed) <= EMBED_TOTAL, embed_total(embed)
    embed.to_dict()  # must serialise


def check_content(content: Any) -> None:
    if content is None:
        return
    assert len(str(content)) <= CONTENT, len(str(content))


def check_view(view: discord.ui.View) -> None:
    rows = view.to_components()  # the real payload discord.py would send
    assert len(rows) <= ROWS, len(rows)
    custom_ids: set[str] = set()
    for row in rows:
        assert row["type"] == 1
        components = row["components"]
        assert 1 <= len(components) <= PER_ROW, len(components)
        if any(component["type"] == 3 for component in components):
            assert len(components) == 1, "a select menu must be alone in its row"
        for component in components:
            cid = component.get("custom_id")
            if cid is not None:
                assert len(cid) <= CUSTOM_ID
                assert cid not in custom_ids, "duplicate custom_id"
                custom_ids.add(cid)
            if component["type"] == 2:
                assert len(component.get("label") or "") <= BUTTON_LABEL
            if component["type"] == 3:
                options = component["options"]
                assert 1 <= len(options) <= SELECT_OPTIONS, len(options)
                assert len(component.get("placeholder") or "") <= PLACEHOLDER
                values = [option["value"] for option in options]
                assert len(values) == len(set(values)), "duplicate select values"
                assert sum(bool(option.get("default")) for option in options) <= component.get("max_values", 1)
                for option in options:
                    assert 1 <= len(option["label"]) <= OPTION_TEXT, option["label"]
                    assert 1 <= len(option["value"]) <= OPTION_TEXT, option["value"]
                    assert len(option.get("description") or "") <= OPTION_TEXT


def check_choices(choices: list) -> None:
    assert len(choices) <= CHOICES, len(choices)
    for choice in choices:
        assert 1 <= len(choice.name) <= CHOICE_TEXT, choice.name
        assert 1 <= len(str(choice.value)) <= CHOICE_TEXT, choice.value


@dataclass
class Event:
    kind: str  # defer | send_message | edit_message | followup | edit_original
    content: Any = None
    embed: discord.Embed | None = None
    view: discord.ui.View | None = None
    ephemeral: bool | None = None
    thinking: bool = False
    extra: dict = field(default_factory=dict)


def check_message(event: Event) -> None:
    check_content(event.content)
    if event.embed is not None:
        check_embed(event.embed)
    if event.view is not None:
        check_view(event.view)


class InteractionRuleError(AssertionError):
    pass


class FakeResponse:
    def __init__(self, owner: "FakeInteraction") -> None:
        self.owner = owner
        self._done = False

    def is_done(self) -> bool:
        return self._done

    def _ack(self) -> None:
        if self._done:
            raise InteractionRuleError("interaction acknowledged twice")
        self._done = True

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._ack()
        self.owner._record(Event("defer", ephemeral=ephemeral, thinking=thinking))

    async def send_message(self, content=None, *, embed=None, view=None, ephemeral=False, **kw) -> None:
        self._ack()
        self.owner._record(Event("send_message", content, embed, view, ephemeral, extra=kw))

    async def edit_message(self, *, content=None, embed=None, view=None, **kw) -> None:
        if self.owner.kind != "component":
            raise InteractionRuleError("edit_message is only valid for component interactions")
        self._ack()
        self.owner._record(Event("edit_message", content, embed, view, None, extra=kw))

    async def autocomplete(self, choices) -> None:
        self._ack()
        self.owner._record(Event("autocomplete", extra={"choices": choices}))


class FakeFollowup:
    def __init__(self, owner: "FakeInteraction") -> None:
        self.owner = owner

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False, **kw) -> None:
        if not self.owner.response.is_done():
            raise InteractionRuleError("followup before the interaction was acknowledged")
        self.owner._record(Event("followup", content, embed, view, ephemeral, extra=kw))


class FakeInteraction:
    """Minimal stand-in for discord.Interaction; ``kind`` is 'command' or 'component'."""

    def __init__(
        self,
        client,
        *,
        user_id: int = 9,
        guild_id: int | None = 1,
        kind: str = "command",
        values: list[str] | None = None,
    ) -> None:
        self.client = client
        self.user = SimpleNamespace(id=user_id, guild_permissions=None, roles=[])
        self.guild_id = guild_id
        self.kind = kind
        self.data = {"values": values} if values is not None else {}
        self.command = SimpleNamespace(name="test")
        self.events: list[Event] = []
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    def _record(self, event: Event) -> None:
        check_message(event)
        self.events.append(event)

    async def edit_original_response(self, *, content=None, embed=None, view=None, **kw) -> None:
        if not self.response.is_done():
            raise InteractionRuleError("edit_original_response before acknowledging")
        self._record(Event("edit_original", content, embed, view, None, extra=kw))

    # --- assertions -------------------------------------------------------------------
    @property
    def kinds(self) -> list[str]:
        return [event.kind for event in self.events]

    def replies(self) -> list[Event]:
        """Messages the user sees (everything except a bare defer)."""
        return [event for event in self.events if event.kind != "defer"]

    def assert_ephemeral_flow(self) -> Event:
        """Exactly one acknowledgement; new messages ephemeral; returns the visible reply."""
        acks = [
            event for event in self.events if event.kind in {"defer", "send_message", "edit_message", "autocomplete"}
        ]
        assert len(acks) == 1, self.kinds
        if self.kind == "command" or self.events[0].kind == "send_message":
            for event in self.events:
                if event.kind in {"defer", "send_message", "followup"}:
                    assert event.ephemeral is True, (event.kind, self.kinds)
        else:
            for event in self.events:
                if event.kind in {"send_message", "followup"}:
                    assert event.ephemeral is True, (event.kind, self.kinds)
        if self.events[0].kind == "defer":
            assert "send_message" not in self.kinds
        replies = self.replies()
        assert replies, self.kinds
        return replies[-1]


class RefreshedCommandData(CommandData):
    """CommandData whose store the background refresh has just filled for the asked race.

    The commands read only the store; the poller's refresh is what calls the sources. These
    matrix tests are about what the commands render, so each read is preceded by the refresh
    the poller would have done (source failures are skipped, as the poller skips them)."""

    async def get_odds(self, key):
        from electionwatch.poller import ODDS_CHECKED

        for source in self._available("odds"):
            try:
                odds = await source.fetch_odds([key], self.http_client)
                fetched = [odds_snapshot for odds_snapshot in odds if odds_snapshot.key == key]
            except Exception:
                continue
            for snapshot in fetched:
                await self.store.add_odds(snapshot)
            if not fetched:
                await self.store.put_cached(ODDS_CHECKED, str(key), "", self._now())
        return await super().get_odds(key)

    async def get_polls(self, key):
        from electionwatch.poller import POLLS_CACHE, dump_polls, polls_ident

        for source in self._available("polls"):
            try:
                polls = [poll for poll in await source.fetch_polls([key], self.http_client) if poll.key == key]
            except Exception:
                continue
            await self.store.put_cached(POLLS_CACHE, polls_ident(source.name, key), dump_polls(polls), self._now())
        return await super().get_polls(key)


class RefreshedRaceLists(RaceLists):
    """RaceLists whose store the background refresh has just filled for the asked state.

    The navigator and autocomplete read only the store; the background refresh is what asks
    the sources. Tests about what they render get the refresh the bot would have done first
    (a failing source stores nothing, as in the refresh)."""

    async def stored(self, state):
        from datetime import datetime, timezone

        for source in self.listers(state):
            try:
                races = await source.list_races(state, self.http)
            except Exception:
                continue
            await self.store.put_cached(
                CACHE, cache_ident(state, source.name), dump_list(races), datetime.now(timezone.utc)
            )
        return await super().stored(state)


def make_client(store, sources: dict | None = None, *, clock=None, poller=None, refreshed: bool = True):
    """A bot stand-in exposing what the commands and navigator read from ``interaction.client``.
    With ``refreshed`` the background refreshes run just before each read (see the classes above);
    without it the client reads whatever the test stored."""
    sources = sources or {}
    client = SimpleNamespace(store=store, sources=sources, http_client=object(), poller=poller)
    command_data_class = RefreshedCommandData if refreshed else CommandData
    client.command_data = command_data_class(store, sources, client.http_client, clock)
    client.race_lists = (RefreshedRaceLists if refreshed else RaceLists)(store, sources, client.http_client, clock)
    return client
