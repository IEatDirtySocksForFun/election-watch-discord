"""The emoji the slash-command embeds use, kept in one place so every command speaks the same
visual language. Each emoji sits next to words ("🔵 Dem", "🏛️ Senate"), never instead of
the words: screen readers read the words, and the emoji are only there to help the eye."""

from __future__ import annotations

from electionwatch.models import normalize_party

# What a command is about: titles and menu buttons.
RACES = "🗳️"
FAVORITES = "⭐"
ODDS = "🎲"
POLLS = "📊"
EARLY_VOTE = "📬"
FAQ = "❓"
SETTINGS = "⚙️"
LIMITS = "⏱️"

# Race navigator tabs.
LEVELS = {
    "congressional": "🏠",
    "senate": "🏛️",
    "state": "🗺️",
    "local": "📍",
    "favorites": FAVORITES,
}

MARKETS = {"kalshi": "📈", "polymarket": "🔮"}

# Early vote: how a ballot was cast, and the headings of its breakdowns.
METHODS = {"returned": "📬", "inperson": "🏫", "requested": "📨", "grace": "⏳"}
BALLOTS = "🗳️"
PARTY = "🏷️"
AGE = "👥"
COUNTY = "🗺️"
COMPARE = "📆"
SEARCH = "🔎"

DATES = "📅"
SAMPLE = "👥"
LINK = "🔗"
INTERNAL = "⚠️"
OTHER = "⚪"


def party(raw: str | None) -> str:
    """🔵 / 🔴 / 🟡 / 🟢 … for a party name or code; ⚪ for "other", unknown or none."""
    if not raw or raw.strip().casefold() in ("other", "others"):
        return OTHER
    try:  # the full party table (electionwatch/data/parties.yaml) when installed
        from electionwatch.parties import party_emoji

        return party_emoji(raw)
    except ImportError:
        return {"REP": "🔴", "DEM": "🔵"}.get(normalize_party(raw) or "", OTHER)


def is_party(raw: str | None) -> bool:
    """True when ``raw`` names a known party (an odds outcome like "REP"), not a person."""
    if not raw:
        return False
    try:
        from electionwatch.parties import party_info

        return party_info(raw) is not None
    except ImportError:
        return normalize_party(raw) in ("REP", "DEM")


def move(points: float) -> str:
    """'▲ +2.3' / '▼ -1.0' / '+0.0': the arrow is extra, the signed number stays."""
    if round(points, 1) > 0:
        return f"▲ {points:+.1f}"
    if round(points, 1) < 0:
        return f"▼ {points:+.1f}"
    return f"{points:+.1f}"
