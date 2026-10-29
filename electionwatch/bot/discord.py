"""discord.py 2.x application-command bot. Discord I/O stays in this module/view."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Literal

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks

from electionwatch.bot.command_data import CommandData
from electionwatch.bot.cooldown import Cooldowns
from electionwatch.bot.early_vote import (
    AUTOCOMPLETE_TIMEOUT,
    BREAKDOWNS,
    county_choices,
    early_vote_client,
    early_vote_embed,
    post_daily_summaries,
    resolve_state,
    state_choices,
)
from electionwatch.bot.logic import (
    change_calls,
    change_favorite,
    change_limits,
    change_odds,
    change_posting,
    change_setup,
    change_watch,
    faq_text,
    is_admin,
    limits_text,
    settings_dump,
)
from electionwatch.bot.navigator import RaceNavigator, race_lists
from electionwatch.bot.poster import DiscordPoster, clip
from electionwatch.config import load_races
from electionwatch.demand import Demand
from electionwatch.live import LiveFetch, command_mode
from electionwatch.poller import Poller
from electionwatch.racekeys import STATES, describe, parse
from electionwatch.racelists import KIND as RACELISTS
from electionwatch.racelists import READ_TIMEOUT, RaceLists, typed_state
from electionwatch.ratelimit import RateBook
from electionwatch.settings import GuildSettings, UserSettings
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.early_vote import EarlyVoteClient, NotCached

logger = logging.getLogger(__name__)
MAX_MESSAGE = 2000  # Discord's message content limit


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
        clock = clock or (lambda: datetime.now(timezone.utc))

        def seconds() -> float:  # budgets and cooldowns run on the bot's clock (replays speed it up)
            now = clock()
            return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()

        # Request budgets per source host, races users asked about, per-user command limits.
        self.rates = RateBook.from_env(os.environ, clock=seconds)
        self.demand = Demand.from_env(os.environ, clock=seconds)
        self.cooldowns = Cooldowns.from_env(os.environ, clock=seconds)
        hooks = getattr(http_client, "event_hooks", None)
        if isinstance(hooks, dict):
            http_client.event_hooks = {**hooks, "request": [*hooks.get("request", []), self.rates.on_request]}
        # EW_COMMAND_DATA=live: commands may fetch from the sources too, within the same
        # budgets (electionwatch/live.py). The default, db, reads only what refreshes stored.
        self.live = (
            LiveFetch.from_env(os.environ, store, sources, http_client, self.rates, clock)
            if command_mode(os.environ) == "live"
            else None
        )
        self.command_data = CommandData(
            store, sources, http_client, clock, rates=self.rates, demand=self.demand, live=self.live
        )
        # Commands read early-vote pages from the store; only the refresher calls civicAPI.
        # In live mode /earlyvote downloads too while civicAPI has room; autocomplete never does.
        self.early_vote_db = EarlyVoteClient(http_client, store=store, cache_only=True)
        self.early_vote = (
            EarlyVoteClient(http_client, store=store, ttl=self.live.max_age, may_download=self.live.early_vote_allowed)
            if self.live is not None
            else self.early_vote_db
        )
        self.early_vote_refresher = EarlyVoteClient(http_client, store=store)
        self._early_vote_running = asyncio.Lock()
        self._background: set[asyncio.Task] = set()
        self.poller = Poller(
            store,
            sources,
            DiscordPoster(self),
            clock,
            http_client,
            rates=self.rates,
            demand=self.demand,
        )
        # The /races browser and race autocomplete read the lists this refresher stores.
        self.race_lists = RaceLists(
            store,
            sources,
            http_client,
            clock,
            rates=self.rates,
            demand=self.demand,
            default_races=self.poller.default_races,
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
            early_vote_command,
            early_vote_posts_command,
            refresh_command,
            limits_command,
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
        self.refresh_race_lists.start()
        self.refresh_early_vote.start()
        self.early_vote_posts.start()

    @tasks.loop(seconds=60)
    async def poll_results(self) -> None:
        try:
            await self.poller.tick()
        except Exception:
            logger.exception("Polling tick failed")

    @poll_results.before_loop
    async def before_poll(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=60)
    async def refresh_race_lists(self) -> None:
        # Its own loop, not part of the poller's tick: a slow state site listing its races
        # must not hold up election results.
        try:
            await self.race_lists.refresh()
        except Exception:
            logger.exception("Race list refresh failed")

    @refresh_race_lists.before_loop
    async def before_refresh_race_lists(self) -> None:
        await self.wait_until_ready()

    async def refresh_early_vote_now(self) -> bool:
        """Download early-vote pages into the store when the budget allows; True if it ran."""
        name = "civicapi:earlyvote"
        if self._early_vote_running.locked() or not self.rates.due(name, "earlyvote"):
            return False
        async with self._early_vote_running:
            with self.rates.track(name, "earlyvote"):
                await self.early_vote_refresher.refresh()
        return True

    @tasks.loop(seconds=60)
    async def refresh_early_vote(self) -> None:
        try:
            await self.refresh_early_vote_now()
        except Exception:
            logger.exception("Early-vote refresh failed")

    @refresh_early_vote.before_loop
    async def before_refresh_early_vote(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(hours=1)
    async def early_vote_posts(self) -> None:
        # Posts at most once per civicAPI snapshot day, only for servers that opted in.
        try:
            await post_daily_summaries(self)
        except NotCached:
            logger.info("Early-vote summary waits for the first early-vote refresh")
        except Exception:
            logger.exception("Early-vote summary failed")

    @early_vote_posts.before_loop
    async def before_early_vote_posts(self) -> None:
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
    # Parse errors quote the user's input, which may be up to 6000 characters long.
    await interaction.response.send_message(clip(str(exc), MAX_MESSAGE), ephemeral=True)


async def _cooldown(interaction: discord.Interaction, command: str, target: str, kind: str) -> bool:
    """True if the user may run a data command now; otherwise tells them when they can.
    Called after defer(): the guild read must not count against Discord's 3 s deadline."""
    bot = interaction.client
    cooldowns = getattr(bot, "cooldowns", None)
    if cooldowns is None:
        return True
    gs = None
    if interaction.guild_id is not None:
        try:
            gs = await bot.store.get_guild(interaction.guild_id)
        except Exception:
            logger.exception("Could not read limits for guild %s", interaction.guild_id)
    every = bot.rates.kind_interval(kind)
    live = getattr(bot, "live", None)
    if live is not None:  # live commands fetch again once an answer is older than this
        every = min(every, live.max_age)
    seconds = cooldowns.user_seconds(every, gs.user_cooldown_seconds if gs else None)
    hourly = gs.user_commands_per_hour if gs and gs.user_commands_per_hour is not None else None
    wait = cooldowns.hit(interaction.user.id, command, target, seconds, hourly)
    if not wait:
        return True
    await interaction.followup.send(
        f"You can ask for this again in {_span(wait)}: {kind} data refreshes about every {_span(every)}, "
        "so a repeat would show the same numbers.",
        ephemeral=True,
    )
    return False


def _span(seconds: float) -> str:
    seconds = max(1, round(seconds))
    return f"{seconds} s" if seconds < 120 else f"{round(seconds / 60)} min"


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
async def calls_command(interaction: discord.Interaction, outlets: str, enabled: Literal["on", "off"]) -> None:
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
    if not await _cooldown(interaction, "odds", str(keys[0]) if race else "watched", "odds"):
        return
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
    # One cooldown per race: other counts or filters show the same stored polls.
    if not await _cooldown(interaction, "polls", str(key), "polls"):
        return
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


@app_commands.command(name="earlyvote_posts", description="Post a daily early-vote summary for watched states")
async def early_vote_posts_command(interaction: discord.Interaction, enabled: Literal["on", "off"]) -> None:
    await _toggle(interaction, "post_early_vote", enabled == "on")


@app_commands.command(name="earlyvote", description="Show early-vote turnout (civicAPI)")
@app_commands.describe(
    state="State code or name; omit it for every state with data",
    breakdown="Detail to show for the state",
    county="One county of the state: its ballots, mail / in person and party registration",
)
async def early_vote_command(
    interaction: discord.Interaction,
    state: str | None = None,
    breakdown: Literal[BREAKDOWNS] | None = None,
    county: str | None = None,
) -> None:
    code = None
    if state:
        try:
            code = resolve_state(state)
        except ValueError as exc:
            await _error(interaction, exc)
            return
    await interaction.response.defer(ephemeral=True)
    # One cooldown per state: every breakdown comes from the same stored pages.
    if not await _cooldown(interaction, "earlyvote", code or "US", "earlyvote"):
        return
    try:
        embed = await early_vote_embed(early_vote_client(interaction.client), code, breakdown, county)
    except ValueError as exc:  # a county the state doesn't list, or a county without a state
        await interaction.followup.send(clip(str(exc), MAX_MESSAGE), ephemeral=True)
        return
    await interaction.followup.send(embed=embed, ephemeral=True)


async def early_vote_state_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    client = getattr(interaction.client, "early_vote_db", None) or early_vote_client(interaction.client)
    states = await client.states_with_data(AUTOCOMPLETE_TIMEOUT)
    # Cold cache or civicAPI down: offer every state; the command says when one has no data.
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in state_choices(states if states is not None else list(STATES), current)
    ]


early_vote_command.autocomplete("state")(early_vote_state_autocomplete)


@early_vote_command.autocomplete("county")
async def early_vote_county_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """The counties of the state already picked, from the stored pages (never a download)."""
    try:
        code = resolve_state(getattr(interaction.namespace, "state", None) or "")
    except ValueError:
        return []
    client = getattr(interaction.client, "early_vote_db", None) or early_vote_client(interaction.client)
    try:
        ev = await asyncio.wait_for(client.state(code, counties=True, compare=False), AUTOCOMPLETE_TIMEOUT)
    except (asyncio.TimeoutError, SourceUnavailable):
        return []
    return [app_commands.Choice(name=name, value=value) for name, value in county_choices(ev, current)]


REFRESH_KINDS = {
    "all": {"results", "calls", "exitpolls", "odds", "polls", "earlyvote", RACELISTS},
    "results": {"results", "calls", "exitpolls"},
    "odds": {"odds"},
    "polls": {"polls"},
    "earlyvote": {"earlyvote"},
    "races": {RACELISTS},
}


@app_commands.command(name="refresh", description="Fetch fresh data now instead of waiting for the next refresh")
@app_commands.describe(what="What to refresh (default: everything)")
async def refresh_command(
    interaction: discord.Interaction,
    what: Literal["all", "results", "odds", "polls", "earlyvote", "races"] = "all",
) -> None:
    gs = await _guild(interaction, admin=True)
    if gs is None:
        return
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    kinds = REFRESH_KINDS[what]
    every = min(bot.rates.kind_interval(kind) for kind in kinds)
    wait = bot.cooldowns.refresh_hit(gs.guild_id, bot.cooldowns.refresh_seconds(every, gs.refresh_cooldown_seconds))
    if wait:
        await interaction.response.send_message(
            f"This server can refresh {what} again in {_span(wait)}.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    forced = set(bot.rates.force(kinds))
    started = bot.rates.clock()
    message = ""
    if "earlyvote" in kinds:
        # A full early-vote download is a few hundred pages at one a second: run it in the
        # background instead of holding the reply.
        if bot._early_vote_running.locked():
            message = " Early-vote pages are already downloading."
        elif not bot.rates.due("civicapi:earlyvote", "earlyvote"):
            message = " Early vote waits for civicAPI's hourly request allowance; try later."
        else:
            task = asyncio.create_task(bot.refresh_early_vote_now())
            bot._background.add(task)
            task.add_done_callback(bot._background.discard)
            message = " Early-vote pages are downloading in the background (one a second)."
    if RACELISTS in kinds:
        # Race lists are a few states per source a minute (racelists.STATES_PER_TICK): forcing
        # them marks every stored list stale and the background refresh works through them.
        bot.race_lists.force()
        if not bot.race_lists.running:
            task = asyncio.create_task(bot.race_lists.refresh())
            bot._background.add(task)
            task.add_done_callback(bot._background.discard)
        message += " Race lists are refreshing in the background, a few states per source a minute."
    kinds = kinds - {"earlyvote", RACELISTS}
    if kinds:
        await bot.poller.tick()
        rows = [row for row in bot.rates.status() if row["source"] in forced and row["kind"] in kinds]
        done = [row for row in rows if (bot.rates.last_run(row["source"]) or -1) >= started]
        held = sorted({row["source"].split(":")[0] for row in rows if row not in done})
        message = (
            f"Refreshed {len(done)} source" + ("s" if len(done) != 1 else "") + "." if rows else "Refreshed."
        ) + message
        if held:
            message += " Waiting for their hourly request allowance or a back-off: " + clip(", ".join(held), 1500) + "."
    await interaction.followup.send(message.strip(), ephemeral=True)


@app_commands.command(name="limits", description="Show refresh rates and command limits; admins can change them")
@app_commands.describe(
    user_cooldown="Seconds before a user can repeat a command for the same race (-1 = default)",
    user_hourly="Data commands per user per hour (0 = no cap, -1 = default)",
    refresh_cooldown="Seconds between two /refresh in this server (-1 = default)",
)
async def limits_command(
    interaction: discord.Interaction,
    user_cooldown: app_commands.Range[int, -1, 604800] | None = None,
    user_hourly: app_commands.Range[int, -1, 10000] | None = None,
    refresh_cooldown: app_commands.Range[int, -1, 604800] | None = None,
) -> None:
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    changing = any(value is not None for value in (user_cooldown, user_hourly, refresh_cooldown))
    gs = await _guild(interaction, admin=changing)
    if gs is None:
        return
    if changing:
        try:
            message = change_limits(gs, user_cooldown, user_hourly, refresh_cooldown)
        except ValueError as exc:
            await _error(interaction, exc)
            return
        await bot.store.save_guild(gs)
        await interaction.response.send_message(message, ephemeral=True)
        return
    live = getattr(bot, "live", None)
    text = clip(limits_text(gs, bot.rates, bot.cooldowns, live.max_age if live is not None else None), MAX_MESSAGE - 8)
    await interaction.response.send_message(f"```\n{text}\n```", ephemeral=True)


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
    # Several database reads before the reply: defer so the 3 s deadline cannot expire.
    await interaction.response.defer(ephemeral=True)
    settings = await _user(interaction)
    watched = None  # outside a server, or a server without its own list: the default races
    if interaction.guild_id is not None:
        try:
            gs = await interaction.client.store.get_guild(interaction.guild_id)
        except Exception:
            logger.exception("Could not read the watch list of guild %s", interaction.guild_id)
            gs = None
        if gs and gs.important:
            watched = list(gs.important)
    view = RaceNavigator(interaction.client, interaction.user.id, settings, watched)
    await view.refresh()
    await interaction.followup.send(embed=await view.embed(), view=view, ephemeral=True)


@app_commands.command(name="favorite", description="Add or remove a favorite race")
async def favorite_command(interaction: discord.Interaction, action: Literal["add", "remove"], race: str) -> None:
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
        from electionwatch.faq.loader import FAQDataError, load_outlets, load_states
    except ImportError:
        await interaction.response.send_message("FAQ data not installed", ephemeral=True)
        return
    try:
        states, outlets = load_states(), load_outlets()
    except FAQDataError:
        # The loader's message names the server's file path: log it, don't show it.
        logger.exception("Could not load the FAQ data")
        await interaction.response.send_message("FAQ data not installed", ephemeral=True)
        return
    try:
        text = faq_text(states, outlets, state)
    except ValueError as exc:
        await _error(interaction, exc)
        return
    await interaction.response.send_message(text, ephemeral=True)


async def race_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot: ElectionBot = interaction.client  # type: ignore[assignment]
    choices: dict[str, str] = {}
    states: set[str] = set()
    # /odds and /polls work in DMs too: offer the default races there.
    gs = None
    if interaction.guild_id is not None:
        # Discord drops an autocomplete answer after 3 s: a slow database falls back to the
        # default races rather than leaving the menu empty.
        try:
            gs = await asyncio.wait_for(bot.store.get_guild(interaction.guild_id), 1.0)
        except Exception:
            logger.warning("Autocomplete could not read guild %s settings in time", interaction.guild_id)
    watched = gs.important if gs and gs.important else [str(race.key) for race in load_races()]
    for text in watched:
        try:
            key = parse(text)
        except ValueError:
            continue
        choices[str(key)] = describe(key)
        states.add(key.state)
    # Typing a state ("PA", "pa sen", "Texas gov") offers that state's races too, and puts the
    # state first in line for the background race-list refresh.
    lists = race_lists(bot)
    typed = typed_state(current)
    if typed:
        states.add(typed)
        lists.want(typed)
    # Stored lists only; a slow database must not cost the whole answer (3 s deadline).
    try:
        listed = await asyncio.wait_for(
            asyncio.gather(*(lists.stored(state) for state in sorted(states))), READ_TIMEOUT
        )
    except Exception:
        logger.warning("Race autocomplete answered without the stored race lists", exc_info=True)
        listed = []
    for found, _ in listed:
        for key, label in found.values():
            choices[str(key)] = label or describe(key)
    needle = current.casefold()
    # Discord rejects the WHOLE autocomplete answer if any choice value exceeds 100
    # characters; LOCAL keys (slug of the full contest name) can. Those stay typeable.
    return [
        app_commands.Choice(name=f"{key} · {label}"[:100], value=key)
        for key, label in sorted(choices.items())
        if len(key) <= 100 and (needle in key.casefold() or needle in label.casefold())
    ][:25]


watch_command.autocomplete("race")(race_autocomplete)
favorite_command.autocomplete("race")(race_autocomplete)
odds_command.autocomplete("race")(race_autocomplete)
polls_command.autocomplete("race")(race_autocomplete)
