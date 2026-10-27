"""ABC News per-race results as a national fallback ResultsSource.

ABC's state results widget server-renders per-candidate vote counts,
percentages and reporting percentages into ``window['__abcnews__']``. It is
HTML scraping of the outlet's own page, so ``scrape=True`` and disabled by
default.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.sources.results._common import election_date
from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from ._common import district_key, normalize_party, parse_iso_ts, house_title
from .abc import extract_abc_config

_CHAMBERS: dict[Level, str] = {
    Level.SENATE: "senate",
    Level.GOVERNOR: "governor",
    Level.HOUSE: "house",
}
_CHAMBER_TITLES = {"senate": "U.S. Senate", "governor": "Governor", "house": "U.S. House"}


@register
class ABCResultsSource(ResultsSource):
    name = "abc_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
    states = frozenset(STATES)

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

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        pairs: set[tuple[str, RaceKey]] = set()
        for key in keys:
            chamber = _CHAMBERS.get(key.level)
            if chamber is None or key.state not in self.states:
                continue
            if key.level is Level.GOVERNOR and key.office != "GOV":
                continue
            pairs.add((chamber, key))

        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        seen: set[str] = set()
        for chamber, key in sorted(pairs, key=lambda p: (p[0], str(p[1]))):
            url = self._url(key)
            if url is None or url in seen:
                continue
            seen.add(url)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"abc_results unreachable: {exc}") from exc
            if resp.status_code in (403, 404):
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"abc_results returned {resp.status_code}")
            try:
                results.extend(parse_abc_results(resp.text, fetched_at=now))
            except Exception:
                continue

        requested = set(keys)
        return [r for r in results if r.key in requested]

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for chamber in _CHAMBERS.values():
            key = RaceKey(state, Level.SENATE, "SEN") if chamber == "senate" else (
                RaceKey(state, Level.GOVERNOR, "GOV") if chamber == "governor" else RaceKey(state, Level.HOUSE, "HOUSE")
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
                races.extend(
                    (r.key, r.title or str(r.key))
                    for r in parse_abc_results(resp.text, fetched_at=now)
                    if r.key.state == state
                )
            except Exception:
                continue
        return races


def parse_abc_results(html: str, fetched_at: datetime | None = None) -> list[RaceResult]:
    """Extract race results from an ABC general-state-results widget page."""
    try:
        config = extract_abc_config(html)
    except ValueError:
        return []
    page = (
        config.get("page", {})
        .get("content", {})
        .get("generalStateResults", {})
    )
    state = ((page.get("state") or {}).get("abbreviation") or "").strip().upper()
    if len(state) != 2 or not state.isalpha():
        return []

    fetched_at = fetched_at or datetime.now(timezone.utc)
    page_updated = parse_iso_ts((page.get("lastUpdated") or {}).get("value"))
    results: list[RaceResult] = []
    for agg_key, aggregate in (page.get("aggregates") or {}).items():
        for race in aggregate.get("races") or []:
            try:
                res = _race_result(race, state, agg_key, fetched_at, page_updated)
                if res is not None:
                    results.append(res)
            except Exception:
                continue
    return results


def _race_result(
    race: dict, state: str, agg_key: str, fetched_at: datetime, page_updated: datetime | None
) -> RaceResult | None:
    chamber = (race.get("chamber") or "").lower()
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

    candidates = [
        CandidateResult(
            name=(c.get("displayName") or c.get("shortDisplayName") or "").strip(),
            party=normalize_party(c.get("trueParty") or c.get("party")),
            votes=int(c.get("rawVotes") or 0),
            pct=c.get("rawPercent"),
            incumbent=bool(c.get("incumbent")),
            winner=False,  # ABC flag is a projection, not official.
        )
        for c in race.get("candidates") or []
    ]
    if not candidates:
        return None

    reporting = race.get("reporting") or {}
    title = _CHAMBER_TITLES.get(chamber)
    if key.level is Level.HOUSE and key.district:
        title = house_title(key.district)
    updated = parse_iso_ts((race.get("lastUpdated") or {}).get("value")) or page_updated
    return RaceResult(
        key=key,
        candidates=candidates,
        source="abc_results",
        fetched_at=fetched_at,
        source_updated_at=updated,
        expected_vote_pct=reporting.get("doubleValue") or reporting.get("value"),
        total_votes=sum(c.votes for c in candidates) or None,
        url=race.get("countyResultsLink") or race.get("exitPollsLink"),
        title=title,
    ).finalize()
