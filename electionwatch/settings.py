"""Per-guild and per-user settings (persisted by the Store) and the post-timing rule."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import NamedTuple

from electionwatch.models import Level, RaceKey


class PostMode(str, Enum):
    DEFAULT = "default"  # admin never set timing: every 5 min, only if results changed
    INTERVAL = "interval"  # admin set a time: every N min, only if results changed
    IMMEDIATE = "immediate"  # admin chose "post every update": post as soon as results change


DEFAULT_INTERVAL_MIN = 5
#: /bettingalerts categories: stable id -> what it covers. Ids are stored in guild documents.
ODDS_CATEGORIES = {
    "senate": "US Senate",
    "house": "US House district",
    "statewide": "Governor and other statewide offices",
    "state-senate": "State Senate district",
    "state-house": "State House district",
    "mayor": "Mayor",
    "local": "Other local races",
}


@dataclass
class GuildSettings:
    guild_id: int
    channel_id: int | None = None
    admin_role_id: int | None = None  # None = Manage Server permission required
    important: list[str] = field(default_factory=list)  # race keys; empty = config/races.yaml
    post_mode: PostMode = PostMode.DEFAULT
    interval_minutes: int = DEFAULT_INTERVAL_MIN
    results_sources: list[str] = field(default_factory=list)  # empty = all available
    post_calls: bool = True
    # CIVICAPI (free) is opt-in: its decision desk had no real call on record to check yet.
    call_outlets: list[str] = field(default_factory=lambda: ["AP", "NBC", "CNN", "FOX", "DDHQ", "VOTEHUB"])
    post_turnout: bool = True
    post_exit_polls: bool = True
    odds_alerts: bool = False
    odds_markets: list[str] = field(default_factory=lambda: ["kalshi", "polymarket"])
    odds_swing_pts: float = 50.0  # alert when implied prob moves >= this many points
    odds_window_minutes: int = 60  #   ... within this look-back window
    paused: bool = False
    post_early_vote: bool = False  # daily civicAPI early-vote summary (/earlyvote_posts)
    # /limits overrides for this server; None = the operator's value, else computed
    # (electionwatch/bot/cooldown.py). Hourly cap 0 = no cap.
    user_cooldown_seconds: int | None = None  # per user, per command and race/state
    user_commands_per_hour: int | None = None  # per user, across /bettingmarkets /polls /earlyvote
    refresh_cooldown_seconds: int | None = None  # between two /refresh in this server
    # /bettingalerts overrides: category id (ODDS_CATEGORIES) or race key -> any of
    # {"enabled": bool, "threshold": float, "window": int}. A missing field falls through,
    # race > category > the server-wide odds_alerts / odds_swing_pts / odds_window_minutes.
    odds_alert_categories: dict[str, dict] = field(default_factory=dict)
    odds_alert_races: dict[str, dict] = field(default_factory=dict)
    # Button menus (electionwatch/bot/menu.py): /menu works for everyone unless an admin ran
    # /menu_private off; the ids are the channel menu from /menu_post, to replace or remove it.
    private_menu: bool = True
    menu_channel_id: int | None = None
    menu_message_id: int | None = None


class OddsAlert(NamedTuple):
    enabled: bool
    threshold: float  # points of implied probability
    window: int  # minutes


def odds_category(key: RaceKey) -> str:
    """The /bettingalerts category id of a race."""
    if key.level is Level.SENATE:
        return "senate"
    if key.level is Level.HOUSE:
        return "house"
    if key.level is Level.GOVERNOR:
        return "statewide"
    if key.level is Level.STATE:
        return "state-senate" if key.office == "SD" else "state-house"
    # Local races are free-form slugs; "nyc-mayor", "mayor-of-tulsa" and friends are mayors.
    return "mayor" if "mayor" in (key.district or "").lower() else "local"


def odds_alert_from(gs: GuildSettings, *overrides: dict | None) -> OddsAlert:
    """The server default with each override laid over it in turn (later wins), field by field."""
    rule = {"enabled": gs.odds_alerts, "threshold": gs.odds_swing_pts, "window": gs.odds_window_minutes}
    for override in overrides:
        rule.update({name: value for name, value in (override or {}).items() if name in rule and value is not None})
    return OddsAlert(bool(rule["enabled"]), float(rule["threshold"]), int(rule["window"]))


def odds_alert_for(gs: GuildSettings, key: RaceKey) -> OddsAlert:
    """Swing-alert setting for one race: race override > category override > server default."""
    return odds_alert_from(gs, gs.odds_alert_categories.get(odds_category(key)), gs.odds_alert_races.get(str(key)))


def odds_alerts_anywhere(gs: GuildSettings) -> bool:
    """True if any race could alert: the server default or some override is on."""
    overrides = [*gs.odds_alert_categories.values(), *gs.odds_alert_races.values()]
    return gs.odds_alerts or any(override.get("enabled") for override in overrides)


@dataclass
class UserSettings:
    user_id: int
    home_state: str | None = None  # navigator opens here
    favorites: list[str] = field(default_factory=list)  # race keys pinned in the navigator
    default_level: str = "senate"  # navigator's first tab


def post_due(gs: GuildSettings, last_post: datetime | None, now: datetime, updated: bool) -> bool:
    """README rule:
    if not admin.specified:            post when 5 min passed AND results updated
    elif admin.specified.time:         post when interval passed AND results updated
    else:                              post (every update, no wait)
    """
    if gs.paused or not updated:
        return False
    if gs.post_mode is PostMode.IMMEDIATE:
        return True
    minutes = DEFAULT_INTERVAL_MIN if gs.post_mode is PostMode.DEFAULT else max(1, gs.interval_minutes)
    return last_post is None or now - last_post >= timedelta(minutes=minutes)
