"""Enhanced Voting / Scytl CDN export adapter.

Enhanced Voting hosts results for several states through a vendor CDN export
that has the same shape as the Georgia SOS export:

    https://app.enhancedvoting.com/cdn/results/<State>/export-<slug>.json

The export contains ``results.ballotItems[]`` with candidate options and
``politicalParty`` codes. This adapter reuses the Georgia parser because
Georgia's Secretary of State site is itself powered by Enhanced Voting and
publishes the identical format.

Feeds are configured in ``ELECTIONS`` by state and date. ``2026-11-03``
includes Arizona, which announced it will use Enhanced Voting for the 2026
general election; the slug follows the ``<YYYY>NovGen`` pattern and will be
verified once the state publishes the URL.
"""
from __future__ import annotations

import json
import os

import httpx

from electionwatch.models import RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from .ga_sos import parse_ga, race_key as ga_race_key

BASE = "https://app.enhancedvoting.com/cdn/results"
HUMAN = "https://app.enhancedvoting.com/results/public"

#: date -> list of {state, slug}.  slug None means guess <YYYY>NovGen.
ELECTIONS: dict[str, list[dict]] = {
    "2024-11-05": [
        {"state": "GA", "slug": "2024NovGen"},
    ],
    "2026-11-03": [
        # TODO: verify the Arizona slug once azsos.gov publishes the 2026 general
        # election Enhanced Voting results page. Expected pattern: 2026NovGen.
        {"state": "AZ", "slug": "2026NovGen"},
    ],
}


def export_url(state: str, slug: str) -> str:
    return f"{BASE}/{state}/export-{slug}.json"


def race_key(state: str, name: str) -> RaceKey:
    """Map an Enhanced Voting contest name to a RaceKey for the given state."""
    n = (name or "").upper()
    special = c.is_special(n)
    # Georgia parser already handles President/US House/State House/etc.
    # but its race_key hardcodes "GA"; rebuild with the requested state.
    key = ga_race_key(name)
    return RaceKey(state, key.level, key.office, key.district, key.special)


def feeds(date: str, state: str | None = None) -> list[dict]:
    """Configured Enhanced Voting feeds for a date (and optionally state).

    Override with ``ENHANCED_VOTING_FEEDS`` as a JSON list of
    ``{"state": "...", "slug": "...", "date": "..."}`` objects. Entries need
    ``state`` and ``slug``; ``date`` limits an entry to one election date.
    """
    raw = os.environ.get("ENHANCED_VOTING_FEEDS")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise SourceUnavailable("ENHANCED_VOTING_FEEDS is not valid JSON") from exc
        if not isinstance(entries, list):
            raise SourceUnavailable("ENHANCED_VOTING_FEEDS must be a JSON list")
        out = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("state") or not entry.get("slug"):
                raise SourceUnavailable(
                    "ENHANCED_VOTING_FEEDS entries need state and slug")
            if entry.get("date") and entry["date"] != date:
                continue
            out.append(entry)
        return out

    out = []
    for entry in ELECTIONS.get(date, []):
        if state is None or entry["state"].upper() == state.upper():
            out.append(entry)
    return out


async def _fetch_export(
    client: httpx.AsyncClient,
    state: str,
    slug: str | None,
    date: str,
) -> dict:
    if slug is None:
        slug = f"{date[:4]}NovGen"
    url = export_url(state, slug)
    try:
        return await c.get_json(client, url)  # type: ignore[return-value]
    except SourceUnavailable as exc:
        raise SourceUnavailable(
            f"enhanced_voting: {url} is not published yet or moved ({exc}); "
            f"Enhanced Voting exports appear on election night"
        ) from exc


@register
class EnhancedVoting(ResultsSource):
    name = "enhanced_voting"
    states = frozenset({"AZ", "GA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = {k.state for k in keys}
        date = c.election_date()
        results: list[RaceResult] = []
        for entry in feeds(date):
            st = entry["state"].upper()
            if st not in wanted:
                continue
            payload = await _fetch_export(client, st, entry.get("slug"), date)
            # Re-map Georgia keys to the requested state (usually the same).
            for r in parse_ga(payload, fetched_at=c.utcnow()):
                results.append(
                    c.make_result(
                        race_key(st, r.title or ""),
                        r.title or "",
                        r.candidates,
                        r.fetched_at,
                        source=EnhancedVoting.name,
                        url=f"{HUMAN}/{st.lower()}/elections/{entry.get('slug') or date[:4] + 'NovGen'}",
                        total_votes=r.total_votes,
                        precincts_reporting=r.precincts_reporting,
                        precincts_total=r.precincts_total,
                        source_updated_at=r.source_updated_at,
                    )
                )
        return c.select(results, keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        date = c.election_date()
        for entry in feeds(date, state):
            st = entry["state"].upper()
            payload = await _fetch_export(client, st, entry.get("slug"), date)
            return [
                (race_key(st, r.title or ""), r.title or str(r.key))
                for r in parse_ga(payload, fetched_at=c.utcnow())
            ]
        return []
