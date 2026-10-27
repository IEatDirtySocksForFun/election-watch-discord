"""Shared helpers for call-source adapters."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import STATES

_STATE_BY_NAME = {name.lower(): code for code, name in STATES.items()}


def state_from_name(name: str | None) -> str | None:
    """'Texas' -> 'TX'; None when the name is unknown."""
    if not name:
        return None
    return _STATE_BY_NAME.get(name.strip().lower())


def district_key(value) -> str | None:
    """Normalise a district number to a RaceKey district ('01', 'AL')."""
    text = str(value).strip().upper()
    if text in ("", "0", "00", "AL", "AT-LARGE", "AT LARGE"):
        return "AL"
    m = re.fullmatch(r"(\d{1,3})", text)
    if not m:
        return None
    return f"{int(m.group(1)):02d}"


def epoch_ms_to_dt(value) -> datetime | None:
    """Epoch milliseconds (CNN call timestamps) -> aware UTC datetime."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_http_date(value) -> datetime | None:
    """RFC 2822 / HTTP date (e.g. NBC's ``lastModified``) -> aware datetime."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def cnn_code_to_key(code: str) -> RaceKey | None:
    """Convert a CNN race code like '2026-SG-TX' or '2026-HG-CA-12' to a RaceKey."""
    m = re.fullmatch(r"\d{4}-([A-Z]+)-([A-Z]{2})(?:-(\d{2}))?", code)
    if not m:
        return None
    office, state, district = m.groups()
    if office == "SG":
        return RaceKey(state, Level.SENATE, "SEN")
    if office == "GO":
        return RaceKey(state, Level.GOVERNOR, "GOV")
    if office == "HG" and district:
        return RaceKey(state, Level.HOUSE, "HOUSE", district)
    return None


def key_to_cnn_code(key: RaceKey, year: int = 2026) -> str | None:
    """Convert a RaceKey to a CNN race code."""
    if key.level is Level.SENATE:
        return f"{year}-SG-{key.state}"
    if key.level is Level.GOVERNOR and key.office == "GOV":
        return f"{year}-GO-{key.state}"
    if key.level is Level.HOUSE and key.district is not None:
        return f"{year}-HG-{key.state}-{key.district}"
    return None


def normalize_party(party: str | None) -> str | None:
    if not party:
        return None
    low = party.strip().lower().rstrip(".")
    mapping = {
        "dem": "DEM", "democrat": "DEM", "democratic": "DEM", "democrats": "DEM", "d": "DEM",
        "gop": "REP", "rep": "REP", "republican": "REP", "republicans": "REP", "r": "REP",
        "ind": "IND", "independent": "IND", "i": "IND",
        "lib": "LIB", "libertarian": "LIB", "lb": "LIB", "l": "LIB",
        "grn": "GRN", "green": "GRN", "g": "GRN",
        "wri": "WRI", "write-in": "WRI", "write-ins": "WRI",
        "other": "OTH", "others": "OTH",
    }
    if low in mapping:
        return mapping[low]
    return party.strip().upper()


def parse_iso_ts(raw: str | None) -> datetime:
    if raw:
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # Models carry aware UTC; outlets that omit the offset publish UTC.
            return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def house_title(district: str | None) -> str:
    """'U.S. House District 7', or 'U.S. House At-Large' for single-seat states (district 'AL')."""
    if not district or not district.isdigit():
        return "U.S. House At-Large"
    return f"U.S. House District {int(district)}"
