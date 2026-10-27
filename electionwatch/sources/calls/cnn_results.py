"""CNN per-race results as a national fallback ResultsSource.

CNN's public race JSON (the same feed used by ``cnn.py`` for calls) carries
``totalVote``, ``percentReporting`` and per-candidate ``voteNum`` /
``votePercentNum``. It is the site's own JSON rather than a documented API, so
``scrape=True`` and disabled by default.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import STATES
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from ._common import cnn_code_to_key, epoch_ms_to_dt, key_to_cnn_code, normalize_party, parse_iso_ts


@register
class CNNResultsSource(ResultsSource):
    name = "cnn_results"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = True
    enabled_by_default = False
    states = frozenset(STATES)

    BASE = "https://politics.api.cnn.io/results/race"
    YEAR = 2024

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        now = datetime.now(timezone.utc)
        results: list[RaceResult] = []
        for key in keys:
            if key.level not in (Level.SENATE, Level.GOVERNOR, Level.HOUSE):
                continue
            if key.level is Level.GOVERNOR and key.office != "GOV":
                continue
            if key.state not in self.states:
                continue
            code = key_to_cnn_code(key, self.YEAR)
            if not code:
                continue
            url = f"{self.BASE}/{code}.json"
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"cnn_results unreachable: {exc}") from exc
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(f"cnn_results returned {resp.status_code} for {code}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"cnn_results bad JSON: {exc}") from exc
            try:
                result = parse_cnn_results(payload, fetched_at=now)
            except Exception:
                continue
            if result and result.key == key:
                results.append(result)
        return results

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        now = datetime.now(timezone.utc)
        races: list[tuple[RaceKey, str]] = []
        for level, office, code_template in (
            (Level.SENATE, "SEN", f"{self.YEAR}-SG-"),
            (Level.GOVERNOR, "GOV", f"{self.YEAR}-GO-"),
        ):
            code = f"{code_template}{state}"
            url = f"{self.BASE}/{code}.json"
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
            result = parse_cnn_results(payload, fetched_at=now)
            if result:
                races.append((result.key, result.title or str(result.key)))
        return races


def parse_cnn_results(payload: dict, fetched_at: datetime | None = None) -> RaceResult | None:
    """Parse a CNN race JSON into a single RaceResult."""
    if not isinstance(payload, dict):
        return None
    key = cnn_code_to_key(payload.get("ecKey", ""))
    if key is None:
        return None
    candidates = [
        CandidateResult(
            name=c.get("fullName") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip(),
            party=normalize_party(
                c.get("majorParty") or c.get("partyName") or c.get("candidatePartyCode")
            ),
            votes=int(c.get("voteNum") or 0),
            pct=c.get("votePercentNum"),
            incumbent=bool(c.get("isIncumbent")),
            winner=False,  # CNN flag is a projection, not official.
        )
        for c in payload.get("candidates", [])
    ]
    if not candidates:
        return None

    title = "U.S. Senate" if key.level is Level.SENATE else (
        "Governor" if key.level is Level.GOVERNOR else f"U.S. House District {int(key.district)}"
    )
    embargo = payload.get("raceEmbargo") or {}
    return RaceResult(
        key=key,
        candidates=candidates,
        source="cnn_results",
        fetched_at=fetched_at or datetime.now(timezone.utc),
        source_updated_at=(
            epoch_ms_to_dt(embargo.get("callsRelease"))
            or parse_iso_ts(payload.get("voteTimestamp"))
        ),
        precincts_reporting=payload.get("percentReporting"),
        expected_vote_pct=payload.get("percentReporting"),
        total_votes=payload.get("totalVote"),
        url=f"https://www.cnn.com/election/{key.state.lower()}",
        title=title,
    ).finalize()
