"""Ephemeral race navigator view (/races). It reads only the database: race lists come from
the background refresh in electionwatch/racelists.py, results from the poller."""

from __future__ import annotations

import logging

import discord

from electionwatch.bot.logic import NAV_TABS, filter_races, remember_navigation
from electionwatch.bot.poster import render_embed
from electionwatch.poller import format_result
from electionwatch.racekeys import ALL_JURISDICTIONS, describe, parse
from electionwatch.racelists import RaceLists
from electionwatch.settings import UserSettings

logger = logging.getLogger(__name__)
_TAB_LABELS = {
    "congressional": "Congressional",
    "senate": "Senate",
    "state": "State",
    "local": "Local",
    "favorites": "★ Favorites",
}
RACE_PAGE = 23  # race-select options per page when two are taken by previous/next
LOADING = (
    "{state}'s race list is still loading: race lists are fetched in the background and "
    "this state is now first in line. Check back in a few minutes; meanwhile this shows "
    "the server's watched races and your favorites."
)


def race_lists(client) -> RaceLists:
    """The bot's RaceLists, or a read-only one over the client's store for a bot stand-in."""
    lists = getattr(client, "race_lists", None)
    if lists is None:
        lists = RaceLists(client.store, client.sources, getattr(client, "http_client", None))
        client.race_lists = lists
    return lists


async def list_races(client, state: str) -> dict[str, tuple]:
    """The stored races of a state, as {key: (key, label)}. Never calls a source."""
    found, _ = await race_lists(client).stored(state)
    return found


class RaceNavigator(discord.ui.View):
    def __init__(self, client, user_id: int, settings: UserSettings, watched: list[str] | None = None):
        super().__init__(timeout=600)
        self.client = client
        self.user_id = user_id
        self.settings = settings
        self.watched = watched  # the server's watch list; None = the default races
        self.loading = False  # no source has stored this state's list yet
        self.state = settings.home_state or "TX"
        self.level = settings.default_level if settings.default_level in NAV_TABS else "senate"
        self.races: list[tuple] = []
        self.selected_race = None
        self._race_choices: list[str] = []  # race-select option value i -> race key
        self.page = 0  # race-select page when a tab has > 25 races

    def _visible(self) -> list[tuple]:
        """Races on the current tab: the user's favorites (any state) or the state's races."""
        if self.level == "favorites":
            keys = []
            for text in self.settings.favorites:
                try:
                    keys.append(parse(text))
                except ValueError:
                    continue
            return [(key, describe(key)) for key in keys]
        return filter_races(self.races, self.state, self.level)

    def _known_races(self) -> list[tuple]:
        """The server's watched races and the user's favorites: navigable even when no source
        lists them, or while the state's list is still loading."""
        known = []
        if self.watched is not None:
            texts = list(self.watched)
        else:
            poller = getattr(self.client, "poller", None)
            known += [(race.key, race.label or str(race.key)) for race in getattr(poller, "default_races", ())]
            texts = []
        for text in texts + list(self.settings.favorites):
            try:
                key = parse(text)
            except ValueError:
                continue
            known.append((key, describe(key)))
        return known

    async def refresh(self) -> None:
        lists = race_lists(self.client)
        lists.want(self.state)
        found, self.loading = await lists.stored(self.state)
        for key, label in self._known_races():
            if key.state == self.state:
                found.setdefault(str(key), (key, label))
        self.races = sorted(found.values(), key=lambda item: str(item[0]))
        visible = self._visible()
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
        # 51 states + DC and 5 territories = 56 entries: three selects of at most 25.
        codes = list(ALL_JURISDICTIONS)
        groups = [codes[index : index + 25] for index in range(0, len(codes), 25)]
        for row, group in enumerate(groups, start=1):
            select = discord.ui.Select(
                placeholder=f"State: {ALL_JURISDICTIONS[group[0]]}–{ALL_JURISDICTIONS[group[-1]]}"[:100],
                options=[
                    discord.SelectOption(label=ALL_JURISDICTIONS[code], value=code, default=code == self.state)
                    for code in group
                ],
                row=row,
            )
            select.callback = self._state_callback
            self.add_item(select)
        visible = self._visible()
        if self.selected_race:
            for label in ("Odds", "Polls"):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=4)
                button.callback = self._race_tool_callback(label.casefold())
                self.add_item(button)
            button = discord.ui.Button(label="Choose race", style=discord.ButtonStyle.secondary, row=4)
            button.callback = self._choose_race_callback
            self.add_item(button)
        elif visible:
            # Option values are capped at 100 characters and LOCAL keys carry a slug of the
            # full contest name, which can be longer: send the list position instead.
            # A select holds 25 options: longer lists are paged with previous/next options.
            pages = 1 if len(visible) <= 25 else -(-len(visible) // RACE_PAGE)
            self.page = min(self.page, pages - 1)
            shown = visible if pages == 1 else visible[self.page * RACE_PAGE : (self.page + 1) * RACE_PAGE]
            self._race_choices = [str(key) for key, _ in shown]
            options = [
                discord.SelectOption(label=(label or str(key))[:100], value=str(index))
                for index, (key, label) in enumerate(shown)
            ]
            if self.page > 0:
                options.insert(0, discord.SelectOption(label="◀ Previous races", value="prev"))
            if self.page < pages - 1:
                options.append(discord.SelectOption(label="Next races ▶", value="next"))
            select = discord.ui.Select(
                placeholder="Race" if pages == 1 else f"Race · page {self.page + 1}/{pages}",
                options=options,
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
            self.page = 0
            await self._save_and_refresh(interaction)

        return callback

    async def _state_callback(self, interaction: discord.Interaction):
        if not await self._owns(interaction):
            return
        self.state = interaction.data["values"][0]
        self.selected_race = None
        self.page = 0
        if self.level == "favorites":  # picking a state leaves the cross-state favorites tab
            self.level = "senate"
        await self._save_and_refresh(interaction)

    async def _race_callback(self, interaction: discord.Interaction):
        if not await self._owns(interaction):
            return
        value = interaction.data["values"][0]
        if value in ("prev", "next"):
            self.page += 1 if value == "next" else -1
        else:
            self.selected_race = self._race_choices[int(value)]
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
        # Saving the settings and reading the lists are database round trips: acknowledge
        # within Discord's 3 s deadline first.
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
        if self.level == "favorites":
            title = _TAB_LABELS["favorites"]
            description = (
                "No current result is available. Choose a favorite race."
                if self.settings.favorites
                else "No favorites yet: add one with /favorite add."
            )
        else:
            title = f"{ALL_JURISDICTIONS[self.state]} · {_TAB_LABELS[self.level]}"
            description = "No current result is available. Choose a state, tab, and race."
            if self.loading:
                description += "\n\n" + LOADING.format(state=ALL_JURISDICTIONS[self.state])
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.light_gray(),
        )
