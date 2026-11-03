"""Idaho — Secretary of State live results app (JSON).

The SOS election-results page links the state's live results app:

    https://voteidaho.gov/election-results/
        -> https://results.voteidaho.gov/results/public/id/elections/nov2026

The app is an Angular SPA backed by one JSON document per jurisdiction and election.
The statewide totals -- every federal, legislative and local contest -- are at:

    https://results.voteidaho.gov/results/public/api/elections/id/{slug}/data

`slug` is the lower-case month abbreviation and year of election day ("nov2026",
"may2026", "nov2025"). Each ballot item carries its own `reportingStatus`
(reporting/total precincts and an `asOf` timestamp) and `summaryResults.ballotOptions`
with `voteCount`, `party.abbreviation`, `isWinner` and write-in flags. The document
also exists before polls close, with zero votes, so a 0-vote picture is expected on
election day until counties report.

`id_sos` remains the certified-results adapter (static archive, appears after
certification). This adapter is the election-night feed. It is opt-in: the paths are
the app's own data endpoint rather than a documented API, and the document is large
(about 0.5 MB for a general election).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://results.voteidaho.gov/results/public"
PAGE = BASE + "/id/elections/{slug}"
DATA = BASE + "/api/elections/id/{slug}/data"

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "STATE CONTROLLER": "COMP",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
}


def election_slug(date_iso: str) -> str:
    """'2026-11-03' -> 'nov2026' (the app uses month name + year, not the day)."""
    return date.fromisoformat(date_iso).strftime("%b%Y").lower()


def page_url(date_iso: str) -> str:
    return PAGE.format(slug=election_slug(date_iso))


def data_url(date_iso: str) -> str:
    return DATA.format(slug=election_slug(date_iso))


def race_key(office: str) -> RaceKey:
    """Map a ballot item name ('State Senator District 1') to a RaceKey."""
    name = re.sub(r"\s+", " ", (office or "").strip())
    upper = name.upper()
    special = c.is_special(upper)
    match = re.match(r"^UNITED STATES REPRESENTATIVE DISTRICT (\d{1,2})$", upper)
    if match:
        return RaceKey("ID", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE SENATOR DISTRICT (\d{1,2})$", upper)
    if match:
        return RaceKey("ID", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE REPRESENTATIVE DISTRICT (\d{1,2}) SEAT ([AB])$", upper)
    if match:
        return RaceKey("ID", Level.STATE, "HD", f"{int(match.group(1)):02d}{match.group(2)}", special)
    if upper == "UNITED STATES SENATOR":
        return RaceKey("ID", Level.SENATE, "SEN", None, special)
    if upper in _STATEWIDE:
        return RaceKey("ID", Level.GOVERNOR, _STATEWIDE[upper], None, special)
    return c.local_key("ID", name)


def _text(entries) -> str:
    """First English entry of a localised name list."""
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("languageId") == "en" and entry.get("text"):
            return str(entry["text"]).strip()
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("text"):
            return str(entry["text"]).strip()
    return ""


def _timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_id_data(payload: dict, *, fetched_at: datetime, date_iso: str | None = None) -> list[RaceResult]:
    """Pure parse of one statewide `data` document."""
    election = payload.get("election") or {}
    election_as_of = _timestamp(election.get("asOf"))
    if date_iso is None:
        date_iso = election.get("electionDate") or c.election_date()
    url = page_url(date_iso)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for item in payload.get("ballotItems") or []:
        title = _text(item.get("name"))
        if not title:
            continue
        candidates: list[CandidateResult] = []
        for option in (item.get("summaryResults") or {}).get("ballotOptions") or []:
            name = _text(option.get("name"))
            if not name:
                continue
            party = c.norm_party((option.get("party") or {}).get("abbreviation"))
            if option.get("isWriteIn") and not party:
                party = "WRI"
            candidates.append(
                CandidateResult(
                    name=name,
                    party=party,
                    votes=c.to_int(option.get("voteCount")),
                    winner=bool(option.get("isWinner")),
                )
            )
        if not candidates:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        seen.add(str(key))
        reporting = item.get("reportingStatus") or {}
        reporting_units = reporting.get("reportingUnits")
        total_units = reporting.get("totalUnits")
        vote_total = item.get("voteTotal")
        results.append(
            c.make_result(
                key,
                title,
                candidates,
                fetched_at,
                source=IdResults.name,
                url=url,
                total_votes=c.to_int(vote_total) if vote_total is not None else None,
                precincts_reporting=c.to_int(reporting_units) if reporting_units is not None else None,
                precincts_total=c.to_int(total_units) if total_units is not None else None,
                source_updated_at=_timestamp(reporting.get("asOf")) or election_as_of,
            )
        )
    return results


async def _all_races(client: httpx.AsyncClient, date_iso: str, fetched_at: datetime) -> list[RaceResult]:
    try:
        payload = await c.get_json(client, data_url(date_iso))
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"ID: the live results app has nothing for {date_iso} ({exc})") from exc
    if not isinstance(payload, dict):
        raise SourceUnavailable(f"ID: unexpected live results payload for {date_iso}")
    return parse_id_data(payload, fetched_at=fetched_at, date_iso=date_iso)


@register
class IdResults(ResultsSource):
    name = "id_results"
    states = frozenset({"ID"})
    scrape = True
    enabled_by_default = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        id_keys = [key for key in keys if key.state == "ID"]
        if not id_keys:
            return []
        date_iso = c.election_date()
        return c.select(await _all_races(client, date_iso, c.utcnow()), id_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "ID":
            return []
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in await _all_races(client, c.election_date(), c.utcnow())
        ]
