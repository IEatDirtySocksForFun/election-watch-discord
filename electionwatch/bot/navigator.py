"""Ephemeral race navigator view. All data comes from Store and ResultsSource."""
from __future__ import annotations

import asyncio
import logging
import time

import discord

from electionwatch.bot.logic import LEVELS, NAV_TABS, filter_races, remember_navigation
from electionwatch.bot.poster import render_embed
from electionwatch.models import Level
from electionwatch.poller import format_result
from electionwatch.racekeys import ALL_JURISDICTIONS, describe, parse
from electionwatch.settings import UserSettings

logger = logging.getLogger(__name__)
_TAB_LABELS = {
    "congressional": "Congressional", "senate": "Senate", "state": "State", "local": "Local",
    "favorites": "★ Favorites",
}
RACE_PAGE = 23   # race-select options per page when two are taken by previous/next

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
    listed = []
    for source, races in zip(sources, answers):
        if isinstance(races, BaseException):
            logger.warning("Could not list %s races from %s: %s", state, source.name, type(races).__name__)
            continue
        listed.append((source, races))
    # Local race keys are slugs of each source's own contest name, so civicAPI's and a county
    # feed's key for the same race differ: where an official feed lists local races, the
    # fallbacks' local races would be duplicates under other names.
    official_local = any(
        key.level is Level.LOCAL
        for source, races in listed if not getattr(source, "fallback", False)
        for key, _ in races
    )
    for source, races in listed:
        for key, label in races:
            if official_local and key.level is Level.LOCAL and getattr(source, "fallback", False):
                continue
            found.setdefault(str(key), (key, label))
    return found


class RaceNavigator(discord.ui.View):
    def __init__(self, client, user_id: int, settings: UserSettings):
        super().__init__(timeout=600)
        self.client = client
        self.user_id = user_id
        self.settings = settings
        self.state = settings.home_state or "TX"
        self.level = settings.default_level if settings.default_level in NAV_TABS else "senate"
        self.races: list[tuple] = []
        self.selected_race = None
        self._race_choices: list[str] = []   # race-select option value i -> race key
        self.page = 0                          # race-select page when a tab has > 25 races

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

    async def refresh(self) -> None:
        found = await list_races(self.client, self.state)
        # Watched races remain navigable even if a source has no list_races endpoint.
        if self.client.poller:
            for race in self.client.poller.default_races:
                if race.key.state == self.state:
                    found.setdefault(str(race.key), (race.key, race.label or str(race.key)))
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
        groups = [codes[index:index + 25] for index in range(0, len(codes), 25)]
        for row, group in enumerate(groups, start=1):
            select = discord.ui.Select(
                placeholder=f"State: {ALL_JURISDICTIONS[group[0]]}–{ALL_JURISDICTIONS[group[-1]]}"[:100],
                options=[
                    discord.SelectOption(
                        label=ALL_JURISDICTIONS[code], value=code, default=code == self.state
                    )
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
            shown = visible if pages == 1 else visible[self.page * RACE_PAGE:(self.page + 1) * RACE_PAGE]
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
        if self.level == "favorites":   # picking a state leaves the cross-state favorites tab
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
        if self.level == "favorites":
            title = _TAB_LABELS["favorites"]
            description = (
                "No current result is available. Choose a favorite race."
                if self.settings.favorites else "No favorites yet: add one with /favorite add."
            )
        else:
            title = f"{ALL_JURISDICTIONS[self.state]} · {_TAB_LABELS[self.level]}"
            description = "No current result is available. Choose a state, tab, and race."
        return discord.Embed(
            title=title,
            description=description,
            color=discord.Color.light_gray(),
        )
