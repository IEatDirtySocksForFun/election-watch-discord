"""discord.py 2.x application-command bot. Discord I/O stays in this module/view."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Literal

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from electionwatch.bot.command_data import CommandData
from electionwatch.bot.logic import (
    change_calls,
    change_favorite,
    change_odds,
    change_posting,
    change_setup,
    change_watch,
    faq_text,
    is_admin,
    settings_dump,
)
from electionwatch.bot.navigator import RaceNavigator, list_races
from electionwatch.bot.poster import DiscordPoster
from electionwatch.config import load_races
from electionwatch.poller import Poller
from electionwatch.racekeys import describe, parse
from electionwatch.settings import GuildSettings, UserSettings

logger = logging.getLogger(__name__)


async def handle_app_command_error(interaction: discord.Interaction, error: Exception) -> None:
    """Log a failing command and tell the user, instead of leaving the interaction hanging."""
    name = getattr(getattr(interaction, "command", None), "name", "command")
    logger.error("Command /%s failed: %s", name, type(error).__name__, exc_info=error)
    message = "Something went wrong running that command. Try again in a moment."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Could not tell the user that /%s failed", name)


class ElectionBot(commands.Bot):
    def __init__(self, store, sources: dict[str, list], http_client: httpx.AsyncClient, clock=None):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.store = store
        self.sources = sources
        self.http_client = http_client
        self.command_data = CommandData(store, sources, http_client, clock)
        self.poller = Poller(
            store,
            sources,
            DiscordPoster(self),
            clock or (lambda: datetime.now(timezone.utc)),
            http_client,
        )
        self.tree.on_error = handle_app_command_error
        for command in (
            setup_command,
            watch_command,
            posting_command,
            calls_command,
            odds_alerts_command,
            odds_command,
            polls_command,
            turnout_command,
            exitpolls_command,
            pause_command,
            resume_command,
            settings_command,
            races_command,
            favorite_command,
            faq_command,
        ):
            self.tree.add_command(command)

    async def setup_hook(self) -> None:
        dev_guild = os.environ.get("DEV_GUILD_ID")
        if dev_guild:
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.poll_results.start()

    @tasks.loop(seconds=60)
    async def poll_results(self) -> None:
        try:
            await self.poller.tick()
        except Exception:
            logger.exception("Polling tick failed")

    @poll_results.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()


async def _guild(interaction: discord.Interaction, *, admin: bool = False) -> GuildSettings | None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return None
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    gs = await bot.store.get_guild(interaction.guild_id) or GuildSettings(interaction.guild_id)
    if admin and not is_admin(interaction.user, gs):
        await interaction.response.send_message(
            "Manage Server permission or the configured admin role is required.", ephemeral=True
        )
        return None
    return gs


async def _user(interaction: discord.Interaction) -> UserSettings:
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    return await bot.store.get_user(interaction.user.id) or UserSettings(interaction.user.id)


async def _error(interaction: discord.Interaction, exc: Exception) -> None:
    # Parse errors quote the user's input, which can be up to 6000 characters: Discord
    # rejects messages over 2000, and the user would get the generic failure instead.
    message = str(exc)
    if len(message) > 2000:
        message = message[:1999] + "…"
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="setup", description="Choose the channel for election posts")
@app_commands.describe(
    channel="Channel which will receive election posts",
    admin_role="Optional role (besides Manage Server) allowed to change settings",
)
async def setup_command(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    admin_role: discord.Role | None = None,
) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    message = change_setup(gs, channel.id, admin_role.id if admin_role else None)
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="watch", description="Add, remove, or list important races")
async def watch_command(
    interaction: discord.Interaction, action: Literal["add", "remove", "list"], race: str | None = None
) -> None:
    gs = await _guild(interaction, admin=action != "list")
    if gs is None:
        return
    try:
        message = change_watch(gs, action, race)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    if action != "list":
        # list is read-only and open to everyone: writing back the copy it read would
        # overwrite a concurrent admin change (and create a document for the guild).
        await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="posting", description="Configure result-post timing")
async def posting_command(
    interaction: discord.Interaction,
    mode: Literal["default", "interval", "immediate"],
    minutes: app_commands.Range[int, 1, 1440] | None = None,
) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    try:
        message = change_posting(gs, mode, minutes)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="calls", description="Configure race-call outlets")
async def calls_command(
    interaction: discord.Interaction, outlets: str, enabled: Literal["on", "off"]
) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    try:
        message = change_calls(gs, outlets, enabled == "on")
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="oddsalerts", description="Configure prediction-market swing alerts")
async def odds_alerts_command(
    interaction: discord.Interaction,
    enabled: Literal["on", "off"],
    threshold: app_commands.Range[float, 0.1, 100.0] | None = None,
    window: app_commands.Range[int, 1, 1440] | None = None,
) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    try:
        message = change_odds(gs, enabled == "on", threshold, window)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(message, ephemeral=True)


async def _watched_keys(interaction: discord.Interaction):
    if interaction.guild_id is None:
        return [race.key for race in load_races()]
    gs = await interaction.client.store.get_guild(interaction.guild_id)
    if not gs or not gs.important:
        return [race.key for race in load_races()]
    keys = []
    for text in gs.important:
        try:
            keys.append(parse(text))
        except ValueError:
            logger.warning("Ignoring invalid watched race key %s", text)
    return keys


@app_commands.command(name="odds", description="Show current Kalshi and Polymarket probabilities")
@app_commands.describe(race="Race to show; omit it for all watched races")
async def odds_command(interaction: discord.Interaction, race: str | None = None) -> None:
    if race:
        try:
            keys = [parse(race)]
        except ValueError as exc:
            await _error(interaction, exc)
            return
    else:
        keys = await _watched_keys(interaction)
    await interaction.response.defer(ephemeral=True)
    if race:
        embed = await interaction.client.command_data.odds_embed(keys[0])
    else:
        embed = await interaction.client.command_data.odds_overview(keys)
    await interaction.followup.send(embed=embed, ephemeral=True)


@app_commands.command(name="polls", description="Show the most recent public polls for a race")
@app_commands.describe(
    race="Race to show",
    count="Number of polls (1-10, default 5)",
    internals="Include partisan/internal polls (default true)",
)
async def polls_command(
    interaction: discord.Interaction,
    race: str,
    count: app_commands.Range[int, 1, 10] = 5,
    internals: bool = True,
) -> None:
    try:
        key = parse(race)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.response.defer(ephemeral=True)
    embed = await interaction.client.command_data.polls_embed(key, count, internals)
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _toggle(interaction: discord.Interaction, field: str, enabled: bool) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    setattr(gs, field, enabled)
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message(
        f"{field.replace('post_', '').replace('_', ' ')} {'on' if enabled else 'off'}", ephemeral=True
    )


@app_commands.command(name="turnout", description="Include turnout in result posts")
async def turnout_command(interaction: discord.Interaction, enabled: Literal["on", "off"]) -> None:
    await _toggle(interaction, "post_turnout", enabled == "on")


@app_commands.command(name="exitpolls", description="Post exit polls")
async def exitpolls_command(interaction: discord.Interaction, enabled: Literal["on", "off"]) -> None:
    await _toggle(interaction, "post_exit_polls", enabled == "on")


@app_commands.command(name="pause", description="Pause all election posts")
async def pause_command(interaction: discord.Interaction) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    gs.paused = True
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message("Election posts paused.", ephemeral=True)


@app_commands.command(name="resume", description="Resume election posts")
async def resume_command(interaction: discord.Interaction) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    gs.paused = False
    await interaction.client.store.save_guild(gs)
    await interaction.response.send_message("Election posts resumed.", ephemeral=True)


@app_commands.command(name="settings", description="Show this server's election settings")
async def settings_command(interaction: discord.Interaction) -> None:
    gs = await _guild(interaction)
    if gs is None:
        return
    await interaction.response.send_message(f"```yaml\n{settings_dump(gs)}\n```", ephemeral=True)


@app_commands.command(name="races", description="Browse current race results")
async def races_command(interaction: discord.Interaction) -> None:
    # refresh() lists races from the state sites: defer so the 3 s deadline cannot expire.
    await interaction.response.defer(ephemeral=True)
    settings = await _user(interaction)
    view = RaceNavigator(interaction.client, interaction.user.id, settings)
    await view.refresh()
    await interaction.followup.send(embed=await view.embed(), view=view, ephemeral=True)


@app_commands.command(name="favorite", description="Add or remove a favorite race")
async def favorite_command(
    interaction: discord.Interaction, action: Literal["add", "remove"], race: str
) -> None:
    settings = await _user(interaction)
    try:
        message = change_favorite(settings, action, race)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.client.store.save_user(settings)
    await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="faq", description="Read state and outlet election FAQs")
async def faq_command(interaction: discord.Interaction, state: str | None = None) -> None:
    try:
        from electionwatch.faq.loader import load_outlets, load_states
    except ImportError:
        await interaction.response.send_message("FAQ data not installed", ephemeral=True)
        return
    try:
        text = faq_text(load_states(), load_outlets(), state)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.response.send_message(f"```yaml\n{text}\n```", ephemeral=True)


async def race_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    choices: dict[str, str] = {}
    states: set[str] = set()
    if interaction.guild_id is not None:
        gs = await bot.store.get_guild(interaction.guild_id)
        watched = gs.important if gs and gs.important else [str(r.key) for r in load_races()]
        from electionwatch.racekeys import parse

        for text in watched:
            try:
                key = parse(text)
            except ValueError:
                continue
            choices[str(key)] = describe(key)
            states.add(key.state)
    for state in sorted(states):
        for key, label in (await list_races(bot, state)).values():
            choices[str(key)] = label or describe(key)
    needle = current.casefold()
    return [
        app_commands.Choice(name=f"{key} · {label}"[:100], value=key)
        for key, label in sorted(choices.items())
        if needle in key.casefold() or needle in label.casefold()
    ][:25]


watch_command.autocomplete("race")(race_autocomplete)
favorite_command.autocomplete("race")(race_autocomplete)
odds_command.autocomplete("race")(race_autocomplete)
polls_command.autocomplete("race")(race_autocomplete)
