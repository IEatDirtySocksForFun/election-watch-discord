"""NBC News per-race results as a national fallback ResultsSource.

The same "firecracker" JSON that backs NBC's race calls (see ``nbc.py``)
carries per-candidate ``vote`` / ``votes``, ``percentVote`` and race-level
``totalVote`` / ``percentIn`` for every Senate, Governor and House race. It is
the site's own JSON (no documented API): ``scrape=True``, disabled by default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import house_title, normalize_party, parse_http_date
from .nbc import _results_url, _tooltip_key

_LEVEL_OFFICES = {Level.SENATE: "senate", Level.GOVERNOR: "governor", Level.HOUSE: "house"}
_OFFICE_TITLES = {"senate": "U.S. Senate", "governor": "Governor", "house": "U.S. House"}


@register
class NBCResultsSource(ResultsSource):
    name = "nbc_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
    fallback = True  # ranked after the official state feeds
    states = frozenset(STATES)

    BASE = "https://www.nbcnews.com/firecracker/api/v2/national-results"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    async def _get(self, client: httpx.AsyncClient, office: str) -> dict:
        url = f"{self.BASE}/{self.YEAR}-elections/{office}-results"
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"nbc_results unreachable: {exc}") from exc
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise SourceUnavailable(f"nbc_results returned {resp.status_code} for {office}")
        try:
            return resp.json()
        except Exception as exc:
            raise SourceUnavailable(f"nbc_results bad JSON: {exc}") from exc

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        offices = {
            _LEVEL_OFFICES[key.level]
            for key in keys
            if key.level in _LEVEL_OFFICES
            and key.state in self.states
            and (key.level is not Level.GOVERNOR or key.office == "GOV")
        }
        if not offices:
            return []
        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        for office in sorted(offices):
            results.extend(parse_nbc_results(await self._get(client, office), fetched_at=now))
        requested = set(keys)
        return [race_result for race_result in results if race_result.key in requested]

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for office in _LEVEL_OFFICES.values():
            for race_result in parse_nbc_results(await self._get(client, office), fetched_at=now):
                if race_result.key.state == state:
                    races.append((race_result.key, race_result.title or str(race_result.key)))
        return races


def parse_nbc_results(payload: dict, fetched_at: datetime | None = None) -> list[RaceResult]:
    """Parse an NBC firecracker results payload into RaceResult rows."""
    if not isinstance(payload, dict):
        return []
    fetched_at = fetched_at or datetime.now(timezone.utc)
    observed = parse_http_date(payload.get("lastModified")) or fetched_at
    year = str(payload.get("electionSeason") or "")
    results: list[RaceResult] = []
    for geo, entry in (payload.get("mapData") or {}).items():
        try:
            tooltip = entry.get("tooltip") or {}
            key = _tooltip_key(geo, tooltip)
            if key is None:
                continue
            candidates = [
                CandidateResult(
                    name=candidate.get("name")
                    or " ".join(part for part in (candidate.get("firstName"), candidate.get("lastName")) if part),
                    party=normalize_party(candidate.get("party")),
                    votes=int(candidate.get("votes") or candidate.get("vote") or 0),
                    pct=candidate.get("percentVote"),
                    incumbent=bool(candidate.get("isIncumbent")),
                    winner=False,  # NBC flag is a projection, not official.
                )
                for candidate in tooltip.get("candidates") or []
            ]
            if not candidates:
                continue
            title = _OFFICE_TITLES.get(_LEVEL_OFFICES.get(key.level, ""))
            if key.level is Level.HOUSE and key.district:
                title = house_title(key.district)
            results.append(
                RaceResult(
                    key=key,
                    candidates=candidates,
                    source="nbc_results",
                    fetched_at=fetched_at,
                    source_updated_at=observed,
                    expected_vote_pct=tooltip.get("percentIn"),
                    total_votes=tooltip.get("totalVote"),
                    url=_results_url(key, year),
                    title=title,
                ).finalize()
            )
        except Exception:
            continue
    return results
