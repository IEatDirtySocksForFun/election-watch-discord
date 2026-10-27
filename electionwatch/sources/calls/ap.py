"""Associated Press race-call adapter.

Requires an AP Elections API key (AP_API_KEY). The adapter requests live general
election data by state and office, then parses races where a candidate has been
marked the winner.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import ClassVar

import os

import httpx

from electionwatch.models import Level, RaceCall, RaceKey
from electionwatch.sources.base import CallSource, SourceUnavailable, register

from ._common import normalize_party, parse_iso_ts


@register
class APSource(CallSource):
    name = "ap"
    outlet = "AP"
    env: ClassVar[tuple[str, ...]] = ("AP_API_KEY",)

    BASE = "https://api.ap.org/v3/elections"
    YEAR = 2026
    ELECTION_DATE = "2026-11-03"

    async def fetch_calls(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[RaceCall]:
        api_key = os.environ.get(self.env[0])
        if not api_key:
            raise SourceUnavailable("AP_API_KEY not set")
        calls: list[RaceCall] = []
        for key in keys:
            params: dict[str, str] = {
                "format": "json",
                "statePostal": key.state,
                "officeID": _office_id(key),
                "raceType": "G",
                "resultsType": "l",
                "electionDate": self.ELECTION_DATE,
                "apikey": api_key,
            }
            if key.level is Level.HOUSE and key.district is not None:
                if key.district.isdigit():
                    params["SeatNum"] = str(int(key.district))
                elif key.district == "AL":
                    # AP numbers House seats, at-large included: the single statewide
                    # seat is SeatNum 1 (AP has no "AL"; the race comes back as
                    # seatName "1", which RaceKey normalises to XX-AL).
                    params["SeatNum"] = "1"

            try:
                resp = await client.get(self.BASE, params=params)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"ap unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceUnavailable(f"ap returned {resp.status_code}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"ap bad JSON: {exc}") from exc
            try:
                calls.extend(parse_ap_response(payload))
            except Exception:
                continue

        # Filter to the requested keys (AP may return more than one race per state).
        requested = {k for k in keys}
        return [c for c in calls if c.key in requested]


def _office_id(key: RaceKey) -> str:
    if key.level is Level.SENATE:
        return "S"
    if key.level is Level.GOVERNOR:
        return "G"
    if key.level is Level.HOUSE:
        return "H"
    return ""


def _race_to_key(race: dict) -> RaceKey | None:
    state = race.get("statePostal", "").strip().upper()
    office = race.get("officeID", "").strip()
    if len(state) != 2 or not state.isalpha():
        return None
    if office == "S":
        return RaceKey(state, Level.SENATE, "SEN")
    if office == "G":
        return RaceKey(state, Level.GOVERNOR, "GOV")
    if office == "H":
        seat = race.get("seatName", "").strip()
        if seat.upper().replace("-", " ") in ("AL", "AT LARGE"):
            district = "AL"
        else:
            # AP emits seatName as "1", "District 1" or "1st District".
            m = re.search(r"\d{1,2}", seat)
            if m is None:
                return None
            district = f"{int(m.group(0)):02d}"
        return RaceKey(state, Level.HOUSE, "HOUSE", district)
    return None


def _race_ts(race: dict) -> str | None:
    units = race.get("reportingUnits") or []
    for unit in units:
        if unit.get("level", "").strip().lower() == "state":
            ts = unit.get("lastUpdated")
            if ts:
                return ts
    return race.get("lastUpdated")


def parse_ap_response(payload: dict) -> list[RaceCall]:
    """Parse an AP Elections API response into RaceCall rows."""
    calls: list[RaceCall] = []
    for race in payload.get("races") or []:
        key = _race_to_key(race)
        if key is None:
            continue
        winner = next(
            (c for c in race.get("candidates", []) if c.get("winner") == "X"), None
        )
        if winner is None:
            continue
        calls.append(
            RaceCall(
                key=key,
                outlet="AP",
                winner=f"{winner.get('first', '')} {winner.get('last', '')}".strip(),
                party=normalize_party(winner.get("party")),
                called_at=parse_iso_ts(_race_ts(race))
                or datetime.now(timezone.utc),
                url=None,
            )
        )
    return calls
