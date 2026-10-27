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

from electionwatch.sources.results._common import election_date
from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from ._common import normalize_party, parse_http_date, house_title
from .nbc import _results_url, _tooltip_key

_LEVEL_OFFICES = {Level.SENATE: "senate", Level.GOVERNOR: "governor", Level.HOUSE: "house"}
_OFFICE_TITLES = {"senate": "U.S. Senate", "governor": "Governor", "house": "U.S. House"}


@register
class NBCResultsSource(ResultsSource):
    name = "nbc_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
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
            _LEVEL_OFFICES[k.level]
            for k in keys
            if k.level in _LEVEL_OFFICES and k.state in self.states
            and (k.level is not Level.GOVERNOR or k.office == "GOV")
        }
        if not offices:
            return []
        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        for office in sorted(offices):
            results.extend(parse_nbc_results(await self._get(client, office), fetched_at=now))
        requested = set(keys)
        return [r for r in results if r.key in requested]

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for office in _LEVEL_OFFICES.values():
            for r in parse_nbc_results(await self._get(client, office), fetched_at=now):
                if r.key.state == state:
                    races.append((r.key, r.title or str(r.key)))
        return races


def parse_nbc_results(payload: dict, fetched_at: datetime | None = None) -> list[RaceResult]:
    """Parse an NBC firecracker results payload into RaceResult rows."""
    if not isinstance(payload, dict):
        return []
    fetched_at = fetched_at or datetime.now(timezone.utc)
    observed = parse_http_date(payload.get("lastModified")) or fetched_at
    year = str(payload.get("electionSeason") or "")
    out: list[RaceResult] = []
    for geo, entry in (payload.get("mapData") or {}).items():
        try:
            tooltip = entry.get("tooltip") or {}
            key = _tooltip_key(geo, tooltip)
            if key is None:
                continue
            candidates = [
                CandidateResult(
                    name=c.get("name") or " ".join(
                        p for p in (c.get("firstName"), c.get("lastName")) if p
                    ),
                    party=normalize_party(c.get("party")),
                    votes=int(c.get("votes") or c.get("vote") or 0),
                    pct=c.get("percentVote"),
                    incumbent=bool(c.get("isIncumbent")),
                    winner=False,  # NBC flag is a projection, not official.
                )
                for c in tooltip.get("candidates") or []
            ]
            if not candidates:
                continue
            title = _OFFICE_TITLES.get(_LEVEL_OFFICES.get(key.level, ""))
            if key.level is Level.HOUSE and key.district:
                title = house_title(key.district)
            out.append(
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
    return out
