"""Shared helpers for call-source adapters."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from electionwatch.models import Level, RaceKey


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
    low = party.strip().lower()
    if low in ("dem", "democrat", "democratic", "d"):
        return "DEM"
    if low in ("gop", "rep", "republican", "r"):
        return "REP"
    if low in ("ind", "independent"):
        return "IND"
    if low in ("lib", "libertarian"):
        return "LIB"
    if low in ("grn", "green"):
        return "GRN"
    return party.strip().upper()


def parse_iso_ts(raw: str | None) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)
