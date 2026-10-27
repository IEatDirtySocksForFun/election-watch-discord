"""Pure command and navigator logic, kept separate from Discord I/O for testing."""
from __future__ import annotations

import yaml

from electionwatch.models import ImportantRace, Level, to_doc
from electionwatch.racekeys import ALL_JURISDICTIONS, STATES, TERRITORIES, describe, parse
from electionwatch.settings import GuildSettings, PostMode, UserSettings

OUTLETS = {"AP", "CBS", "FOX", "NBC", "CNN", "ABC", "DDHQ", "VOTEHUB", "NYT", "CIVICAPI"}
#: Longest race text accepted by /watch and /favorite: Discord's limit for an autocomplete
#: choice value. A free-form LOCAL slug could otherwise be thousands of characters long,
#: overflowing every later reply (/watch list, /settings) that shows it.
MAX_RACE_TEXT = 100
LEVELS = {
    "congressional": {Level.HOUSE},
    "senate": {Level.SENATE},
    "state": {Level.GOVERNOR, Level.STATE},
    "local": {Level.LOCAL},
}
#: Navigator tabs: the race levels plus the user's favorites (races from any state).
NAV_TABS = [*LEVELS, "favorites"]


def is_admin(member, guild_settings: GuildSettings) -> bool:
    """Manage Server always grants access; an optional configured role also does."""
    permissions = getattr(member, "guild_permissions", None)
    if permissions is not None and permissions.manage_guild:
        return True
    role_id = guild_settings.admin_role_id
    # A discord.User (DMs, user-installed app contexts) has no roles at all.
    roles = getattr(member, "roles", None) or ()
    return role_id is not None and any(getattr(role, "id", None) == role_id for role in roles)


def change_watch(gs: GuildSettings, action: str, race: str | None = None) -> str:
    action = action.lower()
    if action == "list":
        races = gs.important or [str(item.key) for item in _default_races()]
        text = "Watched races: " + (", ".join(races) if races else "none")
        return text if len(text) <= 1900 else text[:1900].rsplit(", ", 1)[0] + f", … ({len(races)} total)"
    if action not in {"add", "remove"}:
        raise ValueError("action must be add, remove, or list")
    if not race:
        raise ValueError("race is required")
    key = _race_key(race)
    current = gs.important or [str(item.key) for item in _default_races()]
    if action == "remove" and key not in current:
        # Nothing to do: don't claim a removal or materialize the default list.
        return f"{key} is not watched"
    gs.important = list(current)
    if action == "add":
        if key not in gs.important:
            gs.important.append(key)
        return f"Watching {key}"
    gs.important.remove(key)
    return f"Removed {key}" + ("; using default races" if not gs.important else "")


def _default_races() -> list[ImportantRace]:
    from electionwatch.config import load_races

    return load_races()


def change_setup(gs: GuildSettings, channel_id: int, admin_role_id: int | None = None) -> str:
    """Point the guild at a post channel; optionally restrict admin commands to one role."""
    if not channel_id:
        raise ValueError("channel is required")
    gs.channel_id = int(channel_id)
    if admin_role_id is not None:
        gs.admin_role_id = int(admin_role_id)
    message = f"Election posts will go to <#{gs.channel_id}>."
    if gs.admin_role_id is not None:
        message += f" Admin commands: Manage Server or <@&{gs.admin_role_id}>."
    return message


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
    if enabled:
        gs.call_outlets = selected
        gs.post_calls = True
        return f"Race calls on; outlets: {', '.join(selected)}"
    # "off" stops the named outlets only; race calls go off once no outlet is left.
    gs.call_outlets = [outlet for outlet in gs.call_outlets if outlet not in selected]
    gs.post_calls = bool(gs.call_outlets) and gs.post_calls
    if gs.post_calls:
        return f"Race calls off for {', '.join(selected)}; still posting: {', '.join(gs.call_outlets)}"
    return f"Race calls off (stopped {', '.join(selected)}; no outlets left)"


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
    # Clipped on a line boundary: the dump is sent inside a code block in one 2000-char message.
    # The watch list goes last so a long one can't push the other settings out, and the
    # truncation marker is a YAML comment so the clipped dump still parses.
    doc = to_doc(gs)
    doc["important"] = doc.pop("important")
    lines = yaml.safe_dump(doc, sort_keys=False).strip().splitlines()
    return _clip_lines(lines, marker=f"# … (truncated; {len(gs.important)} watched races in total)")


def _race_key(race: str) -> str:
    if len(race.strip()) > MAX_RACE_TEXT:
        raise ValueError(f"race is too long ({MAX_RACE_TEXT} characters at most)")
    return str(parse(race))


def change_favorite(us: UserSettings, action: str, race: str) -> str:
    key = _race_key(race)
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
    if state not in ALL_JURISDICTIONS:
        raise ValueError("unknown state")
    if level not in NAV_TABS:
        raise ValueError("unknown navigator tab")
    us.home_state = state
    us.default_level = level


def race_label(key, label: str | None = None) -> str:
    return label or describe(key)


def _clip_lines(lines: list[str], limit: int = 1900, marker: str = "# … (truncated)") -> str:
    """Join lines, stopping on a line boundary so 2000-character messages stay valid YAML."""
    kept: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit:
            kept.append(marker)
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


#: /faq is sent as ```yaml\n<text>\n``` in one 2000-character message.
FAQ_LIMIT = 2000 - len("```yaml\n\n```") - 8


def _yaml_lines(data) -> list[str]:
    return yaml.safe_dump(data, sort_keys=False).strip().splitlines()


def _first_fit(*candidates: list[str]) -> str:
    """The first candidate that fits whole; otherwise the last one clipped on a line boundary."""
    for lines in candidates:
        if len("\n".join(lines)) <= FAQ_LIMIT:
            return "\n".join(lines)
    return _clip_lines(candidates[-1], FAQ_LIMIT)


def _outlet_names(outlets) -> dict:
    return {code: rec.get("name", code) if isinstance(rec, dict) else rec for code, rec in outlets.items()}


def faq_text(states, outlets, state: str | None = None) -> str:
    """Serialize installed FAQ data deterministically, without a model or paraphrasing.

    The result is valid YAML (it is shown in a ```yaml block): outlet details are shortened
    to fit one message rather than being cut off in the middle of a record.
    """
    data = states
    if state is None:
        # All 51 states never fit in one message: point at what is installed instead of
        # dumping a truncated YAML blob.
        codes = (
            sorted(states) if isinstance(states, dict)
            else sorted({getattr(entry, "state", "") for entry in states} - {""})
        )
        head = [
            ("State FAQs: " + ", ".join(codes)) if codes else "# No state FAQ data installed.",
            "# Use /faq state:<code> for one state.",
        ]
        if not outlets:
            return _first_fit(head)
        brief = {
            code: {k: v for k, v in rec.items() if k != "notes"} if isinstance(rec, dict) else rec
            for code, rec in outlets.items()
        }
        return _first_fit(head + _yaml_lines({"Outlets": outlets}), head + _yaml_lines({"Outlets": brief}))
    code = state.strip().upper()
    if code in TERRITORIES:
        return f"# No FAQ data for {TERRITORIES[code]}: territories are not covered yet."
    if code not in STATES:
        raise ValueError("unknown state")
    if isinstance(states, dict):
        data = states.get(code) or states.get(STATES[code])
        if data is None:
            return _clip_lines(
                [f"No FAQ data for {STATES[code]}", ""]
                + yaml.safe_dump(outlets, sort_keys=False).strip().splitlines()
            )
        body = _yaml_lines({code: data})
    else:
        body = _yaml_lines({"states": [entry for entry in states if getattr(entry, "state", None) == code]})
    # Every state record plus all ten outlet records never fits: fall back to outlet names,
    # then to a pointer at /faq without a state, which lists the outlet details.
    return _first_fit(
        body + _yaml_lines({"outlets": outlets}),
        body + _yaml_lines({"outlets": _outlet_names(outlets)}),
        body + ["# Outlet details: /faq without a state."],
    )
