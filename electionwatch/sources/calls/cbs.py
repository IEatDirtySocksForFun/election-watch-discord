"""CBS News race-call adapter.

CBS results pages use the public election archive API
``api-election.cbsnews.com/archive/api/public/races2/<year>/G`` with
``filter.officeCode`` / ``filter.state`` query parameters. It is the site's own
JSON rather than a documented API, so ``scrape=True`` and disabled by default.

Each race carries ``stateCode``, ``office``, ``jurisdictionCode`` (the House
district number) and a ``candidates`` list whose ``isWinner`` flag is CBS'
projection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import Level, RaceCall, RaceKey
from electionwatch.sources.base import CallSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import district_key, normalize_party, parse_iso_ts

_OFFICE_SLUGS: dict[str, str] = {"S": "senate", "G": "governor", "H": "house"}
_LEVEL_OFFICES = {Level.SENATE: "S", Level.GOVERNOR: "G", Level.HOUSE: "H"}


@register
class CBSSource(CallSource):
    name = "cbs"
    outlet = "CBS"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False

    BASE = "https://api-election.cbsnews.com/archive/api/public/races2"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    async def fetch_calls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceCall]:
        pairs = {(_LEVEL_OFFICES[key.level], key.state) for key in keys if key.level in _LEVEL_OFFICES}
        calls: list[RaceCall] = []
        for office, state in sorted(pairs):
            try:
                resp = await client.get(
                    f"{self.BASE}/{self.YEAR}/G",
                    params={"filter.officeCode": office, "filter.state": state},
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"cbs unreachable: {exc}") from exc
            if resp.status_code in (403, 404):
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"cbs returned {resp.status_code} for {office}/{state}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"cbs bad JSON: {exc}") from exc
            try:
                calls.extend(parse_cbs_races(payload))
            except Exception:
                continue

        requested = set(keys)
        return [call for call in calls if call.key in requested]


def parse_cbs_races(payload) -> list[RaceCall]:
    """Parse a CBS ``races2`` response (list of races) into RaceCall rows."""
    if not isinstance(payload, list):
        return []
    calls: list[RaceCall] = []
    for race in payload:
        try:
            key = _race_key(race)
            if key is None:
                continue
            winner = next((candidate for candidate in race.get("candidates") or [] if candidate.get("isWinner")), None)
            if winner is None:
                continue
            calls.append(
                RaceCall(
                    key=key,
                    outlet="CBS",
                    winner=winner.get("fullName") or winner.get("lastName") or "",
                    party=normalize_party(winner.get("party")),
                    called_at=_race_ts(race),
                    url=_race_url(race, key),
                )
            )
        except Exception:
            # Never raise on one bad race.
            continue
    return calls


def _race_key(race: dict) -> RaceKey | None:
    state = (race.get("stateCode") or "").strip().upper()
    office = (race.get("office") or "").strip().upper()
    if len(state) != 2 or not state.isalpha():
        return None
    if office == "S":
        return RaceKey(state, Level.SENATE, "SEN")
    if office == "G":
        return RaceKey(state, Level.GOVERNOR, "GOV")
    if office == "H":
        district = district_key(race.get("jurisdictionCode"))
        if district is None:
            return None
        return RaceKey(state, Level.HOUSE, "HOUSE", district)
    return None


def _race_ts(race: dict) -> datetime:
    # CBS does not publish the call time; lastUpdated is the closest stable stamp.
    return parse_iso_ts(race.get("lastUpdated")) or datetime.now(timezone.utc)


def _race_url(race: dict, key: RaceKey) -> str | None:
    slug = _OFFICE_SLUGS.get((race.get("office") or "").upper())
    if slug is None:
        return None
    year = str(race.get("electionDate") or "")[:4]
    return f"https://www.cbsnews.com/elections/{year}/{key.state.lower()}/{slug}/"
