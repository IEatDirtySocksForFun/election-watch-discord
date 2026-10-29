"""Pure command and navigator logic, kept separate from Discord I/O for testing."""

from __future__ import annotations

import math
from urllib.parse import urlparse

import yaml

from electionwatch.bot.poster import (
    MAX_DESCRIPTION,
    MAX_EMBED_TOTAL,
    MAX_FIELD_NAME,
    MAX_FIELD_VALUE,
    MAX_FOOTER,
    MAX_TITLE,
    clip,
)
from electionwatch.models import ImportantRace, Level, to_doc
from electionwatch.racekeys import ALL_JURISDICTIONS, STATES, TERRITORIES, describe, parse, parse_typed
from electionwatch.settings import (
    ODDS_CATEGORIES,
    GuildSettings,
    OddsAlert,
    PostMode,
    UserSettings,
    odds_alert_for,
    odds_alert_from,
    odds_category,
)

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


#: Most race overrides one server can keep (/bettingalerts race:); categories cover the rest.
MAX_ODDS_RACE_OVERRIDES = 100


def change_odds(
    gs: GuildSettings,
    enabled: bool | str | None,
    threshold: float | None = None,
    window: int | None = None,
    race: str | None = None,
    category: str | None = None,
) -> str:
    """/bettingalerts: the server default, or an override for one race or one category.
    enabled None leaves it as it is; "default" removes the race or category override."""
    if isinstance(enabled, bool):
        enabled = "on" if enabled else "off"
    if enabled not in (None, "on", "off", "default"):
        raise ValueError("enabled must be on, off, or default")
    if threshold is not None and not 0 < threshold <= 100:
        raise ValueError("threshold must be greater than 0 and at most 100 points")
    if window is not None and not 1 <= window <= 1440:
        raise ValueError("window must be between 1 and 1440 minutes")
    if race and category:
        raise ValueError("pick a race or a category, not both")
    if category is not None and category not in ODDS_CATEGORIES:
        raise ValueError("category must be one of " + ", ".join(ODDS_CATEGORIES))
    if enabled is None and threshold is None and window is None:
        raise ValueError("give enabled, threshold or window to change")
    if not race and category is None:
        if enabled == "default":
            raise ValueError("enabled:default removes a race or category override: pick a race or a category")
        if enabled is not None:
            gs.odds_alerts = enabled == "on"
        if threshold is not None:
            gs.odds_swing_pts = threshold
        if window is not None:
            gs.odds_window_minutes = window
        count = len(gs.odds_alert_categories) + len(gs.odds_alert_races)
        return (
            f"Odds alerts {'on' if gs.odds_alerts else 'off'}; threshold {gs.odds_swing_pts:g} points "
            f"within {gs.odds_window_minutes} minutes"
        ) + (f" ({count} race/category override{'s still apply' if count != 1 else ' still applies'})" if count else "")
    if enabled == "default" and (threshold is not None or window is not None):
        raise ValueError("enabled:default removes the whole override: leave out threshold and window")

    if race:
        target = _race_key(race)
        overrides, label = gs.odds_alert_races, target
    else:
        target = category
        overrides, label = gs.odds_alert_categories, f"{ODDS_CATEGORIES[category]} ({category})"
    if enabled == "default":
        if overrides.pop(target, None) is None:
            return f"{label} has no odds alert override"
        return f"Removed the odds alert override for {label}"
    if race and target not in overrides and len(overrides) >= MAX_ODDS_RACE_OVERRIDES:
        raise ValueError(
            f"This server already has {MAX_ODDS_RACE_OVERRIDES} race overrides. Remove one with "
            "enabled:default, or use a category instead."
        )
    override = dict(overrides.get(target) or {})
    if enabled is not None:
        override["enabled"] = enabled == "on"
    if threshold is not None:
        override["threshold"] = float(threshold)
    if window is not None:
        override["window"] = int(window)
    overrides[target] = override
    message = f"Odds alerts for {label}: {_override_text(override)}."
    if not race:
        alert = _alert_text(odds_alert_from(gs, override))
        return message + f" Races in it get: {alert}, unless a race override says otherwise."
    message += f" In effect: {_alert_text(odds_alert_for(gs, parse(target)))}."
    watched = gs.important or [str(default_race.key) for default_race in _default_races()]
    if target not in watched:
        message += f" {target} isn't watched, so it won't alert until it's added with /watch."
    return message


def _alert_text(alert: OddsAlert) -> str:
    return f"{'on' if alert.enabled else 'off'}, {alert.threshold:g} points within {alert.window} minutes"


def _override_text(override: dict) -> str:
    parts = []
    if override.get("enabled") is not None:
        parts.append("on" if override["enabled"] else "off")
    if override.get("threshold") is not None:
        parts.append(f"threshold {override['threshold']:g} points")
    if override.get("window") is not None:
        parts.append(f"window {override['window']} minutes")
    return ", ".join(parts) or "nothing set"


def odds_alerts_text(gs: GuildSettings, race: str | None = None, category: str | None = None) -> str:
    """/bettingalerts without changes: the server default and every override, one per line."""
    if race and category:
        raise ValueError("pick a race or a category, not both")
    if category is not None and category not in ODDS_CATEGORIES:
        raise ValueError("category must be one of " + ", ".join(ODDS_CATEGORIES))
    lines = []
    if race:
        key = parse(_race_key(race))
        lines.append(f"{key} ({odds_category(key)}) gets: {_alert_text(odds_alert_for(gs, key))}")
    elif category is not None:
        alert = odds_alert_from(gs, gs.odds_alert_categories.get(category))
        lines.append(f"{ODDS_CATEGORIES[category]} ({category}) gets: {_alert_text(alert)}")
    lines.append(f"Server default: {_alert_text(odds_alert_from(gs))}")
    if gs.odds_alert_categories:
        lines.append("Categories:")
        lines += [
            f"  {ODDS_CATEGORIES.get(name, name)} ({name}): {_override_text(override)}"
            for name, override in gs.odds_alert_categories.items()
        ]
    if gs.odds_alert_races:
        lines.append(f"Races ({len(gs.odds_alert_races)} of {MAX_ODDS_RACE_OVERRIDES}):")
        lines += [f"  {key}: {_override_text(override)}" for key, override in gs.odds_alert_races.items()]
    if not gs.odds_alert_categories and not gs.odds_alert_races:
        lines.append("No race or category overrides.")
    lines.append("A race setting beats its category's, which beats the server default. Only watched races alert.")
    return _clip_lines(lines, marker="… (more overrides than fit here; /settings has them too)")


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


def limits_text(gs: GuildSettings | None, rates, cooldowns, live_max_age: float | None = None) -> str:
    """/limits: this server's command limits, then refresh intervals and request budgets.
    Only sources slowed below their kind's floor (or set by the operator) are listed one by
    one; the full list would not fit one message with ~70 adapters. `live_max_age` is set when
    commands fetch from the sources themselves (EW_COMMAND_DATA=live)."""
    from electionwatch.ratelimit import FLOORS

    def every(kind: str) -> float:
        return rates.kind_interval(kind) if rates is not None else FLOORS[kind]

    lines = []
    if cooldowns is not None:
        server = gs or GuildSettings(0)
        odds_every = every("odds") if live_max_age is None else min(every("odds"), live_max_age)
        user = cooldowns.user_seconds(odds_every, server.user_cooldown_seconds)
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
    if live_max_age is None:
        lines.append("Data refresh (in the background; commands read the database only):")
    else:
        lines.append(
            f"Data refresh (in the background; commands also fetch from the source when an answer "
            f"is over {_span(live_max_age)} old, within the same budgets):"
        )
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
    # Odds alert overrides are written one per line ({enabled: true, threshold: 5.0}), not one
    # line per field, so a hundred of them still leave room for the watch list. No line
    # wrapping: clipping between the halves of a wrapped override would break the YAML.
    doc = to_doc(gs)
    overrides = {name: doc.pop(name) for name in ("odds_alert_categories", "odds_alert_races")}
    important = {"important": doc.pop("important")}
    lines = (
        _yaml_lines(doc)
        + yaml.safe_dump(overrides, sort_keys=False, default_flow_style=None, width=math.inf).strip().splitlines()
        + _yaml_lines(important)
    )
    total = f"{len(gs.important)} watched races in total"
    if gs.odds_alert_races:
        total = f"{len(gs.odds_alert_races)} race overrides and {total}"
    return _clip_lines(lines, marker=f"# … (truncated; {total})")


def _race_key(race: str) -> str:
    if len(race.strip()) > MAX_RACE_TEXT:
        raise ValueError(f"race is too long ({MAX_RACE_TEXT} characters at most)")
    return str(parse_typed(race))  # "pa 08", "pensylvania 8" and "PA-08" are all PA-08


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


def _yaml_lines(payload) -> list[str]:
    return yaml.safe_dump(payload, sort_keys=False).strip().splitlines()


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
# /faq answers with an embed: a dict in discord.Embed.from_dict() shape, so this stays pure.
FAQ_COLOR = 0x5865F2  # Discord blurple, like the other command embeds
MAX_FIELDS = 25  # Discord's limit per embed

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

STATE_FIELDS = (
    ("Count order", "count_order"),
    ("Mail deadline", "mail_deadline"),
    ("Mail processing", "mail_processing"),
    ("When to expect a call", "typical_call_time"),
    ("Recount", "recount_threshold"),
    ("RCV", "ranked_choice"),
)
FIELD_EMOJI = {
    "count_order": "📋",
    "mail_deadline": "📬",
    "mail_processing": "🗃️",
    "typical_call_time": "📣",
    "recount_threshold": "🔁",
    "ranked_choice": "🔢",
}
HEADER_KEYS = {
    "official_results_url",
    "sos_url",
    "results_vendor",
    "polls_close_local",
    "polls_close_note",
    "timezones",
    "notes",
    "sources",
    "unverified",
}
# Decision desks call races themselves; VoteHub passes on others' calls; the rest are newsrooms.
DESKS = {"AP", "DDHQ", "CIVICAPI"}
AGGREGATORS = {"VOTEHUB"}
PRINT = {"NYT", "REUTERS"}


def link(label: str, url: str) -> str:
    return f"[{label}](<{url}>)"


def clean(text) -> str:
    """Fold YAML's wrapped text onto one line."""
    return " ".join(str(text).split())


def timezone_names(zone: str) -> str:
    return TIMEZONE_NAMES.get(zone, zone.rsplit("/", 1)[-1].replace("_", " "))


def sources_line(urls) -> str | None:
    """One link per site, labelled by domain, so 7 NCSL/SOS URLs don't eat the embed."""
    seen: dict[str, str] = {}
    for url in urls or ():
        host = urlparse(url).netloc.removeprefix("www.")
        seen.setdefault(host, url)
    if not seen:
        return None
    return " · ".join(link(host, url) for host, url in seen.items())


def ranked_choice(value) -> str:
    text = clean(value)
    return {"false": "❌ No", "true": "✅ Yes"}.get(text.lower(), text)


def _field(name: str, value: str) -> dict:
    return {"name": clip(name, MAX_FIELD_NAME), "value": clip(value, MAX_FIELD_VALUE), "inline": False}


def _embed_size(embed: dict) -> int:
    return (
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + sum(len(field["name"]) + len(field["value"]) for field in embed.get("fields", ()))
        + len(embed.get("footer", {}).get("text", ""))
    )


def _faq_embed(title: str, description: str = "", fields=(), footer: str | None = None) -> dict:
    embed = {"title": clip(title, MAX_TITLE), "color": FAQ_COLOR}
    if description:
        embed["description"] = clip(description, MAX_DESCRIPTION)
    embed["fields"] = list(fields)[:MAX_FIELDS]
    if footer:
        embed["footer"] = {"text": clip(footer, MAX_FOOTER)}
    return embed


def fit(*candidates: dict) -> dict:
    """First embed within Discord's 6000-character total; otherwise the last, fields dropped from the end."""
    for embed in candidates:
        if _embed_size(embed) <= MAX_EMBED_TOTAL:
            return embed
    embed = candidates[-1]
    while embed["fields"] and _embed_size(embed) > MAX_EMBED_TOTAL:
        embed["fields"].pop()
    return embed


def state_embed(code: str, record: dict) -> dict:
    unverified = set(record.get("unverified") or ())
    zones = record.get("timezones") or []

    header = []
    links = [
        link(label, record[key])
        for label, key in (("Official results", "official_results_url"), ("Election office", "sos_url"))
        if record.get(key)
    ]
    vendor = record.get("results_vendor")
    if vendor and "results_vendor" not in unverified:
        links.append(f"Results via {vendor}")
    if links:
        header.append("🔗 " + " · ".join(links))
    if record.get("polls_close_local"):
        timezone = " / ".join(dict.fromkeys(timezone_names(zone) for zone in zones))
        header.append(f"🕖 Polls close {record['polls_close_local']} {timezone}".rstrip())
    if record.get("polls_close_note"):
        header.append(f"*{clean(record['polls_close_note'])}*")

    fields = []
    for label, key in STATE_FIELDS:
        if record.get(key) in (None, ""):
            continue
        value = ranked_choice(record[key]) if key == "ranked_choice" else clean(record[key])
        flag = "*(unverified)* " if key in unverified else ""
        fields.append(_field(f"{FIELD_EMOJI[key]} {label}", flag + value))
    shown_keys = {key for _, key in STATE_FIELDS} | HEADER_KEYS
    for key, value in record.items():
        if key not in shown_keys and value not in (None, ""):
            fields.append(_field(f"ℹ️ {key.replace('_', ' ').capitalize()}", clean(value)))

    notes = [_field("💬 Good to know", clean(record["notes"]))] if record.get("notes") else []
    sources = sources_line(record.get("sources"))
    sources = [_field("📚 Sources", sources)] if sources else []

    title = f"🗳️ {STATES[code]}"
    description = "\n".join(header)
    footer = "Decision desks and outlets: /faq without a state"
    return fit(
        _faq_embed(title, description, fields + notes + sources, footer),
        _faq_embed(title, description, fields + notes, footer),  # drop sources first
        _faq_embed(title, description, fields, footer),  # then the notes
    )


def _outlet_emoji(code: str) -> str:
    if code in DESKS:
        return "🏁"
    if code in AGGREGATORS:
        return "📊"
    return "📰" if code in PRINT else "📺"


def outlet_fields(outlets, with_notes: bool) -> list[dict]:
    fields = []
    for code, record in outlets.items():
        if not isinstance(record, dict):
            fields.append(_field(f"{_outlet_emoji(code)} {code}", clean(record)))
            continue
        parts = [link("Results", record["results_url"])]
        if record.get("exit_poll_url"):
            parts.append(link("Voter survey", record["exit_poll_url"]))
        value = " · ".join(parts)
        if with_notes and record.get("notes"):
            value += f"\n*{clean(record['notes'])}*"
        fields.append(_field(f"{_outlet_emoji(code)} {record.get('name', code)} ({code})", value))
    return fields


def faq_embed(states, outlets, state: str | None = None) -> dict:
    """Installed FAQ data as one Discord embed (a discord.Embed.from_dict() dict)."""
    if state is None:
        codes = (
            sorted(states)
            if isinstance(states, dict)
            else sorted({getattr(entry, "state", "") for entry in states} - {""})
        )
        description = "Use `/faq state:` with a code or name (`NC`, `North Carolina`, `N.C.`) for one state."
        description += "\n\n" + (
            " · ".join(f"`{state_code}`" for state_code in codes) if codes else "No state FAQ data installed."
        )
        if outlets:
            description += "\n\n**🏁 Decision desks & outlets**"
        title = "🗳️ State FAQs"
        if not outlets:
            return _faq_embed(title, description)
        return fit(
            _faq_embed(title, description, outlet_fields(outlets, True)),
            _faq_embed(title, description, outlet_fields(outlets, False)),
        )

    code = state.strip().upper()
    if code in TERRITORIES:
        name = TERRITORIES[code]
        return _faq_embed(f"🗳️ {name}", f"No FAQ data for {name}: territories are not covered yet.")
    if code not in STATES:
        raise ValueError("unknown state")

    record = (
        states.get(code)
        if isinstance(states, dict)
        else next((to_doc(entry) for entry in states if getattr(entry, "state", None) == code), None)
    )
    if not record:
        return _faq_embed(
            f"🗳️ {STATES[code]}", f"No FAQ data for {STATES[code]} yet.", footer="Outlet links: /faq without a state"
        )
    return state_embed(code, record)
