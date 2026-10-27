"""Per-guild and per-user settings (persisted by the Store) and the post-timing rule."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum


class PostMode(str, Enum):
    DEFAULT = "default"      # admin never set timing: every 5 min, only if results changed
    INTERVAL = "interval"    # admin set a time: every N min, only if results changed
    IMMEDIATE = "immediate"  # admin chose "post every update": post as soon as results change


DEFAULT_INTERVAL_MIN = 5


@dataclass
class GuildSettings:
    guild_id: int
    channel_id: int | None = None
    admin_role_id: int | None = None             # None = Manage Server permission required
    important: list[str] = field(default_factory=list)   # race keys; empty = config/races.yaml
    post_mode: PostMode = PostMode.DEFAULT
    interval_minutes: int = DEFAULT_INTERVAL_MIN
    results_sources: list[str] = field(default_factory=list)   # empty = all available
    post_calls: bool = True
    call_outlets: list[str] = field(default_factory=lambda: ["AP", "NBC", "CNN", "FOX", "DDHQ", "VOTEHUB"])
    post_turnout: bool = True
    post_exit_polls: bool = True
    odds_alerts: bool = False
    odds_markets: list[str] = field(default_factory=lambda: ["kalshi", "polymarket"])
    odds_swing_pts: float = 50.0                 # alert when implied prob moves >= this many points
    odds_window_minutes: int = 60                #   ... within this look-back window
    paused: bool = False


@dataclass
class UserSettings:
    user_id: int
    home_state: str | None = None                # navigator opens here
    favorites: list[str] = field(default_factory=list)   # race keys pinned in the navigator
    default_level: str = "senate"                # navigator's first tab


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
