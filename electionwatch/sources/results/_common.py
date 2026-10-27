"""Helpers shared by the state results adapters.

Parsing functions in the adapters are pure: they take an already-fetched payload and
return `RaceResult` objects. Everything that touches the network lives in `fetch()`.
"""
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import date, datetime, timezone

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import SourceUnavailable

#: The election the bot follows unless ELECTION_DATE overrides it.
DEFAULT_ELECTION_DATE = "2026-11-03"

_PARTY_MAP = {
    "DEM": "DEM", "D": "DEM", "DFL": "DEM",
    "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM",
    "DEMOCRATICFARMERLABOR": "DEM",
    "REP": "REP", "R": "REP", "GOP": "REP", "REPUBLICAN": "REP",
    "LIB": "LIB", "L": "LIB", "LIBERTARIAN": "LIB",
    "GRE": "GRN", "GRN": "GRN", "GREEN": "GRN",
    "CST": "CON", "CON": "CON", "CONSTITUTION": "CON",
    "IND": "IND", "I": "IND", "INDEPENDENT": "IND",
    "W": "WRI", "WI": "WRI", "WRITE-IN": "WRI", "WRITEIN": "WRI",
    "NON": "NPA", "NP": "NPA", "NPA": "NPA", "UNA": "NPA", "UNK": "NPA",
    "ASP": "ASP", "FWD": "FWD", "PF": "PF",
}

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def election_date() -> str:
    """ISO date of the election being tracked (ELECTION_DATE env, default 2026-11-03)."""
    return os.environ.get("ELECTION_DATE", DEFAULT_ELECTION_DATE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_date(value: str) -> str:
    """'11/03/2026' or '11/3/2026' -> '2026-11-03'."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", value or "")
    if not m:
        raise ValueError(f"not a US date: {value!r}")
    return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def us_date(iso: str) -> str:
    """'2026-11-03' -> '11/03/2026' (the form the state sites take in a query string)."""
    d = date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year:04d}"


def norm_party(value: str | None) -> str | None:
    if value is None:
        return None
    p = re.sub(r"[^A-Za-z]", "", str(value)).upper()
    if not p:
        return None
    return _PARTY_MAP.get(p, p)


def to_int(value) -> int:
    """Vote counts arrive as ints, '1,234' or '' depending on the state."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^0-9-]", "", str(value))
    return int(digits) if digits not in ("", "-") else 0


def to_pct(value, *, fraction: bool = False) -> float | None:
    """Percent as 0-100. `fraction=True` means the source sends 0-1."""
    if value in (None, ""):
        return None
    try:
        num = float(str(value).strip().rstrip("%"))
    except ValueError:
        return None
    if fraction:
        num *= 100.0
    return round(num, 2)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "race"


def is_special(name: str) -> bool:
    n = (name or "").upper()
    return "SPECIAL" in n or "UNEXPIRED" in n


def strip_incumbent(name: str) -> tuple[str, bool]:
    """'TED CRUZ (I)' -> ('TED CRUZ', True)."""
    m = re.search(r"\s*\((?:I|INC\.?|INCUMBENT)\)\s*$", name or "", re.I)
    if m:
        return name[: m.start()].strip(), True
    return (name or "").strip(), False


def make_result(
    key: RaceKey,
    title: str,
    candidates: list[CandidateResult],
    fetched_at: datetime,
    *,
    source: str,
    url: str | None = None,
    total_votes: int | None = None,
    precincts_reporting: int | None = None,
    precincts_total: int | None = None,
    expected_vote_pct: float | None = None,
    registered_voters: int | None = None,
    turnout_pct: float | None = None,
    source_updated_at: datetime | None = None,
) -> RaceResult:
    if expected_vote_pct is None and precincts_reporting and precincts_total:
        expected_vote_pct = round(100.0 * precincts_reporting / precincts_total, 2)
    return RaceResult(
        key=key,
        candidates=candidates,
        source=source,
        fetched_at=fetched_at,
        source_updated_at=source_updated_at,
        precincts_reporting=precincts_reporting,
        precincts_total=precincts_total,
        expected_vote_pct=expected_vote_pct,
        total_votes=total_votes,
        registered_voters=registered_voters,
        turnout_pct=turnout_pct,
        url=url,
        title=title,
    ).finalize()


def select(results: list[RaceResult], keys: list[RaceKey]) -> list[RaceResult]:
    wanted = {str(k) for k in keys}
    return [r for r in results if str(r.key) in wanted]


def _decode_payload(resp: httpx.Response) -> object:
    try:
        return resp.json()
    except ValueError:
        pass
    # A few state feeds serve gzip bodies without Content-Encoding; httpx leaves those alone.
    body = resp.content
    try:
        return json.loads(gzip.decompress(body))
    except (OSError, ValueError) as exc:
        raise SourceUnavailable(f"{resp.url} returned undecodable JSON") from exc


async def get_json(client: httpx.AsyncClient, url: str) -> object:
    try:
        resp = await client.get(url, headers={"Accept": "application/json, text/plain, */*"})
    except httpx.HTTPError as exc:  # timeouts, DNS, TLS
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceUnavailable(f"GET {url} -> HTTP {resp.status_code}")
    return _decode_payload(resp)


async def get_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        resp = await client.get(url, headers={"Accept": "text/plain, */*"})
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceUnavailable(f"GET {url} -> HTTP {resp.status_code}")
    if resp.content[:2] == b"\x1f\x8b":
        return gzip.decompress(resp.content).decode("utf-8", "replace")
    return resp.text


def parse_updated_at(text: str | None, *, tz: str | None = None) -> datetime | None:
    """Parse the timestamp strings state sites publish ('6/25/2026 1:40:00 PM PDT')."""
    if not text:
        return None
    raw = str(text).strip()
    raw = re.sub(r"^(?:" + "|".join(_WEEKDAYS) + r")\,?\s+", "", raw, flags=re.I)
    raw = re.sub(r"\s+([ap])\.?m\.?\s*$", lambda m: f" {m.group(1).upper()}M", raw, flags=re.I)
    formats = (
        "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M %p",
        "%b %d, %Y %H:%M:%S", "%B %d, %Y %I:%M:%S %p", "%B %d, %Y %I:%M %p",
        "%B %d, %Y, %I:%M:%S %p", "%B %d, %Y, %I:%M %p",
        "%Y-%m-%dT%H:%M:%S",
    )
    for fmt in formats:
        try:
            naive = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if tz:
            try:
                from zoneinfo import ZoneInfo
                return naive.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
            except Exception:  # no tzdata in a slim container
                return naive.replace(tzinfo=timezone.utc)
        return naive.replace(tzinfo=timezone.utc)
    return None


def district_from(text: str) -> str | None:
    """'District 01' / '1st Congressional District' / 'D16' -> '01' / '01' / '16'."""
    m = re.search(r"(?:DISTRICT|DIST\.?|D)\s*[-# ]?\s*(\d{1,3})", text or "", re.I)
    if m:
        return f"{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,3})(?:st|nd|rd|th)\s+CONGRESSIONAL", text or "", re.I)
    if m:
        return f"{int(m.group(1)):02d}"
    return None


def local_key(state: str, name: str) -> RaceKey:
    return RaceKey(state, Level.LOCAL, "LOCAL", slug(name))
