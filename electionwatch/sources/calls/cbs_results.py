"""CBS News per-race results as a national fallback ResultsSource.

The same ``races2`` JSON that backs CBS' race calls (see ``cbs.py``) carries
per-candidate vote counts, ``pctIn`` (CBS' expected-vote estimate) and
``totalVote`` for every Senate, Governor and House race in every state. That
makes it a usable fallback when a state's official feed is down or blocked
(e.g. results.texas-election.com behind a Cloudflare challenge).

It is the site's own JSON, not a documented API: ``scrape=True``, disabled by
default. Enable with ``EW_ENABLED_SOURCES=cbs_results``. Only US Senate,
Governor and US House are covered. ``winner`` stays False because CBS' flag is a
projection, not an official declaration (the call itself comes from ``cbs``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult, normalize_party
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import parse_iso_ts
from .cbs import CBSSource, _race_key, _race_url

_LEVEL_OFFICES = {Level.SENATE: "S", Level.GOVERNOR: "G", Level.HOUSE: "H"}
_OFFICE_TITLES = {"S": "U.S. Senate", "G": "Governor", "H": "U.S. House"}


@register
class CBSResultsSource(ResultsSource):
    name = "cbs_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
    fallback = True  # ranked after the official state feeds
    states = frozenset(STATES)

    BASE = CBSSource.BASE
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    async def _get(self, client: httpx.AsyncClient, office: str, state: str) -> list:
        try:
            resp = await client.get(
                f"{self.BASE}/{self.YEAR}/G",
                params={"filter.officeCode": office, "filter.state": state},
            )
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"cbs_results unreachable: {exc}") from exc
        if resp.status_code in (403, 404):
            return []
        if resp.status_code >= 400:
            raise SourceUnavailable(f"cbs_results returned {resp.status_code} for {office}/{state}")
        try:
            return resp.json()
        except Exception as exc:
            raise SourceUnavailable(f"cbs_results bad JSON: {exc}") from exc

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        pairs = {
            (_LEVEL_OFFICES[key.level], key.state)
            for key in keys
            if key.level in _LEVEL_OFFICES
            and key.state in self.states
            and (key.level is not Level.GOVERNOR or key.office == "GOV")
        }
        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        for office, state in sorted(pairs):
            results.extend(parse_cbs_results(await self._get(client, office, state), fetched_at=now))
        requested = set(keys)
        return [result for result in results if result.key in requested]

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for office in ("S", "G", "H"):
            for result in parse_cbs_results(await self._get(client, office, state), fetched_at=now):
                races.append((result.key, result.title or str(result.key)))
        return races


def parse_cbs_results(payload, fetched_at: datetime | None = None) -> list[RaceResult]:
    """Parse a CBS ``races2`` response (a list of races) into RaceResult rows."""
    if not isinstance(payload, list):
        return []
    fetched_at = fetched_at or datetime.now(timezone.utc)
    results: list[RaceResult] = []
    for race in payload:
        try:
            key = _race_key(race)
            if key is None:
                continue
            candidates = [
                CandidateResult(
                    name=candidate.get("fullName")
                    or f"{candidate.get('firstName', '')} {candidate.get('lastName', '')}".strip(),
                    party=normalize_party(candidate.get("party")),
                    votes=int(candidate.get("vote") or 0),
                    pct=candidate.get("votePct"),
                    incumbent=bool(candidate.get("isIncumbent")),
                )
                for candidate in race.get("candidates") or []
            ]
            if not candidates:
                continue
            office = (race.get("office") or "").upper()
            title = _OFFICE_TITLES.get(office)
            if office == "H" and key.district:
                title = f"U.S. House District {key.district}"
            results.append(
                RaceResult(
                    key=key,
                    candidates=candidates,
                    source="cbs_results",
                    fetched_at=fetched_at,
                    source_updated_at=parse_iso_ts(race.get("lastUpdated")) if race.get("lastUpdated") else None,
                    expected_vote_pct=race.get("pctIn"),
                    total_votes=race.get("totalVote"),
                    url=_race_url(race, key),
                    title=title,
                ).finalize()
            )
        except Exception:
            # Never raise on one bad race.
            continue
    return results
