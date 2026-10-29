"""Button menus, for people who'd rather click than type slash commands.

The same buttons appear in two places: the menu an admin posts in a channel (/menu_post) and
the private one anyone can open with /menu (unless an admin turned that off with
/menu_private off). Every click answers privately with what the matching slash command
shows; the code paths are the commands' own (the ``show_*`` helpers in discord.py).

The menu view has no timeout and fixed custom_ids, so ElectionBot.setup_hook registers one
instance with ``add_view`` and menus posted before a restart keep working. The race and state
pickers a click opens are ordinary private views that expire after a while.
"""

from __future__ import annotations

import discord

from electionwatch.bot import emoji
from electionwatch.bot.early_vote import watched_states
from electionwatch.bot.logic import is_admin
from electionwatch.racekeys import STATES, describe, parse
from electionwatch.settings import GuildSettings

#: Button -> label. Four a row: the data first, then this server's setup.
TOOLS = {
    "races": "Races",
    "favorites": "Favorites",
    "odds": "Odds",
    "polls": "Polls",
    "earlyvote": "Early vote",
    "faq": "FAQ",
    "settings": "Settings",
    "limits": "Limits",
}
#: Button -> its icon (separate from the label so a renamed label keeps its icon).
ICONS = {
    "races": emoji.RACES,
    "favorites": emoji.FAVORITES,
    "odds": emoji.ODDS,
    "polls": emoji.POLLS,
    "earlyvote": emoji.EARLY_VOTE,
    "faq": emoji.FAQ,
    "settings": emoji.SETTINGS,
    "limits": emoji.LIMITS,
}
CUSTOM_ID = "electionwatch:menu:{}"  # never change these: posted menus carry them
PICKER_TIMEOUT = 600
OPTIONS = 25  # Discord's limit per select
PICKER_SELECTS = 4  # the fifth row is left for a button
ALL = "ALL"  # the "every state" option of the state picker (not a state code)
PRIVATE_MENU_OFF = (
    "The private menu is turned off in this server. Look for a menu an admin posted, or type / to see the commands."
)


def custom_id(tool: str) -> str:
    return CUSTOM_ID.format(tool)


def menu_embed() -> discord.Embed:
    return discord.Embed(
        title=f"{emoji.RACES} Election menu",
        description=(
            "Click a button: only you see the answer.\n\n"
            f"{ICONS['races']} **Races**: browse results by state and office\n"
            f"{ICONS['favorites']} **Favorites**: the races you pinned with /favorite\n"
            f"{ICONS['odds']} **Odds**, {ICONS['polls']} **Polls**: pick a race\n"
            f"{ICONS['earlyvote']} **Early vote**, {ICONS['faq']} **FAQ**: pick a state\n"
            f"{ICONS['settings']} **Settings**, {ICONS['limits']} **Limits**: how this server is set up"
        ),
        color=discord.Color.blurple(),
    )


def _commands():
    # The slash commands' module imports this one for MenuView: import it back lazily.
    from electionwatch.bot import discord as commands

    return commands


async def _report(interaction: discord.Interaction, error: Exception) -> None:
    """A failing click gets the same private "something went wrong" as a failing command."""
    await _commands().handle_app_command_error(interaction, error)


async def _guild_settings(interaction: discord.Interaction) -> GuildSettings | None:
    if interaction.guild_id is None:
        return None
    return await interaction.client.store.get_guild(interaction.guild_id)


class MenuView(discord.ui.View):
    """The menu's buttons. One instance serves every posted menu (see the module docstring)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        for index, (tool, label) in enumerate(TOOLS.items()):
            button = discord.ui.Button(
                label=label,
                emoji=ICONS[tool],
                custom_id=custom_id(tool),
                style=discord.ButtonStyle.secondary if tool in ("settings", "limits") else discord.ButtonStyle.primary,
                row=index // 4,
            )
            button.callback = self._callback(tool)
            self.add_item(button)

    def _callback(self, tool: str):
        async def callback(interaction: discord.Interaction):
            # Every answer reads the database first: acknowledge within Discord's 3 s.
            # thinking=True makes the answer a new private message, also under a public menu.
            await interaction.response.defer(ephemeral=True, thinking=True)
            if await _private_menu_off(interaction):
                await interaction.followup.send(PRIVATE_MENU_OFF, ephemeral=True)
                return
            await HANDLERS[tool](interaction)

        return callback

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _report(interaction, error)


async def _private_menu_off(interaction: discord.Interaction) -> bool:
    """A click on a /menu opened before an admin turned the private menu off. A private menu
    is an ephemeral message; the posted channel menu is not, and always works."""
    message = getattr(interaction, "message", None)
    if message is None or not message.flags.ephemeral:
        return False
    gs = await _guild_settings(interaction)
    return gs is not None and not gs.private_menu and not is_admin(interaction.user, gs)


# --------------------------------------------------------------------------- buttons


async def _races(interaction: discord.Interaction) -> None:
    await _commands().show_races(interaction)


async def _favorites(interaction: discord.Interaction) -> None:
    await _commands().show_races(interaction, level="favorites")


async def _race_picker(interaction: discord.Interaction, tool: str) -> None:
    commands = _commands()
    watched = await commands._watched_keys(interaction)
    favorites = []
    for text in (await commands._user(interaction)).favorites:
        try:
            favorites.append(parse(text))
        except ValueError:
            continue
    picker = RacePicker(tool, watched, favorites)
    if not picker.keys:
        await interaction.followup.send(
            "There are no races to pick from yet: add one with /favorite add.", ephemeral=True
        )
        return
    await interaction.followup.send(picker.content(), view=picker, ephemeral=True)


async def _odds(interaction: discord.Interaction) -> None:
    await _race_picker(interaction, "odds")


async def _polls(interaction: discord.Interaction) -> None:
    await _race_picker(interaction, "polls")


async def _state_picker(interaction: discord.Interaction, tool: str) -> None:
    states = watched_states(await _guild_settings(interaction) or GuildSettings(0))
    picker = StatePicker(tool, states)
    await interaction.followup.send(picker.content(), view=picker, ephemeral=True)


async def _early_vote(interaction: discord.Interaction) -> None:
    await _state_picker(interaction, "earlyvote")


async def _faq(interaction: discord.Interaction) -> None:
    await _state_picker(interaction, "faq")


async def _settings(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.followup.send("Use this in a server.", ephemeral=True)
        return
    gs = await _guild_settings(interaction) or GuildSettings(interaction.guild_id)
    await interaction.followup.send(_commands().settings_message(gs), ephemeral=True)


async def _limits(interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.followup.send("Use this in a server.", ephemeral=True)
        return
    gs = await _guild_settings(interaction) or GuildSettings(interaction.guild_id)
    await interaction.followup.send(_commands().limits_message(interaction.client, gs), ephemeral=True)


HANDLERS = {
    "races": _races,
    "favorites": _favorites,
    "odds": _odds,
    "polls": _polls,
    "earlyvote": _early_vote,
    "faq": _faq,
    "settings": _settings,
    "limits": _limits,
}


# --------------------------------------------------------------------------- pickers


class RacePicker(discord.ui.View):
    """Odds or Polls: the server's watched races, then the user's favorites, 25 a select."""

    def __init__(self, tool: str, watched: list, favorites: list) -> None:
        super().__init__(timeout=PICKER_TIMEOUT)
        self.tool = tool
        self.keys: list = []  # option value i -> race key (LOCAL keys can be over 100 characters)
        self.left_out = 0
        seen: set[str] = set()
        for title, group in (("Watched races", watched), ("Your favorites", favorites)):
            keys = [key for key in group if str(key) not in seen]
            seen.update(str(key) for key in keys)
            chunks = [keys[index : index + OPTIONS] for index in range(0, len(keys), OPTIONS)]
            for number, chunk in enumerate(chunks, start=1):
                if len(self.children) >= PICKER_SELECTS:
                    self.left_out += len(chunk)
                    continue
                start = len(self.keys)
                self.keys += chunk
                select = discord.ui.Select(
                    placeholder=title if len(chunks) == 1 else f"{title} ({number}/{len(chunks)})",
                    options=[
                        discord.SelectOption(label=describe(key)[:100], value=str(start + index))
                        for index, key in enumerate(chunk)
                    ],
                    row=len(self.children),
                )
                select.callback = self._pick
                self.add_item(select)
        if tool == "odds":
            button = discord.ui.Button(label="All watched races", style=discord.ButtonStyle.secondary, row=4)
            button.callback = self._overview
            self.add_item(button)

    def content(self) -> str:
        text = f"Pick a race for {'odds' if self.tool == 'odds' else 'polls'}."
        if self.left_out:
            text += f" {self.left_out} more didn't fit here: use /{self.tool} race:<race> for those."
        return text

    async def _pick(self, interaction: discord.Interaction) -> None:
        key = self.keys[int(interaction.data["values"][0])]
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.tool == "odds":
            await _commands().show_odds(interaction, key)
        else:
            await _commands().show_polls(interaction, key)

    async def _overview(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _commands().show_odds(interaction, None)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _report(interaction, error)


class StatePicker(discord.ui.View):
    """Early vote or FAQ: every state at the top plus the server's states, then all 50 + DC
    in three selects (a select holds 25)."""

    def __init__(self, tool: str, first: list[str]) -> None:
        super().__init__(timeout=PICKER_TIMEOUT)
        self.tool = tool
        everything = "Every state (overview)" if tool == "earlyvote" else "All states (what's covered)"
        options = [discord.SelectOption(label=everything, value=ALL)]
        options += [discord.SelectOption(label=STATES[code], value=code) for code in first if code in STATES]
        self._add(options[:OPTIONS], "This server's states", 0)
        codes = list(STATES)
        size = -(-len(codes) // 3)
        for row, start in enumerate(range(0, len(codes), size), start=1):
            group = codes[start : start + size]
            self._add(
                [discord.SelectOption(label=STATES[code], value=code) for code in group],
                f"States: {STATES[group[0]]}–{STATES[group[-1]]}",
                row,
            )

    def _add(self, options: list, placeholder: str, row: int) -> None:
        select = discord.ui.Select(placeholder=placeholder[:100], options=options, row=row)
        select.callback = self._pick
        self.add_item(select)

    def content(self) -> str:
        if self.tool == "earlyvote":
            return "Pick a state for early-vote numbers. For a party, method, age or county breakdown, use /earlyvote."
        return "Pick a state for its voting FAQ."

    async def _pick(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        code = None if value == ALL else value
        await interaction.response.defer(ephemeral=True, thinking=True)
        if self.tool == "earlyvote":
            await _commands().show_early_vote(interaction, code)
        else:
            await interaction.followup.send(_commands().faq_message(code), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _report(interaction, error)
