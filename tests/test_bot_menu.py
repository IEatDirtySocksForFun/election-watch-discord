"""Button menus (electionwatch/bot/menu.py): /menu, /menu_post, /menu_remove, /menu_private.

The wire-level checks (real discord.py dispatch, persistent view lookup, ephemeral flags on
the JSON Discord receives) are in test_interaction_payloads.py. These use the lighter fakes:

  [x] the menu view is persistent: no timeout, fixed custom_ids, registered by setup_hook
  [x] /menu_post, /menu_remove, /menu_private are admin-only (Manage Server or the admin role)
  [x] /menu_post defaults to the current channel, replaces the old menu, survives a failed
      post or delete; /menu_remove forgets a menu someone already deleted
  [x] /menu: on by default, off for members after /menu_private off, admins and DMs keep it
  [x] each button routes to its command's code path; every reply private, within limits
  [x] the race picker splits long lists into selects of 25 and says what didn't fit
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import discord
import httpx
import pytest
from discord.ext import tasks
from fake_civicapi_early_vote import fixture_client, mock_http_client
from fake_interaction_admin import ADMIN_ROLE_ID, GUILD_ID, ROLES, make_interaction
from fake_interaction_data import FakeInteraction, check_embed, check_view, make_client

from electionwatch.bot.discord import (
    ElectionBot,
    menu_command,
    menu_post_command,
    menu_private_command,
    menu_remove_command,
)
from electionwatch.bot.menu import (
    PRIVATE_MENU_OFF,
    TOOLS,
    MenuView,
    RacePicker,
    StatePicker,
    custom_id,
)
from electionwatch.bot.navigator import RaceNavigator
from electionwatch.models import to_doc
from electionwatch.racekeys import STATES, parse
from electionwatch.settings import GuildSettings, UserSettings
from electionwatch.store.memory import MemoryStore

REFUSE_PLAIN = "Manage Server permission or the configured admin role is required"
REFUSE_DM = "Use this command in a server"
OLD_CHANNEL, OLD_MESSAGE, HERE = 10, 20, 30


def response_error(kind, status: int):
    return kind(SimpleNamespace(status=status, reason="error"), "error")


class PostChannel:
    """A text channel the bot posts the menu in."""

    def __init__(self, channel_id: int, fail: Exception | None = None) -> None:
        self.id = channel_id
        self.fail = fail
        self.sent: list = []

    async def send(self, *, embed=None, view=None):
        if self.fail is not None:
            raise self.fail
        check_embed(embed)
        check_view(view)
        self.sent.append((embed, view))
        return SimpleNamespace(id=self.id * 100 + len(self.sent))


class MenuClient:
    """What the menu commands need from ElectionBot: the store, and deleting a message."""

    def __init__(self, store) -> None:
        self.store = store
        self.deleted: list[tuple[int, int]] = []
        self.delete_error: Exception | None = None

    def get_partial_messageable(self, channel_id: int):
        client = self

        async def delete(message_id):
            if client.delete_error is not None:
                raise client.delete_error
            client.deleted.append((channel_id, message_id))

        return SimpleNamespace(
            get_partial_message=lambda message_id: SimpleNamespace(delete=lambda: delete(message_id))
        )


def baseline(**kw) -> GuildSettings:
    return GuildSettings(
        GUILD_ID, admin_role_id=ADMIN_ROLE_ID, menu_channel_id=OLD_CHANNEL, menu_message_id=OLD_MESSAGE, **kw
    )


async def invoke(command, interaction, **kwargs):
    """Run a command callback: it must answer exactly once, privately, within 2000 characters."""
    await command.callback(interaction, **kwargs)
    assert len(interaction.replies) == 1, interaction.sent
    assert all(sent.ephemeral for sent in interaction.sent), interaction.sent
    reply = interaction.replies[0]
    assert reply.content is None or len(reply.content) <= 2000
    if reply.embed is not None:
        check_embed(reply.embed)
    if reply.view is not None:
        check_view(reply.view)
    return reply


# --------------------------------------------------------------------------- persistence


def test_menu_view_is_persistent_with_fixed_custom_ids():
    view = MenuView()
    assert view.timeout is None and view.is_persistent()
    ids = [item.custom_id for item in view.children]
    assert ids == [f"electionwatch:menu:{tool}" for tool in TOOLS]
    assert ids == [item.custom_id for item in MenuView().children]  # the same after a restart
    assert [item.label for item in view.children] == list(TOOLS.values())
    check_view(view)


async def test_setup_hook_registers_the_menu_before_connecting(monkeypatch):
    monkeypatch.setattr(tasks.Loop, "start", lambda self, *args, **kwargs: None)
    async with httpx.AsyncClient() as http:
        bot = ElectionBot(MemoryStore(), {}, http)

        async def sync(**_):
            return []

        bot.tree.sync = sync
        try:
            await bot.setup_hook()
            (view,) = bot.persistent_views
            assert isinstance(view, MenuView)
            assert {item.custom_id for item in view.children} == {custom_id(tool) for tool in TOOLS}
        finally:
            for view in bot.persistent_views:
                view.stop()
            await bot.close()


# --------------------------------------------------------------------------- admin commands

# (id, command, kwargs, check(guild_settings, client, channel, reply))
ADMIN_SPECS = [
    (
        "post here",
        menu_post_command,
        {},
        lambda gs, client, here, reply: (
            (gs.menu_channel_id, gs.menu_message_id) == (HERE, HERE * 100 + 1)
            and client.deleted == [(OLD_CHANNEL, OLD_MESSAGE)]
            and "The old one is gone" in reply
        ),
    ),
    (
        "post elsewhere",
        menu_post_command,
        {"channel": PostChannel(55)},
        lambda gs, client, here, reply: gs.menu_channel_id == 55 and not here.sent and "<#55>" in reply,
    ),
    (
        "remove",
        menu_remove_command,
        {},
        lambda gs, client, here, reply: (
            (gs.menu_channel_id, gs.menu_message_id) == (None, None)
            and client.deleted == [(OLD_CHANNEL, OLD_MESSAGE)]
            and reply == f"Menu removed from <#{OLD_CHANNEL}>."
        ),
    ),
    (
        "private off",
        menu_private_command,
        {"enabled": "off"},
        lambda gs, client, here, reply: gs.private_menu is False and reply == "private menu off",
    ),
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("spec", ADMIN_SPECS, ids=[spec[0] for spec in ADMIN_SPECS])
async def test_menu_admin_commands_are_admin_only(spec, role):
    _, command, kwargs, check = spec
    store = MemoryStore()
    await store.save_guild(baseline())
    before = to_doc(await store.get_guild(GUILD_ID))
    client = MenuClient(store)
    interaction = make_interaction(client, role)
    interaction.channel = PostChannel(HERE)

    reply = await invoke(command, interaction, **copy.deepcopy(kwargs))

    gs = await store.get_guild(GUILD_ID)
    if role in ("manage", "role_admin"):
        assert check(gs, client, interaction.channel, reply.content), reply.content
    else:
        assert (REFUSE_DM if role == "dm" else REFUSE_PLAIN) in reply.content
        assert to_doc(gs) == before
        assert not interaction.channel.sent and not client.deleted


async def test_menu_post_first_time_and_a_failed_post():
    store = MemoryStore()
    client = MenuClient(store)
    interaction = make_interaction(client, "manage")
    interaction.channel = PostChannel(HERE)
    reply = await invoke(menu_post_command, interaction)
    assert reply.content == f"Menu posted in <#{HERE}>. Anyone can click it; the answers are private."
    ((embed, view),) = interaction.channel.sent
    assert embed.title == "Election menu" and isinstance(view, MenuView)

    interaction = make_interaction(client, "manage")
    interaction.channel = PostChannel(HERE + 1, fail=response_error(discord.Forbidden, 403))
    reply = await invoke(menu_post_command, interaction)
    assert f"I couldn't post in <#{HERE + 1}>" in reply.content
    gs = await store.get_guild(GUILD_ID)
    assert (gs.menu_channel_id, gs.menu_message_id) == (HERE, HERE * 100 + 1)  # the old one stays
    assert not client.deleted


async def test_menu_post_says_when_the_old_menu_could_not_be_deleted():
    store = MemoryStore()
    await store.save_guild(baseline())
    client = MenuClient(store)
    client.delete_error = response_error(discord.Forbidden, 403)
    interaction = make_interaction(client, "manage")
    interaction.channel = PostChannel(HERE)
    reply = await invoke(menu_post_command, interaction)
    assert f"I couldn't delete the old one in <#{OLD_CHANNEL}>" in reply.content
    assert (await store.get_guild(GUILD_ID)).menu_channel_id == HERE


async def test_menu_post_in_a_channel_the_bot_cannot_post_in():
    interaction = make_interaction(MenuClient(MemoryStore()), "manage")
    interaction.channel = None  # e.g. a channel type without messages
    reply = await invoke(menu_post_command, interaction)
    assert reply.content == "Pick a text channel for the menu."


@pytest.mark.parametrize(
    "error, expected, forgotten",
    [
        (response_error(discord.NotFound, 404), "The menu was already deleted.", True),
        (response_error(discord.Forbidden, 403), f"I couldn't delete the menu in <#{OLD_CHANNEL}>", False),
    ],
)
async def test_menu_remove_when_deleting_fails(error, expected, forgotten):
    store = MemoryStore()
    await store.save_guild(baseline())
    client = MenuClient(store)
    client.delete_error = error
    reply = await invoke(menu_remove_command, make_interaction(client, "manage"))
    assert expected in reply.content
    gs = await store.get_guild(GUILD_ID)
    assert (gs.menu_message_id is None) is forgotten


async def test_menu_remove_without_a_posted_menu():
    store = MemoryStore()
    reply = await invoke(menu_remove_command, make_interaction(MenuClient(store), "manage"))
    assert reply.content == "There's no posted menu in this server."
    assert await store.get_guild(GUILD_ID) is None


# --------------------------------------------------------------------------- /menu


@pytest.mark.parametrize("private_menu", [True, False, None], ids=["on", "off", "never-set"])
@pytest.mark.parametrize("role", ROLES)
async def test_private_menu_on_and_off(role, private_menu):
    store = MemoryStore()
    if private_menu is not None:
        await store.save_guild(GuildSettings(GUILD_ID, admin_role_id=ADMIN_ROLE_ID, private_menu=private_menu))
    reply = await invoke(menu_command, make_interaction(SimpleNamespace(store=store), role))
    if private_menu is False and role == "plain":
        assert reply.content == PRIVATE_MENU_OFF and reply.view is None
    else:
        assert isinstance(reply.view, MenuView) and reply.embed.title == "Election menu"
    if private_menu is None:
        assert await store.get_guild(GUILD_ID) is None  # opening the menu writes nothing


async def test_menu_private_toggle_round_trip():
    store = MemoryStore()
    await store.save_guild(GuildSettings(GUILD_ID, admin_role_id=ADMIN_ROLE_ID))
    client = SimpleNamespace(store=store)
    assert (await invoke(menu_private_command, make_interaction(client, "manage"), enabled="off")).content == (
        "private menu off"
    )
    assert (await invoke(menu_command, make_interaction(client, "plain"))).content == PRIVATE_MENU_OFF
    assert (await invoke(menu_private_command, make_interaction(client, "role_admin"), enabled="on")).content == (
        "private menu on"
    )
    assert isinstance((await invoke(menu_command, make_interaction(client, "plain"))).view, MenuView)


# --------------------------------------------------------------------------- buttons


@pytest.fixture
async def client():
    store = MemoryStore()
    await store.save_guild(GuildSettings(1, important=["TX SEN", "GA GOV", "NC GOV"]))
    await store.save_user(UserSettings(9, favorites=["PA SEN", "TX SEN"]))
    async with mock_http_client() as http:
        bot = make_client(store)
        bot.early_vote = fixture_client(http)
        yield bot


async def click(client, tool: str, **kw) -> tuple[FakeInteraction, object]:
    interaction = FakeInteraction(client, kind="component", **kw)
    button = next(item for item in MenuView().children if item.custom_id == custom_id(tool))
    await button.callback(interaction)
    reply = interaction.assert_ephemeral_flow()
    assert interaction.kinds == ["defer", "followup"]
    assert interaction.events[0].thinking  # a new private message, also under a public menu
    return interaction, reply


async def pick(client, view, select_index: int, value: str) -> object:
    interaction = FakeInteraction(client, kind="component", values=[value])
    select = [item for item in view.children if isinstance(item, discord.ui.Select)][select_index]
    await select.callback(interaction)
    reply = interaction.assert_ephemeral_flow()
    assert interaction.kinds == ["defer", "followup"]
    return reply


async def test_races_button_opens_the_navigator(client):
    _, reply = await click(client, "races")
    assert isinstance(reply.view, RaceNavigator) and reply.view.watched == ["TX SEN", "GA GOV", "NC GOV"]
    assert reply.view.level == "senate"


async def test_favorites_button_opens_the_favorites_tab(client):
    _, reply = await click(client, "favorites")
    assert isinstance(reply.view, RaceNavigator) and reply.view.level == "favorites"
    assert reply.view.selected_race == "PA SEN"


@pytest.mark.parametrize("tool", ["odds", "polls"])
async def test_odds_and_polls_buttons_offer_watched_races_then_favorites(client, tool):
    _, reply = await click(client, tool)
    picker = reply.view
    label = "betting markets" if tool == "odds" else tool
    assert isinstance(picker, RacePicker) and reply.content == f"Pick a race for {label}."
    assert [str(key) for key in picker.keys] == ["TX SEN", "GA GOV", "NC GOV", "PA SEN"]  # no duplicate TX SEN
    assert [item.placeholder for item in picker.children if isinstance(item, discord.ui.Select)] == [
        "Watched races",
        "Your favorites",
    ]
    buttons = [item.label for item in picker.children if isinstance(item, discord.ui.Button)]
    assert buttons == (["All watched races"] if tool == "odds" else [])
    answer = await pick(client, picker, 1, "3")
    title = "Odds" if tool == "odds" else "Polls"
    assert answer.embed.title.startswith(f"{title} · ") and "Pennsylvania" in answer.embed.title


async def test_odds_overview_button_shows_the_watched_races(client):
    _, reply = await click(client, "odds")
    overview = next(item for item in reply.view.children if isinstance(item, discord.ui.Button))
    interaction = FakeInteraction(client, kind="component")
    await overview.callback(interaction)
    assert interaction.assert_ephemeral_flow().embed.title == "Watched race odds"


@pytest.mark.parametrize("tool", ["earlyvote", "faq"])
async def test_early_vote_and_faq_buttons_offer_the_servers_states_then_all(client, tool):
    _, reply = await click(client, tool)
    picker = reply.view
    assert isinstance(picker, StatePicker)
    selects = [item for item in picker.children if isinstance(item, discord.ui.Select)]
    assert [option.value for option in selects[0].options] == ["ALL", "GA", "NC", "TX"]
    assert [option.value for select in selects[1:] for option in select.options] == list(STATES)
    answer = await pick(client, picker, 0, "NC")
    if tool == "earlyvote":
        assert "North Carolina" in answer.embed.title
    else:
        assert answer.content.startswith("# North Carolina")
    answer = await pick(client, picker, 0, "ALL")
    if tool == "earlyvote":
        assert answer.embed.title == "Early vote · United States"
    else:
        assert "State FAQs" in answer.content


async def test_settings_and_limits_buttons(client):
    _, reply = await click(client, "settings")
    assert reply.content.startswith("```yaml\n") and "private_menu: true" in reply.content
    assert "menu_message_id" in reply.content
    _, reply = await click(client, "limits")
    assert "Data refresh" in reply.content
    for tool in ("settings", "limits"):
        _, reply = await click(client, tool, guild_id=None)
        assert reply.content == "Use this in a server."


async def test_buttons_in_a_dm_use_the_default_races(client):
    from electionwatch.config import load_races

    defaults = [str(race.key) for race in load_races()]
    _, reply = await click(client, "odds", guild_id=None)
    assert [str(key) for key in reply.view.keys] == defaults + [key for key in ("PA SEN",) if key not in defaults]
    _, reply = await click(client, "earlyvote", guild_id=None)
    first = [item for item in reply.view.children if isinstance(item, discord.ui.Select)][0]
    assert {option.value for option in first.options[1:]} == {parse(key).state for key in defaults}


async def test_menu_pickers_respect_the_command_cooldowns(client):
    from electionwatch.bot.cooldown import Cooldowns
    from electionwatch.ratelimit import RateBook

    client.cooldowns = Cooldowns(user_cooldown=60, clock=lambda: 0.0)
    client.rates = RateBook.from_env({}, clock=lambda: 0.0)
    _, reply = await click(client, "polls")
    assert (await pick(client, reply.view, 0, "0")).embed is not None
    again = await pick(client, reply.view, 0, "0")
    assert again.content.startswith("You can ask for this again in 60 s")
    _, reply = await click(client, "earlyvote")
    assert (await pick(client, reply.view, 0, "TX")).embed is not None
    # Texas again, from the all-states select this time: still the same state.
    selects = [item for item in reply.view.children if isinstance(item, discord.ui.Select)]
    texas = next(index for index, select in enumerate(selects) if index and "TX" in [o.value for o in select.options])
    assert "You can ask for this again" in (await pick(client, reply.view, texas, "TX")).content


# --------------------------------------------------------------------------- picker limits


def test_race_picker_splits_long_lists_and_says_what_did_not_fit():
    watched = [parse(f"CA-{district:02d}") for district in range(1, 53)]
    watched += [parse("TX LOCAL " + "a-very-long-local-contest-name-" * 5 + str(index)) for index in range(40)]
    favorites = [parse("TX SEN"), parse("CA-01")]
    picker = RacePicker("odds", watched, favorites)
    check_view(picker)
    selects = [item for item in picker.children if isinstance(item, discord.ui.Select)]
    assert len(selects) == 4 and all(len(select.options) <= 25 for select in selects)
    assert [select.placeholder for select in selects] == [f"Watched races ({number}/4)" for number in range(1, 5)]
    assert len(picker.keys) == 92 and picker.left_out == 1  # TX SEN; CA-01 is already watched
    assert "use /bettingmarkets race:<race> for those" in picker.content()
    assert all(len(option.value) <= 3 for select in selects for option in select.options)


def test_state_picker_fits_discord_limits_with_every_state_watched():
    picker = StatePicker("earlyvote", list(STATES))
    check_view(picker)
    first = [item for item in picker.children if isinstance(item, discord.ui.Select)][0]
    assert len(first.options) == 25 and first.options[0].value == "ALL"
