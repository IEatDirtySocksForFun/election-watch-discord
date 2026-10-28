"""Mississippi — Secretary of State statewide recapitulation report (CSV).

Mississippi has no election-night JSON feed: the SOS publishes one CSV per election on
its results page, built from the counties' recap sheets:

    https://www.sos.ms.gov/elections/electionresults/<YYYY>ElectionRecapSheets.csv

    "County","Office","Candidate","Party","County Total","State Total"
    "Adams","United States-Senate","Ty Pinkins","Democrat","6560","450749"

One row per candidate per county, with the statewide figure repeated in "State Total", so
the parser groups by office and keeps the state total (never the county sum, which would
be right only if every county reported).

Offices are named with the office first and the district second:

    United States-President                       -> LOCAL us-president
    United States-Senate                          -> SEN
    US House Of Rep 01-1st Congressional District -> HOUSE 01
    State Senate 05-...                           -> SD-05
    State House Of Rep 12-...                     -> HD-12
    everything else (supreme court, court of appeals, ...) -> LOCAL slug

The file lands after the count is canvassed rather than on election night, which the
results page says outright ("official 2024 general election results"). It carries no
precinct counts and no winner column.

Discovery: the results page for a general election is
`/elections/electionresults_aspx/elections_results_<YYYY>_general.aspx`; the CSV link on it
is followed instead of hard-coding the file name, so 2026 works once the page exists.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

PAGE = "https://www.sos.ms.gov/elections/electionresults_aspx/elections_results_{year}_general.aspx"
HUMAN = "https://www.sos.ms.gov/elections-voting/election-results"

ELECTIONS: dict[str, str | None] = {
    # date -> CSV file name; None = follow the links on that year's results page.
    "2024-11-05": "2024ElectionRecapSheets.csv",
    "2026-11-03": None,
}

_CSV_LINK = re.compile(r'href="([^"]*ElectionRecapSheets\.csv)"', re.I)

_STATEWIDE = (
    ("LIEUTENANT GOVERNOR", "LTGOV"),
    ("ATTORNEY GENERAL", "AG"),
    ("SECRETARY OF STATE", "SOS"),
    ("STATE TREASURER", "TREAS"),
    ("COMMISSIONER OF AGRICULTURE", "AGCOM"),
    ("COMMISSIONER OF INSURANCE", "INSCOM"),
    ("GOVERNOR", "GOV"),
)

#: "Presidential Electors for X for President and Y for Vice President"
_ELECTORS = re.compile(r"^Presidential Electors for (.+?) for President and (.+?) for Vice President$", re.I)


def _candidate_name(office: str, name: str) -> str:
    """Slates arrive as elector prose; keep the ticket as 'President / Vice President'."""
    if office.upper().startswith("UNITED STATES-PRESIDENT"):
        match = _ELECTORS.match(name)
        if match:
            return f"{match.group(1).strip()} / {match.group(2).strip()}"
    return name


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _office_district(name: str) -> str | None:
    """'US House Of Rep 01-1st Congressional District' -> '01'."""
    match = re.search(r"\b(?:REP|REPRESENTATIVES?|SENATE|HOUSE)\s*(\d{1,3})\b", name)
    if not match:
        match = re.search(r"\bDISTRICT\s*(\d{1,3})\b", name)
    return f"{int(match.group(1)):02d}" if match else None


def race_key(office: str) -> RaceKey:
    """Map a Mississippi office column to a RaceKey."""
    office_name = _norm(office)
    special = c.is_special(office_name)
    if office_name.startswith("UNITED STATES-PRESIDENT"):
        return RaceKey("MS", Level.LOCAL, "LOCAL", "us-president")
    if office_name.startswith("UNITED STATES-SENATE"):
        return RaceKey("MS", Level.SENATE, "SEN", None, special)
    match = re.match(r"^US HOUSE OF REP", office_name)
    if match:
        return RaceKey("MS", Level.HOUSE, "HOUSE", _office_district(office_name) or "00", special)
    match = re.match(r"^STATE SENATE", office_name)
    if match:
        return RaceKey("MS", Level.STATE, "SD", _office_district(office_name) or "00", special)
    match = re.match(r"^STATE (?:HOUSE|REP)", office_name)
    if match:
        return RaceKey("MS", Level.STATE, "HD", _office_district(office_name) or "00", special)
    for title, code in _STATEWIDE:
        if title in office_name:
            return RaceKey("MS", Level.GOVERNOR, code, None, special)
    return c.local_key("MS", office_name)


def parse_ms(text: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the statewide recap CSV."""
    rows = [record for record in csv.DictReader(io.StringIO(text or "")) if record.get("Office")]
    by_office: dict[str, dict[tuple[str, str], int]] = {}
    for row in rows:
        office = str(row.get("Office") or "").strip()
        name = str(row.get("Candidate") or "").strip()
        if not office or not name:
            continue
        party = c.norm_party(row.get("Party")) or ""
        votes = c.to_int(row.get("State Total"))
        bucket = by_office.setdefault(office, {})
        key = (_candidate_name(office, name), party)
        bucket[key] = max(bucket.get(key, 0), votes)

    results: list[RaceResult] = []
    seen: set[str] = set()
    for office, tally in by_office.items():
        key = race_key(office)
        if str(key) in seen:
            continue
        cands = [CandidateResult(name=name, party=party or None, votes=votes) for (name, party), votes in tally.items()]
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                office,
                cands,
                fetched_at,
                source=MsSos.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
            )
        )
    return results


def _csv_name_from_page(html: str) -> str | None:
    match = _CSV_LINK.search(html or "")
    return match.group(1).rsplit("/", 1)[-1] if match else None


async def _feed(client: httpx.AsyncClient, date: str) -> str:
    """Absolute CSV URL for the tracked election."""
    year = date[:4]
    pinned = ELECTIONS.get(date)
    if pinned:
        return f"https://www.sos.ms.gov/elections/electionresults/{pinned}"
    page = PAGE.format(year=year)
    html = await c.get_text(client, page)
    name = _csv_name_from_page(html)
    if not name:
        raise SourceUnavailable(f"MS: no ElectionRecapSheets.csv link on {page}")
    return f"https://www.sos.ms.gov/elections/electionresults/{name}"


@register
class MsSos(ResultsSource):
    name = "ms_sos"
    states = frozenset({"MS"})
    scrape = False  # the results themselves are a CSV file; only discovery reads HTML

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "MS" for key in keys):
            return []
        text = await c.get_text(client, await _feed(client, c.election_date()))
        return c.select(parse_ms(text, fetched_at=c.utcnow()), [key for key in keys if key.state == "MS"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MS":
            return []
        text = await c.get_text(client, await _feed(client, c.election_date()))
        return [(result.key, result.title or str(result.key)) for result in parse_ms(text, fetched_at=c.utcnow())]
