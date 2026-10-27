"""Ephemeral race navigator view. All data comes from Store and ResultsSource."""
from __future__ import annotations

import asyncio
import logging
import time

import discord

from electionwatch.bot.logic import LEVELS, filter_races, remember_navigation
from electionwatch.bot.poster import render_embed
from electionwatch.poller import format_result
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import UserSettings

logger = logging.getLogger(__name__)
_TAB_LABELS = {"congressional": "Congressional", "senate": "Senate", "state": "State", "local": "Local"}

LIST_TTL_SECONDS = 300        # scrape adapters must not be hit more than once a minute
LIST_RETRY_SECONDS = 60       # a failed or slow source is retried after this
LIST_TIMEOUT_SECONDS = 2.0    # autocomplete has to answer within Discord's 3 s deadline
_list_cache: dict[tuple[str, str], tuple[float, list]] = {}   # (source, state) -> (expires, races)


async def list_races(client, state: str) -> dict[str, tuple]:
    """Races the available results sources publish for a state, as {key: (key, label)}.

    Cached and time-boxed: the navigator and autocomplete call this on every click and
    keystroke, and both must answer within Discord's interaction deadline.
    """
    sources = [
        source for source in client.sources.get("results", [])
        if state in source.states and source.available()
    ]

    async def one(source) -> list:
        ident = (source.name, state)
        cached = _list_cache.get(ident)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        try:
            races = await asyncio.wait_for(
                source.list_races(state, client.http_client), LIST_TIMEOUT_SECONDS
            )
        except Exception:
            _list_cache[ident] = (time.monotonic() + LIST_RETRY_SECONDS, [])
            raise
        _list_cache[ident] = (time.monotonic() + LIST_TTL_SECONDS, races)
        return races

    found: dict[str, tuple] = {}
    answers = await asyncio.gather(*(one(source) for source in sources), return_exceptions=True)
    for source, races in zip(sources, answers):
        if isinstance(races, BaseException):
            logger.warning("Could not list %s races from %s: %s", state, source.name, type(races).__name__)
            continue
        for key, label in races:
            found[str(key)] = (key, label)
    return found


class RaceNavigator(discord.ui.View):
    def __init__(self, client, user_id: int, settings: UserSettings):
        super().__init__(timeout=600)
        self.client = client
        self.user_id = user_id
        self.settings = settings
        self.state = settings.home_state or "TX"
        self.level = settings.default_level if settings.default_level in LEVELS else "senate"
        self.races: list[tuple] = []
        self.selected_race = None

    async def refresh(self) -> None:
        found = await list_races(self.client, self.state)
        # Watched races remain navigable even if a source has no list_races endpoint.
        if self.client.poller:
            for race in self.client.poller.default_races:
                if race.key.state == self.state:
                    found.setdefault(str(race.key), (race.key, race.label or str(race.key)))
        self.races = sorted(found.values(), key=lambda item: str(item[0]))
        visible = filter_races(self.races, self.state, self.level)
        if self.selected_race not in {str(key) for key, _ in visible}:
            self.selected_race = str(visible[0][0]) if visible else None
        self._build_controls()

    def _build_controls(self) -> None:
        self.clear_items()
        for level, label in _TAB_LABELS.items():
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary if level == self.level else discord.ButtonStyle.secondary,
                row=0,
            )
            button.callback = self._tab_callback(level)
            self.add_item(button)
        codes = list(STATES)
        groups = [codes[index:index + 25] for index in range(0, len(codes), 25)]
        for row, group in enumerate(groups, start=1):
            select = discord.ui.Select(
                placeholder=f"State: {STATES[group[0]]}–{STATES[group[-1]]}"[:100],
                options=[
                    discord.SelectOption(label=STATES[code], value=code, default=code == self.state)
                    for code in group
                ],
                row=row,
            )
            select.callback = self._state_callback
            self.add_item(select)
        visible = filter_races(self.races, self.state, self.level)[:25]
        if self.selected_race:
            for label in ("Odds", "Polls"):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=4)
                button.callback = self._race_tool_callback(label.casefold())
                self.add_item(button)
            button = discord.ui.Button(label="Choose race", style=discord.ButtonStyle.secondary, row=4)
            button.callback = self._choose_race_callback
            self.add_item(button)
        elif visible:
            select = discord.ui.Select(
                placeholder="Race",
                options=[
                    discord.SelectOption(
                        label=(label or str(key))[:100], value=str(key), default=False
                    )
                    for key, label in visible
                ],
                row=4,
            )
            select.callback = self._race_callback
            self.add_item(select)

    def _tab_callback(self, level: str):
        async def callback(interaction: discord.Interaction):
            if not await self._owns(interaction):
                return
            self.level = level
            self.selected_race = None
            await self._save_and_refresh(interaction)

        return callback

    async def _state_callback(self, interaction: discord.Interaction):
        if not await self._owns(interaction):
            return
        self.state = interaction.data["values"][0]
        self.selected_race = None
        await self._save_and_refresh(interaction)

    async def _race_callback(self, interaction: discord.Interaction):
        if not await self._owns(interaction):
            return
        self.selected_race = interaction.data["values"][0]
        self._build_controls()
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    def _race_tool_callback(self, tool: str):
        async def callback(interaction: discord.Interaction):
            if not await self._owns(interaction) or not self.selected_race:
                return
            key = parse(self.selected_race)
            await interaction.response.defer(ephemeral=True)
            if tool == "odds":
                embed = await self.client.command_data.odds_embed(key)
            else:
                embed = await self.client.command_data.polls_embed(key)
            await interaction.followup.send(embed=embed, ephemeral=True)

        return callback

    async def _choose_race_callback(self, interaction: discord.Interaction):
        if not await self._owns(interaction):
            return
        self.selected_race = None
        self._build_controls()
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    async def _save_and_refresh(self, interaction: discord.Interaction) -> None:
        # Listing races can hit the network: acknowledge within Discord's 3 s deadline first.
        await interaction.response.defer()
        remember_navigation(self.settings, self.state, self.level)
        await self.client.store.save_user(self.settings)
        await self.refresh()
        await interaction.edit_original_response(embed=await self.embed(), view=self)

    async def _owns(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This navigator belongs to another user.", ephemeral=True)
        return False

    async def embed(self) -> discord.Embed:
        if self.selected_race:
            result = await self.client.store.latest_result(parse(self.selected_race))
            if result:
                return render_embed(format_result(result))
        return discord.Embed(
            title=f"{STATES[self.state]} · {_TAB_LABELS[self.level]}",
            description="No current result is available. Choose a state, tab, and race.",
            color=discord.Color.light_gray(),
        )
