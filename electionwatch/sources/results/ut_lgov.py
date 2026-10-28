"""Utah — Lieutenant Governor / electionresults.utah.gov (Enhanced Voting CDN export).

The Utah results site (https://electionresults.utah.gov/results/public/Utah/elections/
general11052024) is an Enhanced Voting app, and the same export that backs it is
published on the vendor CDN with one file per election:

    https://app.enhancedvoting.com/cdn/results/Utah/export-general<MMDDYYYY>.json

e.g. export-general11052024.json (29 MB) for 2024-11-05. The slug embeds the date
in MMDDYYYY order, unlike the <YYYY>NovGen slugs used for Georgia/Arizona, so the
slug is configured per election in ELECTIONS.

Shape (same vendor envelope as ga_sos):

    {"electionDate": "2024-11-05", "electionName": "...", "createdAt": "...",
     "results": {"id": "...", "name": "Utah",
                 "ballotItems": [
                   {"type": "State", "name": "U.S. House 1 ", "voteFor": 1,
                    "precinctsParticipating": null, "precinctsReporting": null,
                    "contestType": "Candidate",
                    "ballotOptions": [
                      {"name": "BURGESS OWENS", "politicalParty": "Republican",
                       "voteCount": 123456, "precinctResults": null}, ...]}],
                 "reportingStatuses": [...]},
     "localResults": {...}}

Utah contest names carry the district in the name with a trailing parenthetical
marker, e.g. "State Senate 2 (Multi-County)" and "State House 59  (Multi-County)";
the marker is dropped and the number becomes the district. Ballot measures are
"For"/"Against" choices with no party. Every item in results.ballotItems is
type "State"; localResults (county-level items) is not parsed.

The export carries no winner flags and its precinct counts are null, so those
fields stay False/None.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://app.enhancedvoting.com/cdn/results/Utah"
HUMAN = "https://electionresults.utah.gov/"

#: date -> export slug.  None = derive as general<MMDDYYYY>.
ELECTIONS: dict[str, str | None] = {
    "2024-11-05": "general11052024",  # verified fixture
    # TODO(2026-10-27): the public 2026 general page
    # (electionresults.utah.gov/results/public/Utah/elections/general11032026) is
    # live but the CDN export is still 404; Enhanced Voting posts it on election
    # night. The slug is date-derived, so no code change should be needed.
    "2026-11-03": "general11032026",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
    "STATE TREASURER": "TREAS",
}

_PARTIES = {
    "INDEPENDENT AMERICAN": "IAP",
    "UNITED UTAH PARTY": "UU",
    "UTAH FORWARD": "FWD",
    "UNAFFILIATED": "NPA",
}


def _norm(name: str) -> str:
    """'U.S. House 1 ' / 'State House 59  (Multi-County)' -> 'US HOUSE 1' / 'STATE HOUSE 59'."""
    cleaned = re.sub(r"\s+", " ", str(name or "")).strip()
    cleaned = re.sub(r"\s*\((?:multi-county|2 year term)[^)]*\)\s*$", "", cleaned, flags=re.I)
    return cleaned.replace("U.S.", "US").upper()


def _party(value: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not raw:
        return None
    return _PARTIES.get(raw) or c.norm_party(raw)


def race_key(name: str) -> RaceKey:
    office_name = _norm(name)
    if office_name in ("US PRESIDENT", "PRESIDENT OF THE UNITED STATES"):
        return c.local_key("UT", "us-president")
    if office_name in ("US SENATE", "US SENATOR"):
        return RaceKey("UT", Level.SENATE, "SEN")
    match = re.match(r"^US HOUSE\s*(\d+)$", office_name)
    if match:
        return RaceKey("UT", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}")
    if office_name in _STATEWIDE:
        return RaceKey("UT", Level.GOVERNOR, _STATEWIDE[office_name])
    match = re.match(r"^STATE SENATE\s*(\d+)$", office_name)
    if match:
        return RaceKey("UT", Level.STATE, "SD", f"{int(match.group(1)):02d}")
    match = re.match(r"^STATE HOUSE\s*(\d+)$", office_name)
    if match:
        return RaceKey("UT", Level.STATE, "HD", f"{int(match.group(1)):02d}")
    return c.local_key("UT", office_name)


def parse_ut(payload, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Utah (Enhanced Voting) export document."""
    if not isinstance(payload, dict):
        return []
    items = (payload.get("results") or {}).get("ballotItems") or []
    results: list[RaceResult] = []
    seen: set[str] = set()
    for ballot_item in items:
        title = re.sub(r"\s+", " ", str(ballot_item.get("name") or "")).strip()
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        cands = []
        for option in ballot_item.get("ballotOptions") or []:
            cand_name, incumbent = c.strip_incumbent(str(option.get("name") or "").strip())
            if not cand_name:
                continue
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=_party(option.get("politicalParty")),
                    votes=c.to_int(option.get("voteCount")),
                    incumbent=incumbent,
                    winner=False,  # the export publishes no winner flag
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=UtLgov.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=c.to_int(ballot_item.get("precinctsReporting")) or None,
                precincts_total=c.to_int(ballot_item.get("precinctsParticipating")) or None,
            )
        )
    return results


def export_slug(date: str) -> str:
    slug = ELECTIONS.get(date)
    if slug:
        return slug
    year, month, day = (int(part) for part in date.split("-"))
    return f"general{month:02d}{day:02d}{year:04d}"


def export_url(date: str) -> str:
    return f"{BASE}/export-{export_slug(date)}.json"


@register
class UtLgov(ResultsSource):
    name = "ut_lgov"
    states = frozenset({"UT"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ut_keys = [key for key in keys if key.state == "UT"]
        if not ut_keys:
            return []
        url = export_url(c.election_date())
        try:
            payload = await c.get_json(client, url)
        except SourceUnavailable as exc:
            raise SourceUnavailable(
                f"ut_lgov: {url} is not published yet or moved ({exc}); Utah's "
                f"Enhanced Voting export appears on election night"
            ) from exc
        return c.select(parse_ut(payload, fetched_at=c.utcnow()), ut_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "UT":
            return []
        try:
            payload = await c.get_json(client, export_url(c.election_date()))
        except SourceUnavailable:
            return []
        return [(result.key, result.title or str(result.key)) for result in parse_ut(payload, fetched_at=c.utcnow())]
