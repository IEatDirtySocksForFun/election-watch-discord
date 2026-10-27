"""Pure command and navigator logic, kept separate from Discord I/O for testing."""
from __future__ import annotations

import yaml

from electionwatch.models import ImportantRace, Level, to_doc
from electionwatch.racekeys import STATES, describe, parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings

OUTLETS = {"AP", "CBS", "FOX", "NBC", "CNN", "ABC", "DDHQ", "VOTEHUB", "NYT"}
LEVELS = {
    "congressional": {Level.HOUSE},
    "senate": {Level.SENATE},
    "state": {Level.GOVERNOR, Level.STATE},
    "local": {Level.LOCAL},
}


def is_admin(member, guild_settings: GuildSettings) -> bool:
    """Manage Server always grants access; an optional configured role also does."""
    permissions = getattr(member, "guild_permissions", None)
    if permissions is not None and permissions.manage_guild:
        return True
    role_id = guild_settings.admin_role_id
    return role_id is not None and any(getattr(role, "id", None) == role_id for role in member.roles)


def change_watch(gs: GuildSettings, action: str, race: str | None = None) -> str:
    action = action.lower()
    if action == "list":
        races = gs.important or [str(item.key) for item in _default_races()]
        return "Watched races: " + (", ".join(races) if races else "none")
    if action not in {"add", "remove"}:
        raise ValueError("action must be add, remove, or list")
    if not race:
        raise ValueError("race is required")
    key = str(parse(race))
    if not gs.important:
        gs.important = [str(item.key) for item in _default_races()]
    if action == "add":
        if key not in gs.important:
            gs.important.append(key)
        return f"Watching {key}"
    if key in gs.important:
        gs.important.remove(key)
    return f"Removed {key}" + ("; using default races" if not gs.important else "")


def _default_races() -> list[ImportantRace]:
    from electionwatch.config import load_races

    return load_races()


def change_posting(gs: GuildSettings, mode: str, minutes: int | None = None) -> str:
    try:
        selected = PostMode(mode.lower())
    except ValueError as exc:
        raise ValueError("mode must be default, interval, or immediate") from exc
    if minutes is not None and not 1 <= minutes <= 1440:
        raise ValueError("minutes must be between 1 and 1440")
    gs.post_mode = selected
    if selected is PostMode.INTERVAL:
        gs.interval_minutes = minutes or max(1, gs.interval_minutes)
    return f"Posting mode: {selected.value}" + (
        f" every {gs.interval_minutes} minutes" if selected is PostMode.INTERVAL else ""
    )


def change_calls(gs: GuildSettings, outlets: str, enabled: bool) -> str:
    selected = sorted({part.upper() for part in outlets.replace(",", " ").split() if part})
    unknown = set(selected) - OUTLETS
    if not selected or unknown:
        raise ValueError("unknown outlets: " + (", ".join(sorted(unknown)) or "none"))
    gs.call_outlets = selected
    gs.post_calls = enabled
    return f"Race calls {'on' if enabled else 'off'}; outlets: {', '.join(selected)}"


def change_odds(
    gs: GuildSettings, enabled: bool, threshold: float | None = None, window: int | None = None
) -> str:
    if threshold is not None and not 0 < threshold <= 100:
        raise ValueError("threshold must be greater than 0 and at most 100 points")
    if window is not None and not 1 <= window <= 1440:
        raise ValueError("window must be between 1 and 1440 minutes")
    gs.odds_alerts = enabled
    if threshold is not None:
        gs.odds_swing_pts = threshold
    if window is not None:
        gs.odds_window_minutes = window
    return (
        f"Odds alerts {'on' if enabled else 'off'}; threshold {gs.odds_swing_pts:g} points "
        f"within {gs.odds_window_minutes} minutes"
    )


def settings_dump(gs: GuildSettings) -> str:
    # to_doc() turns the PostMode enum into its plain value: yaml cannot represent the enum.
    return yaml.safe_dump(to_doc(gs), sort_keys=False).strip()


def change_favorite(us: UserSettings, action: str, race: str) -> str:
    key = str(parse(race))
    if action == "add":
        if key not in us.favorites:
            us.favorites.append(key)
        return f"Favorited {key}"
    if action == "remove":
        if key in us.favorites:
            us.favorites.remove(key)
        return f"Removed favorite {key}"
    raise ValueError("action must be add or remove")


def filter_races(races: list[tuple], state: str, level: str) -> list[tuple]:
    allowed = LEVELS[level]
    return [(key, label) for key, label in races if key.state == state and key.level in allowed]


def remember_navigation(us: UserSettings, state: str, level: str) -> None:
    if state not in STATES:
        raise ValueError("unknown state")
    if level not in LEVELS:
        raise ValueError("unknown navigator tab")
    us.home_state = state
    us.default_level = level


def race_label(key, label: str | None = None) -> str:
    return label or describe(key)


def _clip_lines(lines: list[str], limit: int = 1900) -> str:
    """Join lines, stopping on a line boundary so 2000-character messages stay valid YAML."""
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            kept.append("… (truncated)")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


def faq_text(states, outlets, state: str | None = None) -> str:
    """Serialize installed FAQ data deterministically, without a model or paraphrasing."""
    data = states
    if state is None:
        # All 51 states never fit in one message: point at what is installed instead of
        # dumping a truncated YAML blob.
        codes = (
            sorted(states) if isinstance(states, dict)
            else sorted({getattr(entry, "state", "") for entry in states} - {""})
        )
        lines = [
            ("State FAQs: " + ", ".join(codes)) if codes else "No state FAQ data installed.",
            "",
            "Use /faq state:<code> for one state.",
        ]
        if outlets:
            lines += ["", "Outlets:"] + yaml.safe_dump(outlets, sort_keys=False).strip().splitlines()
        return _clip_lines(lines)
    code = state.upper()
    if code not in STATES:
        raise ValueError("unknown state")
    if isinstance(states, dict):
        data = states.get(code) or states.get(STATES[code])
        if data is None:
            return _clip_lines(
                [f"No FAQ data for {STATES[code]}", ""]
                + yaml.safe_dump(outlets, sort_keys=False).strip().splitlines()
            )
    else:
        data = [entry for entry in states if getattr(entry, "state", None) == code]
    text = yaml.safe_dump({"states": data, "outlets": outlets}, sort_keys=False).strip()
    return _clip_lines(text.splitlines())
