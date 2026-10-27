"""Delaware — Department of Elections results CSV.

Delaware publishes a single statewide CSV for each election:

    http://elections.delaware.gov/reports/GE2024.csv

Rows are one candidate per office. The CSV contains no precinct counts and no
winner flags, but it does carry a per-row timestamp (`xmlreporttime`) and the
party name for each candidate.

`ELECTIONS` maps an ISO election date to the report filename.  The 2026
general filename is not published yet; discovery checks the SOS reports page.
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
HUMAN = "https://elections.delaware.gov/reports/GE2024.html"

#: date -> CSV filename (without path); None = discover.
ELECTIONS: dict[str, str | None] = {
    "2024-11-05": "GE2024.csv",
    "2026-11-03": None,
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


def race_key(office: str) -> RaceKey:
    n = _norm(office)
    special = c.is_special(n)
    if n in ("PRESIDENT AND VICE PRESIDENT", "PRESIDENT"):
        return c.local_key("DE", "us-president")
    if n in ("U.S. SENATOR", "UNITED STATES SENATOR", "US SENATOR"):
        return RaceKey("DE", Level.SENATE, "SEN", None, special)
    if n in ("U.S. REPRESENTATIVE IN CONGRESS", "US REPRESENTATIVE",
             "UNITED STATES REPRESENTATIVE", "U.S. REPRESENTATIVE"):
        return RaceKey("DE", Level.HOUSE, "HOUSE", "AL", special)
    if n == "GOVERNOR":
        return RaceKey("DE", Level.GOVERNOR, "GOV", None, special)
    if n in ("LIEUTENANT GOVERNOR", "LT. GOVERNOR"):
        return RaceKey("DE", Level.GOVERNOR, "LTGOV", None, special)
    if n in ("INSURANCE COMMISSIONER", "COMMISSIONER OF INSURANCE"):
        return RaceKey("DE", Level.GOVERNOR, "INSCOM", None, special)
    if n in ("ATTORNEY GENERAL", "AG"):
        return RaceKey("DE", Level.GOVERNOR, "AG", None, special)
    if n in ("SECRETARY OF STATE", "SOS"):
        return RaceKey("DE", Level.GOVERNOR, "SOS", None, special)
    if n in ("TREASURER", "STATE TREASURER"):
        return RaceKey("DE", Level.GOVERNOR, "TREAS", None, special)
    if n in ("AUDITOR OF ACCOUNTS", "STATE AUDITOR", "AUDITOR"):
        return RaceKey("DE", Level.GOVERNOR, "AUD", None, special)
    m = re.search(r"STATE SENATOR DISTRICT\s+(\d+)", n)
    if m:
        return RaceKey("DE", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = re.search(r"STATE REPRESENTATIVE DISTRICT\s+(\d+)", n)
    if m:
        return RaceKey("DE", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    return c.local_key("DE", office)


def _candidate_name(raw: str) -> str:
    # Delaware tickets are "FIRST LAST AND RUNNING MATE".
    return (raw or "").strip().title()


def _party(raw: str) -> str | None:
    # "Democratic Party" / "Republican Party" -> DEM / REP via norm_party.
    p = re.sub(r"\s+Party\s*$", "", (raw or "").strip(), flags=re.I)
    return c.norm_party(p)


def _updated(raw: str) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    # ISO 8601 from the CSV, e.g. "2024-11-12T12:59:37.000".
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z?", text)
    if m:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=timezone.utc)
    return c.parse_updated_at(text)


def parse_de(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the Delaware statewide CSV rows."""
    by_office: dict[str, list[dict]] = {}
    for row in rows:
        office = (row.get("office") or "").strip()
        if not office:
            continue
        by_office.setdefault(office, []).append(row)

    out: list[RaceResult] = []
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
        out.append(c.make_result(
            key, office, cands, fetched_at,
            source=DeDoe.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            source_updated_at=updated,
        ))
    return out


async def _csv_url(client: httpx.AsyncClient, date: str) -> str:
    filename = ELECTIONS.get(date)
    if filename:
        return f"{BASE}/{filename}"
    # Discovery: scrape the reports archive page for the newest general CSV.
    text = await c.get_text(client, f"{BASE}/")
    m = re.search(r"href=\"(GE\d{4}\.csv)\"", text)
    if not m:
        raise SourceUnavailable(
            f"DE: no CSV filename configured for {date} and the reports page "
            f"does not list a GEYYYY.csv file")
    return f"{BASE}/{m.group(1)}"


@register
class DeDoe(ResultsSource):
    name = "de_doe"
    states = frozenset({"DE"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        de_keys = [k for k in keys if k.state == "DE"]
        if not de_keys:
            return []
        url = await _csv_url(client, c.election_date())
        text = await c.get_text(client, url)
        rows = list(csv.DictReader(text.splitlines()))
        return c.select(parse_de(rows, fetched_at=c.utcnow()), de_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "DE":
            return []
        url = await _csv_url(client, c.election_date())
        text = await c.get_text(client, url)
        rows = list(csv.DictReader(text.splitlines()))
        seen: set[str] = set()
        out: list[tuple[RaceKey, str]] = []
        for row in rows:
            office = (row.get("office") or "").strip()
            if not office:
                continue
            key = race_key(office)
            if str(key) in seen:
                continue
            seen.add(str(key))
            out.append((key, office))
        return out
