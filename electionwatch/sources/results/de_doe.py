"""Delaware — Department of Elections results.

Delaware publishes two official feeds for each election, both plain GETs:

    https://elections.delaware.gov/results/enr/Election_StatewideResults_ID_GE<year>.json
    http://elections.delaware.gov/reports/GE<year>.csv

The JSON is what the Division's own results page (`/results/enr/GE<year>.html`) reads
every five minutes: one row per candidate, carrying the contest title, the party, the
vote split by method, the total, the percentage and a per-row ``ReportTime``. It is
published before the election with every vote at 0, and moves to ``/reports/json/``
once that election is no longer the current one. Older elections keep only the CSV,
which has one row per candidate and no precinct counts; that URL is built from the
filing convention and a 404 there means the report is not published yet.

The JSON also carries ``Precincts Reported`` / ``Total Precincts`` per contest (530 for a
statewide contest, 37 for Senate District 1 in the 2026 file), so the per-race precinct
fields are filled from it. The CSV has no precinct columns, so the CSV fallback leaves
them empty.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://elections.delaware.gov/reports"
ENR = "https://elections.delaware.gov/results/enr"
HUMAN = "https://elections.delaware.gov/reports/GE2024.html"

#: date -> CSV filename. Dates not listed use GE<year>.csv.
ELECTIONS: dict[str, str] = {
    "2024-11-05": "GE2024.csv",
    # 2026-11-03 is not listed: the report will be published as GE2026.csv.
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


def race_key(office: str) -> RaceKey:
    office_name = _norm(office)
    special = c.is_special(office_name)
    if office_name in ("PRESIDENT AND VICE PRESIDENT", "PRESIDENT"):
        return c.local_key("DE", "us-president")
    if office_name in ("U.S. SENATOR", "UNITED STATES SENATOR", "US SENATOR"):
        return RaceKey("DE", Level.SENATE, "SEN", None, special)
    if office_name in (
        "U.S. REPRESENTATIVE IN CONGRESS",
        "REPRESENTATIVE IN CONGRESS",
        "US REPRESENTATIVE",
        "UNITED STATES REPRESENTATIVE",
        "U.S. REPRESENTATIVE",
    ):
        return RaceKey("DE", Level.HOUSE, "HOUSE", "AL", special)
    if office_name == "GOVERNOR":
        return RaceKey("DE", Level.GOVERNOR, "GOV", None, special)
    if office_name in ("LIEUTENANT GOVERNOR", "LT. GOVERNOR"):
        return RaceKey("DE", Level.GOVERNOR, "LTGOV", None, special)
    if office_name in ("INSURANCE COMMISSIONER", "COMMISSIONER OF INSURANCE"):
        return RaceKey("DE", Level.GOVERNOR, "INSCOM", None, special)
    if office_name in ("ATTORNEY GENERAL", "AG"):
        return RaceKey("DE", Level.GOVERNOR, "AG", None, special)
    if office_name in ("SECRETARY OF STATE", "SOS"):
        return RaceKey("DE", Level.GOVERNOR, "SOS", None, special)
    if office_name in ("TREASURER", "STATE TREASURER"):
        return RaceKey("DE", Level.GOVERNOR, "TREAS", None, special)
    if office_name in ("AUDITOR OF ACCOUNTS", "STATE AUDITOR", "AUDITOR"):
        return RaceKey("DE", Level.GOVERNOR, "AUD", None, special)
    match = re.search(r"STATE SENATOR DISTRICT\s+(\d+)", office_name)
    if match:
        return RaceKey("DE", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.search(r"STATE REPRESENTATIVE DISTRICT\s+(\d+)", office_name)
    if match:
        return RaceKey("DE", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
    return c.local_key("DE", office)


def _candidate_name(raw: str) -> str:
    # Delaware tickets are "FIRST LAST AND RUNNING MATE".
    return (raw or "").strip().title()


def _party(raw: str) -> str | None:
    # "Democratic Party" / "Republican Party" -> DEM / REP via norm_party.
    party_text = re.sub(r"\s+Party\s*$", "", (raw or "").strip(), flags=re.I)
    return c.norm_party(party_text)


def _updated(raw: str) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    # ISO 8601 from the CSV, e.g. "2024-11-12T12:59:37.000".
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z?", text)
    if match:
        return datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}").replace(tzinfo=timezone.utc)
    return c.parse_updated_at(text)


def parse_de(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the Delaware statewide CSV rows."""
    by_office: dict[str, list[dict]] = {}
    for row in rows:
        office = (row.get("office") or "").strip()
        if not office:
            continue
        by_office.setdefault(office, []).append(row)

    results: list[RaceResult] = []
    seen: set[str] = set()
    for office, crow in by_office.items():
        key = race_key(office)
        if str(key) in seen:
            continue
        cands = []
        updated: datetime | None = None
        for row in crow:
            name = _candidate_name(row.get("candidatename"))
            if not name:
                continue
            party = _party(row.get("partyname"))
            votes = c.to_int(row.get("totalvotessum"))
            pct = c.to_pct(row.get("totalvotespercentage"))
            cands.append(CandidateResult(name=name, party=party, votes=votes, pct=pct))
            if updated is None:
                updated = _updated(row.get("xmlreporttime"))
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                office,
                cands,
                fetched_at,
                source=DeDoe.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                source_updated_at=updated,
            )
        )
    return results


def enr_url(date: str) -> str:
    """Statewide ENR JSON for an election.

    While that election is the current one the file sits at ``/results/enr/``; after
    the election it moves to ``/reports/json/`` (the request follows that redirect).
    """
    return f"{ENR}/Election_StatewideResults_ID_GE{date[:4]}.json"


def _enr_updated(raw) -> datetime | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return c.parse_updated_at(str(raw))
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def parse_de_enr(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the statewide ENR JSON (one row per candidate)."""
    by_contest: dict[str, list[dict]] = {}
    for row in rows:
        title = str(row.get("Contest Title") or "").strip()
        if not title:
            continue
        by_contest.setdefault(title, []).append(row)

    results: list[RaceResult] = []
    seen: set[str] = set()
    for title, contest_rows in by_contest.items():
        key = race_key(title)
        if str(key) in seen:
            continue
        cands = []
        updated: datetime | None = None
        reporting = total_precincts = None
        for row in contest_rows:
            name = _candidate_name(row.get("Candidate Name"))
            if not name:
                continue
            cands.append(
                CandidateResult(
                    name=name,
                    party=_party(row.get("Party Name")),
                    votes=c.to_int(row.get("Total Votes")),
                    pct=c.to_pct(row.get("Percentage")),
                )
            )
            if updated is None:
                updated = _enr_updated(row.get("ReportTime"))
            if reporting is None:
                reporting = c.to_int(row.get("Precincts Reported"))
                total_precincts = c.to_int(row.get("Total Precincts"))
        if not cands:
            continue
        seen.add(str(key))
        election_id = str(contest_rows[0].get("Election Id") or "").strip()
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=DeDoe.name,
                url=f"{ENR}/{election_id}.html" if election_id else None,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=reporting,
                precincts_total=total_precincts,
                source_updated_at=updated,
            )
        )
    return results


def parse_rows(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    """Parse whichever feed ``fetch_rows`` returned: ENR JSON rows or reports CSV rows."""
    if rows and "Contest Title" in rows[0]:
        return parse_de_enr(rows, fetched_at=fetched_at)
    return parse_de(rows, fetched_at=fetched_at)


def csv_url(date: str) -> str:
    """Report URL for an election: the configured filename, else GE<year>.csv.

    Used for elections that predate the ENR JSON (and as the fallback when the JSON
    is absent). Delaware exposes no machine-readable index of the reports directory
    (it answers 403), so the URL is built from the filing convention instead of
    discovered. Before publication the year's file simply does not exist yet —
    ``fetch_rows`` reports that as SourceUnavailable.
    """
    filename = ELECTIONS.get(date) or f"GE{date[:4]}.csv"
    return f"{BASE}/{filename}"


async def fetch_rows(client: httpx.AsyncClient, date: str) -> list[dict]:
    """Rows for an election: the ENR JSON while it is published, else the reports CSV.

    A 404 on both feeds means the election is not published yet, which is reported as
    SourceUnavailable rather than treated as an outage.
    """
    try:
        return await _enr_rows(client, date)
    except SourceUnavailable as exc:
        if "HTTP 404" not in str(exc):
            raise
    url = csv_url(date)
    try:
        text = await c.get_text(client, url)
    except SourceUnavailable as exc:
        if "HTTP 404" in str(exc):
            raise SourceUnavailable(f"DE: {enr_url(date)} and {url} are not published yet for {date}") from exc
        raise
    return list(csv.DictReader(text.splitlines()))


async def _enr_rows(client: httpx.AsyncClient, date: str) -> list[dict]:
    url = enr_url(date)
    try:
        resp = await client.get(url, headers={"Accept": "application/json, text/plain, */*"}, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceUnavailable(f"GET {url} -> HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise SourceUnavailable(f"{url} returned undecodable JSON") from exc
    if not isinstance(payload, list):
        raise SourceUnavailable(f"{url} returned {type(payload).__name__}, not a list of rows")
    return [row for row in payload if isinstance(row, dict)]


@register
class DeDoe(ResultsSource):
    name = "de_doe"
    states = frozenset({"DE"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        de_keys = [key for key in keys if key.state == "DE"]
        if not de_keys:
            return []
        rows = await fetch_rows(client, c.election_date())
        return c.select(parse_rows(rows, fetched_at=c.utcnow()), de_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "DE":
            return []
        rows = await fetch_rows(client, c.election_date())
        seen: set[str] = set()
        races: list[tuple[RaceKey, str]] = []
        for race_result in parse_rows(rows, fetched_at=c.utcnow()):
            if str(race_result.key) in seen:
                continue
            seen.add(str(race_result.key))
            races.append((race_result.key, race_result.title or str(race_result.key)))
        return races
