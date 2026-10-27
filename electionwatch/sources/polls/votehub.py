"""VoteHub polling API adapter (verified 2026-10-27).

``GET https://api.votehub.com/polls`` returns VoteHub's whole public polling
database as one JSON array (~2.9 MB, no auth, no pagination; query parameters
such as ``poll_type``/``state`` are accepted but ignored by the API). Every
record carries a stable ``id``, the pollster, fieldwork dates, sample and
population, sponsors and a ``partisan`` flag -- which is why this adapter is
enabled by default while the HTML scrapers are not.

Caveat: ``answers[].choice`` is a candidate name only. VoteHub publishes no
party per answer, so ``PollResult.party`` is ``None`` for candidate polls
(party-word answers such as the generic ballot are normalised instead).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

import httpx

from electionwatch.models import Level, Poll, PollResult, RaceKey, normalize_party
from electionwatch.racekeys import STATES
from electionwatch.sources.base import PollSource, SourceUnavailable, register

from ._common import norm_space, state_code

log = logging.getLogger(__name__)

API_URL = "https://api.votehub.com/polls"

# poll_type -> Level the race belongs to
_TYPES: dict[str, Level] = {
    "us-senator": Level.SENATE,
    "governor": Level.GOVERNOR,
    "us-representative": Level.HOUSE,
}

_POPULATION = {"lv": "LV", "rv": "RV", "a": "A"}

# Single-district states: VoteHub writes "AK-01", race keys use "AK-AL".
AT_LARGE = frozenset({"AK", "DE", "ND", "SD", "VT", "WY"})

# VoteHub's generic-ballot answers are party words ("Dem"/"Rep"), not names.
_PARTY_ANSWERS = {
    "dem": "DEM", "democrat": "DEM", "democratic": "DEM",
    "rep": "REP", "republican": "REP", "gop": "REP",
    "independent": "IND", "libertarian": "LIB", "green": "GRN",
}

_SUBJECT = re.compile(r"^(?P<year>\d{4})\s+(?P<body>.+?)(?:\s+special)?$")
_DISTRICT = re.compile(r"^(?P<state>[A-Z]{2})-(?P<district>\d{1,2}|AL)$", re.I)


def parse_subject(subject: str | None) -> tuple[str, str | None] | None:
    """'2026 Michigan' -> ('MI', None); '2026 IL-04' -> ('IL', '04')."""
    m = _SUBJECT.match(norm_space(subject))
    if not m:
        return None
    body = m.group("body").strip()
    dm = _DISTRICT.match(body)
    if dm:
        state = dm.group("state").upper()
        if state not in STATES:
            return None
        district = dm.group("district").upper()
        if district != "AL":
            district = f"{int(district):02d}"
            if state in AT_LARGE and district == "01":
                district = "AL"
        return state, district
    state = state_code(body)
    if state:
        return state, None
    return None


def _key_index(keys: list[RaceKey]) -> dict[tuple[str, Level, str | None], RaceKey]:
    index: dict[tuple[str, Level, str | None], RaceKey] = {}
    for key in keys:
        index.setdefault((key.state, key.level, key.district), key)
    return index


def _result(choice: str, pct: float) -> PollResult:
    name = norm_space(choice)
    party = normalize_party(_PARTY_ANSWERS.get(name.casefold()))
    return PollResult(name=name, party=party, pct=float(pct))


def parse_votehub_polls(payload: list[dict], keys: list[RaceKey]) -> list[Poll]:
    """Turn the VoteHub ``/polls`` array into :class:`Poll` objects for ``keys``."""
    index = _key_index(keys)
    polls: list[Poll] = []
    for record in payload or []:
        kind = record.get("poll_type")
        if kind not in _TYPES:
            continue
        level = _TYPES[kind]
        subject = parse_subject(record.get("subject"))
        if not subject:
            continue
        state, district = subject
        if level is not Level.HOUSE:
            district = None
        key = index.get((state, level, district))
        if key is None:
            continue
        end_raw = record.get("end_date")
        if not end_raw:
            continue
        try:
            end_date = date.fromisoformat(end_raw)
            start_date = date.fromisoformat(record["start_date"]) if record.get("start_date") else None
        except ValueError:
            continue
        results = []
        for answer in record.get("answers") or []:
            try:
                pct = float(answer.get("pct"))
            except (TypeError, ValueError):
                continue
            if not 0 <= pct <= 100 or not answer.get("choice"):
                continue
            results.append(_result(str(answer["choice"]), pct))
        if not results:
            continue
        sponsors = [s for s in (record.get("sponsors") or []) if s]
        polls.append(
            Poll(
                key=key,
                pollster=norm_space(record.get("pollster")) or "Unknown",
                results=results,
                end_date=end_date,
                start_date=start_date,
                sample_size=record.get("sample_size") or None,
                population=_POPULATION.get((record.get("population") or "").lower()),
                sponsor=", ".join(sponsors) or None,
                partisan=normalize_party(record.get("partisan")),
                source="votehub",
                url=record.get("url") or None,
            )
        )
    polls.sort(key=lambda p: (p.end_date, p.pollster), reverse=True)
    return polls


@register
class VoteHubPollSource(PollSource):
    """VoteHub's public polling API (JSON, no key)."""

    name = "votehub"
    enabled_by_default = True
    scrape = False

    CACHE_SECONDS = 30 * 60   # the instance lives for the whole process: refresh periodically

    def __init__(self) -> None:
        self._cache: list[dict] | None = None
        self._cached_at = 0.0

    async def _payload(self, client: httpx.AsyncClient) -> list[dict]:
        if self._cache is None or time.monotonic() - self._cached_at >= self.CACHE_SECONDS:
            try:
                resp = await client.get(API_URL, headers={"Accept": "application/json"})
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"votehub: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceUnavailable(f"votehub: HTTP {resp.status_code}")
            try:
                data = resp.json()
            except ValueError as exc:   # an HTML error/challenge page instead of JSON
                raise SourceUnavailable("votehub: response is not JSON") from exc
            if not isinstance(data, list):
                raise SourceUnavailable("votehub: unexpected payload")
            self._cache, self._cached_at = data, time.monotonic()
        return self._cache

    async def fetch_polls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[Poll]:
        if not keys:
            return []
        payload = await self._payload(client)
        polls = parse_votehub_polls(payload, keys)
        log.info("votehub: %d polls for %d keys", len(polls), len(keys))
        return polls
