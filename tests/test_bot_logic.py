from types import SimpleNamespace

import pytest

from electionwatch.bot.logic import (
    change_calls,
    change_favorite,
    change_odds,
    change_posting,
    change_watch,
    faq_text,
    filter_races,
    is_admin,
    remember_navigation,
)
from electionwatch.models import Level
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


def test_settings_dump_is_plain_yaml():
    import yaml

    from electionwatch.bot.logic import settings_dump

    gs = GuildSettings(1, channel_id=2, post_mode=PostMode.INTERVAL, interval_minutes=15)
    data = yaml.safe_load(settings_dump(gs))
    assert data["guild_id"] == 1 and data["post_mode"] == "interval"
    assert data["interval_minutes"] == 15


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
    text = faq_text({"TX": {"voting": "Bring ID"}}, {"AP": "Associated Press"}, "TX")
    assert "Bring ID" in text and "Associated Press" in text
    assert faq_text({}, {}, "TX").startswith("No FAQ data for Texas")


def test_faq_without_a_state_lists_available_states_and_fits_one_message():
    from electionwatch.racekeys import STATES

    states = {
        code: {"official_results_url": "https://example.test", "notes": "n" * 300}
        for code in STATES
    }
    outlets = {"AP": {"name": "Associated Press", "results_url": "https://ap.test"}}
    text = faq_text(states, outlets)
    assert len(text) <= 2000
    for code in ("AL", "CA", "DC", "TX", "WY"):
        assert code in text
    assert "Associated Press" in text
    assert "n" * 300 not in text  # not a squeezed dump of every state

    one = faq_text(states, outlets, "TX")
    assert len(one) <= 2000 and "official_results_url" in one
