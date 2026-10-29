from types import SimpleNamespace

import pytest

from electionwatch.bot.logic import (
    change_calls,
    change_favorite,
    change_odds,
    change_posting,
    change_watch,
    faq_embed,
    filter_races,
    is_admin,
    remember_navigation,
)
from electionwatch.racekeys import parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings


def test_admin_permission_or_role():
    gs = GuildSettings(1)
    manager = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True), roles=[])
    ordinary = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False), roles=[])
    assert is_admin(manager, gs)
    assert not is_admin(ordinary, gs)
    gs.admin_role_id = 42
    ordinary.roles = [SimpleNamespace(id=42)]
    assert is_admin(ordinary, gs)


def test_watch_posting_calls_and_odds_validation():
    gs = GuildSettings(1)
    assert "TX SEN" in change_watch(gs, "list")
    change_watch(gs, "add", "NC-1")
    assert gs.important == ["TX SEN", "NC-01"]
    change_watch(gs, "remove", "NC-01")
    assert gs.important == ["TX SEN"]

    change_posting(gs, "interval", 15)
    assert gs.post_mode is PostMode.INTERVAL and gs.interval_minutes == 15
    change_posting(gs, "immediate")
    assert gs.post_mode is PostMode.IMMEDIATE
    with pytest.raises(ValueError):
        change_posting(gs, "interval", 0)

    change_calls(gs, "ap, cnn NBC", True)
    assert gs.call_outlets == ["AP", "CNN", "NBC"]
    with pytest.raises(ValueError):
        change_calls(gs, "invalid", True)
    change_odds(gs, True, 7.5, 30)
    assert gs.odds_alerts and gs.odds_swing_pts == 7.5 and gs.odds_window_minutes == 30


def test_setup_sets_channel_and_optional_admin_role():
    from electionwatch.bot.logic import change_setup

    gs = GuildSettings(1)
    message = change_setup(gs, 2)
    assert gs.channel_id == 2 and gs.admin_role_id is None
    assert "<#2>" in message

    message = change_setup(gs, 3, 42)
    assert gs.channel_id == 3 and gs.admin_role_id == 42
    assert "<@&42>" in message
    with pytest.raises(ValueError):
        change_setup(gs, 0)


def test_settings_card_groups_the_settings():
    from electionwatch.bot.logic import settings_card

    gs = GuildSettings(1, channel_id=2, post_mode=PostMode.INTERVAL, interval_minutes=15)
    card = settings_card(gs)
    fields = {name: value for name, value, _ in card.fields}
    assert card.title == "⚙️ Server settings"
    assert "Channel: <#2>" in fields["📢 Posting"] and "every 15 minutes" in fields["📢 Posting"]


def test_favorites_navigation_and_faq_are_pure_data_operations():
    us = UserSettings(9)
    change_favorite(us, "add", "Texas Senate")
    change_favorite(us, "add", "TX SEN")
    assert us.favorites == ["TX SEN"]
    change_favorite(us, "remove", "TX SEN")
    assert us.favorites == []

    races = [(parse("TX SEN"), "Senate"), (parse("TX GOV"), "Governor"), (parse("NC-01"), "House")]
    assert filter_races(races, "TX", "state") == [(races[1][0], "Governor")]
    remember_navigation(us, "NC", "congressional")
    assert us.home_state == "NC" and us.default_level == "congressional"
    embed = faq_embed({"TX": {"voting": "Bring ID"}}, {"AP": "Associated Press"}, "TX")
    assert {"name": "ℹ️ Voting", "value": "Bring ID", "inline": False} in embed["fields"]
    assert "No FAQ data for Texas" in faq_embed({}, {}, "TX")["description"]


def test_faq_without_a_state_lists_available_states_and_fits_one_embed():
    from electionwatch.racekeys import STATES

    states = {code: {"official_results_url": "https://example.test", "notes": "n" * 300} for code in STATES}
    outlets = {"AP": {"name": "Associated Press", "results_url": "https://ap.test"}}
    embed = faq_embed(states, outlets)
    for code in ("AL", "CA", "DC", "TX", "WY"):
        assert f"`{code}`" in embed["description"]
    assert [field["name"] for field in embed["fields"]] == ["🏁 Associated Press (AP)"]
    assert "n" * 300 not in str(embed)  # not a squeezed dump of every state

    one = faq_embed(states, outlets, "TX")
    assert one["title"] == "🗳️ Texas"
    assert one["description"].splitlines()[0] == "🔗 [Official results](<https://example.test>)"
    assert {"name": "💬 Good to know", "value": "n" * 300, "inline": False} in one["fields"]


def test_faq_embed_stays_within_discord_limits():
    long = "word " * 400  # 2000 characters: each field clips at 1024, the total at 6000
    record = {key: long for key in ("count_order", "mail_deadline", "mail_processing", "typical_call_time")}
    record |= {"recount_threshold": long, "ranked_choice": True, "notes": long, "sources": ["https://a.test/x"]}
    embed = faq_embed({"TX": record}, {}, "TX")
    assert all(len(field["value"]) <= 1024 for field in embed["fields"])
    total = len(embed["title"]) + len(embed.get("description", "")) + len(embed["footer"]["text"])
    assert total + sum(len(field["name"]) + len(field["value"]) for field in embed["fields"]) <= 6000
    names = [field["name"] for field in embed["fields"]]
    assert "📚 Sources" not in names and "💬 Good to know" not in names  # dropped first, in that order
    assert {"name": "🔢 RCV", "value": "✅ Yes", "inline": False} in embed["fields"]


def test_settings_card_and_watch_list_fit_discord():
    from electionwatch.bot.logic import change_watch, settings_card

    gs = GuildSettings(1, important=[f"TX HD-{district_number:02d}" for district_number in range(1, 151)])
    card = settings_card(gs)
    assert all(len(value) <= 1024 for _, value, _ in card.fields) and len(card.text()) <= 6000
    assert len(change_watch(gs, "list")) <= 2000


def test_settings_card_shows_every_setting_even_with_hundreds_of_watched_races():
    # Found by replay/command_sim (2020/2024, all races watched): a long watch list once
    # pushed post_mode/paused out of the reply. Each has a field of its own now.
    from electionwatch.bot.logic import settings_card

    gs = GuildSettings(
        1,
        channel_id=2,
        important=[f"TX HD-{district_number:03d}" for district_number in range(1, 151)],
        post_mode=PostMode.INTERVAL,
        interval_minutes=10,
        paused=True,
    )
    fields = {name: value for name, value, _ in settings_card(gs).fields}
    assert "every 10 minutes" in fields["📢 Posting"] and "⏸️ paused" in fields["📢 Posting"]
    watched = fields["👁️ Watched races (150)"]
    assert watched.startswith("TX HD-001 · TX HD-002") and watched.endswith("more") and len(watched) <= 1024
