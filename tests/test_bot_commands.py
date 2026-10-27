"""The Discord layer: which commands exist and how failures are reported.

No gateway connection is made: the bot is constructed with fakes and never started.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from electionwatch.bot.discord import ElectionBot, handle_app_command_error
from electionwatch.store.memory import MemoryStore

#: The command surface the brief specifies.
EXPECTED_COMMANDS = {
    "setup", "watch", "posting", "calls", "odds", "turnout", "exitpolls",
    "pause", "resume", "settings", "races", "favorite", "faq",
}


@pytest.mark.asyncio
async def test_bot_registers_every_command_and_the_error_handler():
    async with httpx.AsyncClient() as client:
        bot = ElectionBot(MemoryStore(), {}, client)
        try:
            commands = {command.name: command for command in bot.tree.get_commands()}
            assert set(commands) == EXPECTED_COMMANDS
            assert {parameter.name for parameter in commands["setup"].parameters} == {
                "channel", "admin_role",
            }
            assert {parameter.name for parameter in commands["watch"].parameters} == {
                "action", "race",
            }
            assert {parameter.name for parameter in commands["posting"].parameters} == {
                "mode", "minutes",
            }
            assert {parameter.name for parameter in commands["odds"].parameters} == {
                "enabled", "threshold", "window",
            }
            assert bot.tree.on_error is handle_app_command_error
        finally:
            await bot.close()


class _Response:
    def __init__(self, done: bool) -> None:
        self.done = done
        self.sent: list[tuple[str, bool]] = []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, content: str, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


class _Followup:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, content: str, ephemeral: bool = False) -> None:
        self.sent.append((content, ephemeral))


@pytest.mark.asyncio
async def test_command_errors_are_reported_without_leaking_internals():
    response = _Response(done=False)
    interaction = SimpleNamespace(
        command=SimpleNamespace(name="watch"), response=response, followup=_Followup()
    )

    await handle_app_command_error(interaction, RuntimeError("token=secret"))

    assert len(response.sent) == 1
    content, ephemeral = response.sent[0]
    assert ephemeral is True
    assert "secret" not in content


@pytest.mark.asyncio
async def test_command_errors_use_followup_when_the_interaction_was_answered():
    response = _Response(done=True)
    followup = _Followup()
    interaction = SimpleNamespace(
        command=SimpleNamespace(name="races"), response=response, followup=followup
    )

    await handle_app_command_error(interaction, RuntimeError("boom"))

    assert response.sent == []
    assert len(followup.sent) == 1 and followup.sent[0][1] is True
