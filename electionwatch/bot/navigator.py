"""Ephemeral race navigator view. All data comes from Store and ResultsSource."""
from __future__ import annotations

import logging

import discord

from electionwatch.bot.logic import LEVELS, filter_races, remember_navigation
from electionwatch.bot.poster import render_embed
from electionwatch.poller import format_result
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import UserSettings

logger = logging.getLogger(__name__)
_TAB_LABELS = {"congressional": "Congressional", "senate": "Senate", "state": "State", "local": "Local"}


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
        found: dict[str, tuple] = {}
        for source in self.client.sources.get("results", []):
            if self.state not in source.states or not source.available():
                continue
            try:
                for key, label in await source.list_races(self.state, self.client.http_client):
                    found[str(key)] = (key, label)
            except Exception:
                logger.exception("Could not list races from %s", source.name)
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
        if visible:
            select = discord.ui.Select(
                placeholder="Race",
                options=[
                    discord.SelectOption(
                        label=(label or str(key))[:100], value=str(key), default=str(key) == self.selected_race
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
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    async def _save_and_refresh(self, interaction: discord.Interaction) -> None:
        remember_navigation(self.settings, self.state, self.level)
        await self.client.store.save_user(self.settings)
        await self.refresh()
        await interaction.response.edit_message(embed=await self.embed(), view=self)

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
