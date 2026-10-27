"""Shared parsing helpers for the poll adapters.

Everything here is a pure function so it can be tested against trimmed real
responses in ``tests/fixtures/<adapter>/`` without touching the network.
"""
from __future__ import annotations

import re
from datetime import date

from electionwatch.models import normalize_party
from electionwatch.racekeys import STATES

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9

STATE_BY_NAME = {name.casefold(): code for code, name in STATES.items()}

# Footnote / citation markers inside scraped table cells: "[39]", "[a]", "[b]".
_REF = re.compile(r"\[[^\[\]]{0,40}\]")

# "Harper Polling (R)", "Ken Paxton (R)", "Change Research (D)".
_PARTY_TAG = re.compile(
    r"\((R|D|I|L|G|REP|DEM|LIB|GRN|IND|Republican|Democratic|Independent|Libertarian|Green)\)\s*$",
    re.I,
)

_DASHES = {"\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-"}


def norm_space(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def strip_refs(text: str) -> str:
    return _REF.sub("", text or "")


def node_text(node) -> str:
    """Readable text of a parsed HTML node.

    selectolax joins child text without a separator by default, which turns
    Wikipedia's ``Don<br>Brown`` header into ``DonBrown``; join with spaces.
    """
    if node is None:
        return ""
    return norm_space(node.text(separator=" "))


def state_code(name: str | None) -> str | None:
    """'North Carolina' / 'north carolina' -> 'NC'."""
    key = norm_space(name).casefold()
    return STATE_BY_NAME.get(key)


def split_party_marker(text: str | None) -> tuple[str, str | None]:
    """Split a trailing source/party marker: 'Harper Polling (R)' -> ('Harper Polling', 'REP').

    Only recognised party words count, so 'Don Brown (author, born 1960)' is left alone.
    """
    cleaned = norm_space(strip_refs(text))
    m = _PARTY_TAG.search(cleaned)
    if not m:
        return cleaned, None
    return cleaned[: m.start()].strip(" ,"), normalize_party(m.group(1))


def parse_percent(text: str | None) -> float | None:
    """'38%', "'''38%'''", '45' -> 38.0 / 45.0; '–', '<1%' -> None (no honest number)."""
    t = norm_space(strip_refs(text)).replace("'''", "").replace("**", "")
    t = t.strip("* \u00a0")
    if not t or t in {"-", "N/A", "n/a"}:
        return None
    if t.startswith("<"):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    value = float(m.group(1))
    if value < 0 or value > 100:
        return None
    return value


_POPULATION = {"lv": "LV", "rv": "RV", "a": "A", "v": None}


def parse_sample(text: str | None) -> tuple[int | None, str | None]:
    """'600 (LV)' / '1,449 LV · Margin of error ±6.6%' -> (1449, 'LV').

    '600 (V)' (Wikipedia's "voters, unclear") keeps the sample and drops the
    population, because LV/RV/A is the only vocabulary the model has.
    """
    t = norm_space(strip_refs(text))
    population = None
    m = re.search(r"\((LV|RV|A|V)\)|\b(LV|RV)\b", t or "", re.I)
    if m:
        population = _POPULATION[(m.group(1) or m.group(2)).lower()]
    sample = None
    m = re.search(r"(\d[\d,]*)", t)
    if m:
        n = int(m.group(1).replace(",", ""))
        sample = n or None
    return sample, population


def _month(token: str) -> int | None:
    return _MONTHS.get(token.casefold().rstrip("."))


def _mk(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


_RANGE_SAME_YEAR = re.compile(
    r"^(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2})"
    r"(?:\s*-\s*(?:(?P<m2>[A-Za-z]{3,9})\.?\s+)?(?P<d2>\d{1,2}))?"
    r",?\s+(?P<y>\d{4})$"
)
_RANGE_TWO_YEARS = re.compile(
    r"^(?P<m1>[A-Za-z]{3,9})\.?\s+(?P<d1>\d{1,2}),?\s+(?P<y1>\d{4})\s*-\s*"
    r"(?:(?P<m2>[A-Za-z]{3,9})\.?\s+)?(?P<d2>\d{1,2}),?\s+(?P<y2>\d{4})$"
)


def parse_date_range(text: str | None) -> tuple[date | None, date | None]:
    """Fieldwork window -> (start, end) as :class:`datetime.date`.

    Handles the shapes the sources actually use:
    'February 22–23, 2026', 'April 22 – May 6, 2026', 'October 6, 2026',
    'December 28, 2025 – January 3, 2026', '10/27/2026' and '2026-10-27'.
    """
    t = norm_space(strip_refs(text))
    if not t:
        return None, None
    t = re.sub(r"^(?:through|thru|to)\s+", "", t, flags=re.I)
    for dash, plain in _DASHES.items():
        t = t.replace(dash, plain)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d, d

    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        d = _mk(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return d, d

    m = _RANGE_TWO_YEARS.match(t)
    if m:
        start = _mk(int(m.group("y1")), _month(m.group("m1")) or 0, int(m.group("d1")))
        end = _mk(int(m.group("y2")), _month(m.group("m2") or m.group("m1")) or 0,
                  int(m.group("d2")))
        return start, end

    m = _RANGE_SAME_YEAR.match(t)
    if m:
        year = int(m.group("y"))
        month1 = _month(m.group("m1"))
        month2 = _month(m.group("m2")) if m.group("m2") else month1
        if month1 is None or month2 is None:
            return None, None
        start = _mk(year, month1, int(m.group("d1")))
        end = _mk(year, month2, int(m.group("d2") or m.group("d1")))
        if start and end and end < start:
            # 'December 28 – January 3, 2026' style window that crosses a year boundary.
            start = _mk(year - 1, month1, int(m.group("d1"))) or start
        return start, end

    return None, None


def clean_name(text: str | None) -> str:
    """Candidate name as displayed: drop footnote markers, incumbent stars, wrapping quotes."""
    t = norm_space(strip_refs(text))
    t = t.strip("*\u2020\u00a0 ")
    t = re.sub(r"\s*\*\s*$", "", t)
    return t.strip()
