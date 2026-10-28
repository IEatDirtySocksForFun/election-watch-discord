"""Fox News per-race results as a national fallback ResultsSource.

Fox's state-level results feeds carry per-candidate vote counts and
precincts/expected-vote totals for Senate, Governor and House races. These are
scraper endpoints with no documented API, so ``scrape=True`` and disabled by
default.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register
from electionwatch.sources.results._common import election_date

from ._common import district_key, normalize_party, parse_iso_ts
from .fox import _OFFICE_NAMES, _results_url


@register
class FOXResultsSource(ResultsSource):
    name = "fox_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
    fallback = True  # ranked after the official state feeds
    states = frozenset(STATES)

    BASE = "https://feeds-elections.foxnews.com/archive/politics/elections"
    YEAR = int(election_date()[:4])  # from ELECTION_DATE (default 2026-11-03)

    def _url(self, key: RaceKey) -> str | None:
        office = _OFFICE_NAMES.get(key.level)
        if office is None:
            return None
        return f"{self.BASE}/{self.YEAR}/3/{self.YEAR}_Generals/{office}/{key.state}/state_level_results/file.json"

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        pairs: set[tuple[str, RaceKey]] = set()
        for key in keys:
            if key.level not in _OFFICE_NAMES or key.state not in self.states:
                continue
            if key.level is Level.GOVERNOR and key.office != "GOV":
                continue
            pairs.add((_OFFICE_NAMES[key.level], key))

        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        seen: set[str] = set()
        for office, key in sorted(pairs, key=lambda p: (p[0], str(p[1]))):
            url = self._url(key)
            if url is None or url in seen:
                continue
            seen.add(url)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"fox_results unreachable: {exc}") from exc
            if resp.status_code in (403, 404):
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"fox_results returned {resp.status_code}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"fox_results bad JSON: {exc}") from exc
            results.extend(parse_fox_results(payload, key.state, key.level, self.YEAR, fetched_at=now))

        requested = set(keys)
        return [r for r in results if r.key in requested]

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for level in (Level.SENATE, Level.GOVERNOR, Level.HOUSE):
            key = RaceKey(
                state, level, "SEN" if level is Level.SENATE else "GOV" if level is Level.GOVERNOR else "HOUSE"
            )
            url = self._url(key)
            if url is None:
                continue
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code != 200:
                continue
            try:
                payload = resp.json()
            except Exception:
                continue
            races.extend(
                (r.key, r.title or str(r.key))
                for r in parse_fox_results(payload, state, level, self.YEAR, fetched_at=now)
            )
        return races


def parse_fox_results(
    payload, state: str, level: Level, year: str | int = "", fetched_at: datetime | None = None
) -> list[RaceResult]:
    """Parse one Fox state-level feed into RaceResult rows."""
    fetched_at = fetched_at or datetime.now(timezone.utc)
    out: list[RaceResult] = []
    if level is Level.HOUSE:
        for race in payload.get("races") or []:
            try:
                district = district_key((race.get("district") or {}).get("districtNumber"))
                if district is None:
                    continue
                key = RaceKey(state, Level.HOUSE, "HOUSE", district)
                candidates, totals = _parse_fox_race(race)
                if not candidates:
                    continue
                title = f"U.S. House District {int(district)}"
                out.append(
                    RaceResult(
                        key=key,
                        candidates=candidates,
                        source="fox_results",
                        fetched_at=fetched_at,
                        source_updated_at=parse_iso_ts(payload.get("last_updated")),
                        precincts_reporting=totals.get("precinctsReporting"),
                        expected_vote_pct=totals.get("expectedPercentage"),
                        total_votes=totals.get("votes"),
                        url=_results_url(key, year),
                        title=title,
                    ).finalize()
                )
            except Exception:
                continue
        return out

    # Senate / Governor payloads are a list with one race object.
    races = payload if isinstance(payload, list) else [payload]
    for race in races:
        try:
            key = (
                RaceKey(state, Level.SENATE, "SEN") if level is Level.SENATE else RaceKey(state, Level.GOVERNOR, "GOV")
            )
            candidates, totals = _parse_fox_race(race)
            if not candidates:
                continue
            title = "U.S. Senate" if level is Level.SENATE else "Governor"
            out.append(
                RaceResult(
                    key=key,
                    candidates=candidates,
                    source="fox_results",
                    fetched_at=fetched_at,
                    source_updated_at=parse_iso_ts(race.get("last_updated")),
                    precincts_reporting=totals.get("precinctsReporting"),
                    expected_vote_pct=totals.get("expectedPercentage"),
                    total_votes=totals.get("votes"),
                    url=_results_url(key, year),
                    title=title,
                ).finalize()
            )
        except Exception:
            continue
    return out


def _parse_fox_race(race: dict) -> tuple[list[CandidateResult], dict]:
    """Return (candidates, totals) for a Fox race object or district wrapper."""
    results = race.get("results") or []
    totals = race.get("districtTotals") or race.get("stateTotals") or {}
    candidates: list[CandidateResult] = []
    for entry in results:
        try:
            candidate = entry.get("candidate") or {}
            name = " ".join(p for p in (candidate.get("firstName"), candidate.get("lastName")) if p)
            party = (
                entry.get("partyCode")
                or candidate.get("partyCode")
                or entry.get("partyName")
                or candidate.get("partyName")
            )
            votes = entry.get("votes") or {}
            candidates.append(
                CandidateResult(
                    name=name,
                    party=normalize_party(party),
                    votes=int(votes.get("count") or 0),
                    pct=votes.get("percentage"),
                    incumbent=bool(entry.get("isIncumbent") or candidate.get("isIncumbent")),
                    winner=False,  # Fox flag is a projection, not official.
                )
            )
        except Exception:
            continue
    return candidates, totals
