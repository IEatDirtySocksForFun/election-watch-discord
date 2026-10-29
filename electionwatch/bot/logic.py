"""Pure command and navigator logic, kept separate from Discord I/O for testing."""

from __future__ import annotations
import yaml
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
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
        races = gs.important or [str(default_race.key) for default_race in _default_races()]
        text = "Watched races: " + (", ".join(races) if races else "none")
        return text if len(text) <= 1900 else text[:1900].rsplit(", ", 1)[0] + f", … ({len(races)} total)"
    if action not in {"add", "remove"}:
        raise ValueError("action must be add, remove, or list")
    if not race:
        raise ValueError("race is required")
    key = _race_key(race)
    current = gs.important or [str(default_race.key) for default_race in _default_races()]
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


def change_odds(gs: GuildSettings, enabled: bool, threshold: float | None = None, window: int | None = None) -> str:
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


MAX_LIMIT_SECONDS = 7 * 24 * 3600


def change_limits(
    gs: GuildSettings,
    user_cooldown: int | None = None,
    user_hourly: int | None = None,
    refresh_cooldown: int | None = None,
) -> str:
    """/limits: -1 resets a value to the default; omitted values are unchanged."""
    changes = []
    for field, value, label in (
        ("user_cooldown_seconds", user_cooldown, "user cooldown"),
        ("user_commands_per_hour", user_hourly, "commands per user per hour"),
        ("refresh_cooldown_seconds", refresh_cooldown, "/refresh cooldown"),
    ):
        if value is None:
            continue
        if value < -1 or value > MAX_LIMIT_SECONDS:
            raise ValueError(f"{label} must be between 0 and {MAX_LIMIT_SECONDS} (-1 = default)")
        setattr(gs, field, None if value == -1 else int(value))
        changes.append(f"{label} {'default' if value == -1 else value}")
    if not changes:
        raise ValueError("give at least one of user_cooldown, user_hourly, refresh_cooldown")
    return "Limits set: " + ", ".join(changes)


def _span(seconds: float) -> str:
    seconds = round(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{round(seconds / 60)} min"
    return f"{seconds / 3600:.1f} h".replace(".0 h", " h")


def limits_text(gs: GuildSettings | None, rates, cooldowns) -> str:
    """/limits: this server's command limits, then refresh intervals and request budgets.
    Only sources slowed below their kind's floor (or set by the operator) are listed one by
    one; the full list would not fit one message with ~70 adapters."""
    from electionwatch.ratelimit import FLOORS

    def every(kind: str) -> float:
        return rates.kind_interval(kind) if rates is not None else FLOORS[kind]

    lines = []
    if cooldowns is not None:
        server = gs or GuildSettings(0)
        user = cooldowns.user_seconds(every("odds"), server.user_cooldown_seconds)
        hourly = server.user_commands_per_hour if server.user_commands_per_hour is not None else cooldowns.user_hourly
        set_user = server.user_cooldown_seconds is not None or cooldowns.user_cooldown is not None
        set_refresh = server.refresh_cooldown_seconds is not None or cooldowns.refresh_cooldown is not None
        refresh = cooldowns.refresh_seconds(min(every(kind) for kind in FLOORS), server.refresh_cooldown_seconds)
        lines += [
            "Command limits in this server:",
            "  same race/state again: "
            + (f"after {_span(user)}" if set_user else f"after 2 refreshes of its data (odds: {_span(user)})"),
            "  per user per hour: " + (f"{hourly:g}" if hourly else "no cap"),
            "  /refresh: once every " + _span(refresh) + ("" if set_refresh else " (longer for slower data)"),
        ]
    lines.append("Data refresh (in the background; commands read the database only):")
    rows = rates.status() if rates is not None else []
    for kind in FLOORS:
        # Race lists have a budget entry per source and state: show one line per source (its
        # slowest state), and count sources, not entries.
        mine: dict[str, dict] = {}
        for row in rows:
            name = row["source"].split(":")[0]
            if row["kind"] == kind and (name not in mine or row["interval"] > mine[name]["interval"]):
                mine[name] = row
        slowed = [row for row in mine.values() if row["overridden"] or row["interval"] > row["floor"] + 1]
        lines.append(
            f"  {kind}: every {_span(every(kind))}"
            + (f" ({len(mine) - len(slowed)} sources at this rate)" if len(mine) > len(slowed) else "")
        )
        for row in slowed:
            budget = ", ".join(f"{host} {used}/{allow:g} per h" for host, (used, allow) in row["hosts"].items())
            requests = row["requests_per_refresh"]
            lines.append(
                f"    {row['source'].split(':')[0]}: every {_span(row['interval'])}"
                + (" (set by operator)" if row["overridden"] else "")
                + (f"; {requests} request{'s' if requests != 1 else ''} per refresh" if requests else "")
                + (f"; {budget}" if budget else "")
            )
    if rates is not None:
        lines.append(
            f"Each host gets at most 1/{rates.safety:g} of its published limit per hour, "
            "shared evenly by the sources that use it."
        )
    return _clip_lines(lines, limit=1950, marker="… (more sources slowed by their budget)")


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


# ── /faq formatting ─────────────────────────────────────────────────────────
# no more yaml block, better
FAQ_LIMIT = 2000

TIMEZONE_NAMES = {
    "America/New_York": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Boise": "Mountain",
    "America/Phoenix": "Mountain (no DST)",
    "America/Los_Angeles": "Pacific",
    "America/Anchorage": "Alaska",
    "America/Adak": "Hawaii-Aleutian",
    "Pacific/Honolulu": "Hawaii",
}


def link(label: str, url: str) -> str:
    return f"[{label}](<{url}>)"

def clean(text) -> str:
    """Fold YAML's wrapped text onto one line."""
    return " ".join(str(text).split())

def timezone_names(zone: str) -> str:
    return TIMEZONE_NAMES.get(zone, zone.rsplit("/", 1)[-1].replace("_", " "))

def sources_line(urls) -> str | None:
    """One link per site, labelled by domain, so 7 NCSL/SOS URLs don't eat the message."""
    seen: dict[str, str] = {}
    for url in urls or ():
        host = urlparse(url).netloc.removeprefix("www.")
        seen.setdefault(host, url)
    if not seen:
        return None
    return "-# Sources: " + " · ".join(link(host, url) for host, url in seen.items())

def ranked_choice(value) -> str:
    text = clean(value)
    return {"false": "No", "true": "Yes"}.get(text.lower(), text)

def state_message(code: str, rec: dict) -> str:
    unverified = set(rec.get("unverified") or ())
    zones = rec.get("timezones") or []

    # Header + small-text links
    header = [f"# {STATES[code]}"]
    links = [link("Official results", rec["official_results_url"]), link("Election office", rec["sos_url"])]
    vendor = rec.get("results_vendor")
    if vendor and "results_vendor" not in unverified:
        links.append(f"Results via {vendor}")
    header.append("-# " + " · ".join(links))

    timezone = " / ".join(dict.fromkeys(timezone_names(zone) for zone in zones))
    header.append(f"-# Polls close {rec['polls_close_local']} {timezone}")
    if rec.get("polls_close_note"):
        header.append(f"-# {clean(rec['polls_close_note'])}")

    def field(label: str, key: str, transform=clean) -> str:
        flag = " *(unverified)*" if key in unverified else ""
        return f"**{label}:**{flag} {transform(rec[key])}"

    body = [
        "",
        field("Count order", "count_order"),
        field("Mail deadline", "mail_deadline"),
        field("Mail processing", "mail_processing"),
        field("When to expect a call", "typical_call_time"),
        field("Recount", "recount_threshold"),
        field("Ranked-choice voting", "ranked_choice", ranked_choice),
    ]
    notes = ["", f"> {clean(rec['notes'])}"] if rec.get("notes") else []
    sources = sources_line(rec.get("sources"))
    sources = [sources] if sources else []

    return fit(
        header + body + notes + sources,
        header + body + notes,       # drop sources first
        header + body,               # then the notes
    )


def fit(*candidates: list[str]) -> str:
    """First candidate that fits in one message; otherwise the last, clipped on a line."""
    for lines in candidates:
        text = "\n".join(lines)
        if len(text) <= FAQ_LIMIT:
            return text
    return _clip_lines(candidates[-1], FAQ_LIMIT - 30, marker="-# … (trimmed to fit)")


def outlet_lines(outlets, with_notes: bool) -> list[str]:
    lines = ["", "## Decision desks & outlets"]
    for code, rec in outlets.items():
        if not isinstance(rec, dict):
            lines.append(f"**{code}** · {rec}")
            continue
        parts = [f"**{code}**", link(rec.get("name", code), rec["results_url"])]
        if rec.get("exit_poll_url"):
            parts.append(link("voter survey", rec["exit_poll_url"]))
        lines.append(" · ".join(parts))
        if with_notes and rec.get("notes"):
            lines.append(f"-# {clean(rec['notes'])}")
    return lines


def faq_text(states, outlets, state: str | None = None) -> str:
    """Render installed FAQ data as a Discord message (markdown, one message)."""
    if state is None:
        codes = sorted(states) if isinstance(states, dict) else sorted(
            {getattr(e, "state", "") for e in states} - {""}
        )
        head = [
            "# State election FAQs",
            "-# Use `/faq state:<code>` for one state.",
            " · ".join(f"`{c}`" for c in codes) if codes else "No state FAQ data installed.",
        ]
        if not outlets:
            return fit(head)
        return fit(head + outlet_lines(outlets, True), head + outlet_lines(outlets, False))

    code = state.strip().upper()
    if code in TERRITORIES:
        return f"# {TERRITORIES[code]}\nNo FAQ data yet: territories are not covered."
    if code not in STATES:
        raise ValueError("unknown state")

    rec = states.get(code) if isinstance(states, dict) else next(
        (to_doc(e) for e in states if getattr(e, "state", None) == code), None
    )
    if not rec:
        return f"# {STATES[code]}\nNo FAQ data installed for {STATES[code]} yet.\n-# Outlet links: `/faq` without a state."
    return state_message(code, rec)
