"""CNN race-call adapter.

CNN's results pages are backed by a public JSON feed at politics.api.cnn.io.
This adapter is marked as a scraper because it consumes the site's own JSON
rather than a documented public API, and it is disabled by default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import RaceCall, RaceKey
from electionwatch.sources.base import CallSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import (
    cnn_code_to_key,
    epoch_ms_to_dt,
    key_to_cnn_code,
    normalize_party,
    parse_iso_ts,
)


@register
class CNNSource(CallSource):
    name = "cnn"
    outlet = "CNN"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False

    BASE = "https://politics.api.cnn.io/results/race"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    async def fetch_calls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceCall]:
        calls: list[RaceCall] = []
        for key in keys:
            code = key_to_cnn_code(key, self.YEAR)
            if not code:
                continue
            url = f"{self.BASE}/{code}.json"
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"cnn unreachable: {exc}") from exc
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"cnn returned {resp.status_code} for {code}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"cnn bad JSON: {exc}") from exc
            try:
                call = parse_cnn_race(payload)
            except Exception:
                continue
            if call and call.key == key:
                calls.append(call)
        return calls


def parse_cnn_race(payload: dict) -> RaceCall | None:
    if payload.get("editorialStatus") != "called":
        return None
    key = cnn_code_to_key(payload.get("ecKey", ""))
    if key is None:
        return None
    winner = next((c for c in payload.get("candidates", []) if c.get("winner")), None)
    if winner is None:
        return None
    party = normalize_party(winner.get("majorParty") or winner.get("partyName") or payload.get("winnerBopPartyId"))
    embargo = payload.get("raceEmbargo") or {}
    return RaceCall(
        key=key,
        outlet="CNN",
        winner=winner.get("fullName") or f"{winner.get('firstName', '')} {winner.get('lastName', '')}".strip(),
        party=party,
        # raceEmbargo.callsRelease is the race's call timestamp; voteTimestamp is
        # the last ballot update (often months later, e.g. certification).
        called_at=(
            epoch_ms_to_dt(embargo.get("callsRelease"))
            or parse_iso_ts(payload.get("voteTimestamp"))
            or datetime.now(timezone.utc)
        ),
        url=f"https://www.cnn.com/election/{key.state.lower()}",
    )
