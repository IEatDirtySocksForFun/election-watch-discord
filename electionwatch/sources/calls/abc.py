"""ABC News race-call adapter.

ABC's state results widget (``abcnews.go.com/widgets/generalstateresults``)
server-renders its data into ``window['__abcnews__']`` as JSON. This is HTML
scraping of the outlet's own page, so ``scrape=True`` and disabled by default.

Paths used: ``page.content.generalStateResults.aggregates`` -> ``{fips: {races:
[...]}}``. Each race has ``candidates[].winner`` and ``result.severity``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.sources.results._common import election_date
from electionwatch.models import Level, RaceCall, RaceKey
from electionwatch.sources.base import CallSource, SourceUnavailable, register

from ._common import district_key, normalize_party, parse_iso_ts

_CHAMBERS: dict[Level, str] = {
    Level.SENATE: "senate",
    Level.GOVERNOR: "governor",
    Level.HOUSE: "house",
}


@register
class ABCSource(CallSource):
    name = "abc"
    outlet = "ABC"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False

    BASE = "https://abcnews.go.com/widgets/generalstateresults"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    def _url(self, key: RaceKey) -> str | None:
        chamber = _CHAMBERS.get(key.level)
        if chamber is None:
            return None
        return (
            f"{self.BASE}?chamber={chamber}&hideMap=true"
            f"&stateAbbrev={key.state.lower()}&year={self.YEAR}&type=general"
        )

    async def fetch_calls(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[RaceCall]:
        calls: list[RaceCall] = []
        seen: set[str] = set()
        for key in keys:
            url = self._url(key)
            if url is None or url in seen:
                continue
            seen.add(url)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"abc unreachable: {exc}") from exc
            if resp.status_code in (403, 404):
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"abc returned {resp.status_code}")
            try:
                calls.extend(parse_abc_state_page(resp.text))
            except Exception:
                continue

        requested = set(keys)
        return [c for c in calls if c.key in requested]


def parse_abc_state_page(html: str) -> list[RaceCall]:
    """Extract race calls from an ABC general-state-results widget page."""
    config = extract_abc_config(html)
    page = (
        config.get("page", {})
        .get("content", {})
        .get("generalStateResults", {})
    )
    state = ((page.get("state") or {}).get("abbreviation") or "").strip().upper()
    if len(state) != 2 or not state.isalpha():
        return []

    calls: list[RaceCall] = []
    for agg_key, aggregate in (page.get("aggregates") or {}).items():
        for race in aggregate.get("races") or []:
            try:
                call = _race_call(race, state, agg_key)
                if call is not None:
                    calls.append(call)
            except Exception:
                continue
    return calls


def extract_abc_config(html: str) -> dict:
    """Return the JSON object assigned to ``window['__abcnews__']``."""
    for marker in ("window['__abcnews__']=", 'window["__abcnews__"]='):
        pos = html.find(marker)
        if pos >= 0:
            body = html[pos + len(marker) :].lstrip()
            try:
                data, _ = json.JSONDecoder().raw_decode(body)
            except ValueError as exc:
                raise ValueError(f"abc widget config is not JSON: {exc}") from exc
            if isinstance(data, dict):
                return data
    raise ValueError("abc widget config not found")


def _race_call(race: dict, state: str, agg_key: str) -> RaceCall | None:
    chamber = (race.get("chamber") or "").lower()
    winner = next((c for c in race.get("candidates") or [] if c.get("winner")), None)
    if winner is None:
        return None
    if chamber == "senate":
        key = RaceKey(state, Level.SENATE, "SEN")
    elif chamber == "governor":
        key = RaceKey(state, Level.GOVERNOR, "GOV")
    elif chamber == "house":
        district = district_key(str(agg_key)[-2:])
        if district is None:
            return None
        key = RaceKey(state, Level.HOUSE, "HOUSE", district)
    else:
        return None

    called_at = (
        parse_iso_ts((race.get("lastUpdated") or {}).get("value"))
        or datetime.now(timezone.utc)
    )
    return RaceCall(
        key=key,
        outlet="ABC",
        winner=(winner.get("displayName") or winner.get("shortDisplayName") or "").strip(),
        party=normalize_party(winner.get("trueParty") or winner.get("party")),
        called_at=called_at,
        url=race.get("countyResultsLink") or race.get("exitPollsLink"),
    )
