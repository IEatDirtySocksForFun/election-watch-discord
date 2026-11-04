"""Hawaii — Office of Elections summary text file.

The Office of Elections publishes one text export per election, linked from the
results page (https://elections.hawaii.gov/election-results/). Two layouts exist:

* 2026 onward — tab-delimited with a ``#Contest ID`` header row:
  ``https://elections.hawaii.gov/wp-content/results/<YYYY> General/summary.txt``
* 2024 and earlier — ``Format#1`` quoted CSV on the files host:
  ``https://files.hawaii.gov/elections/files/results/<YYYY>/General/summary.txt``

Both carry one row per candidate. Candidate names have a party prefix such as
(D), (R), (G) and may span several lines when a joint ticket is listed (the
governor's running mate), so embedded newlines are kept inside quoted fields.
The adapter normalizes the names and maps offices to RaceKey values.
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

BASE = "https://files.hawaii.gov/elections/files/results"  # 2024 and earlier
CURRENT = "https://elections.hawaii.gov/wp-content/results"  # 2026 onward
HUMAN = "https://elections.hawaii.gov/election-results"

#: 2026+ tab-delimited columns the parser reads, by header name.
_TAB_COLUMNS = {
    "Contest ID": "contest_id",
    "Contest Title": "office",
    "Registered Voters": "registered_voters",
    "Total Precincts": "precincts_total",
    "Counted Precincts": "precincts_reporting",
    "Candidate Name": "candidate_raw",
    "Total Votes": "votes_total",
}

_ROMAN = {"I": "01", "II": "02", "III": "03", "IV": "04", "V": "05"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", "\n").strip())


def _parse_name(raw: str) -> tuple[str, str]:
    """'(D) HARRIS, Kamala D. For PRESIDENT ...' -> ('HARRIS, Kamala D.', 'DEM')."""
    text = _norm(raw)
    match = re.match(r"^\(([A-Z])\)\s*(.+)$", text)
    prefix = match.group(1) if match else ""
    body = match.group(2) if match else text
    # President rows include running mate on subsequent lines; keep the first line.
    first = body.split("\n")[0].strip()
    first = re.sub(r"\s+For\s+.*$", "", first, flags=re.I)
    party_map = {
        "D": "DEM",
        "R": "REP",
        "G": "GRN",
        "L": "LIB",
        "S": "NPA",
        "N": "NPA",
        "W": "WRI",
    }
    return first, party_map.get(prefix, c.norm_party(prefix))


def race_key(office_raw: str) -> RaceKey:
    office = _norm(office_raw).upper()
    special = c.is_special(office)
    if office in ("PRESIDENT AND VICE PRESIDENT",):
        return c.local_key("HI", "us-president")
    if office in ("U.S. SENATOR", "U.S.\nSENATOR", "UNITED STATES SENATOR"):
        return RaceKey("HI", Level.SENATE, "SEN", None, special)
    match = re.match(r"^U\.S\.\s*REPRESENTATIVE,\s*DIST\s*(I+|\d+)", office)
    if match:
        dist = _ROMAN.get(match.group(1)) or f"{int(match.group(1)):02d}"
        return RaceKey("HI", Level.HOUSE, "HOUSE", dist, special)
    if office == "GOVERNOR" or office == "GOVERNOR AND LIEUTENANT GOVERNOR":
        # The general election lists governor and lieutenant governor as one ticket.
        return RaceKey("HI", Level.GOVERNOR, "GOV", None, special)
    if office == "LIEUTENANT GOVERNOR":
        return RaceKey("HI", Level.GOVERNOR, "LTGOV", None, special)
    match = re.match(r"^STATE\s+SENATOR,\s*DIST\s*(\d+)", office)
    if match:
        return RaceKey("HI", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^STATE\s+REPRESENTATIVE,\s*DIST\s*(\d+)", office)
    if match:
        return RaceKey("HI", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^QUESTION\s*#?\s*(\d+)", office)
    if match:
        return c.local_key("HI", f"question-{int(match.group(1))}")
    return c.local_key("HI", office)


def _tab_rows(reader) -> list[dict]:
    """2026+ tab-delimited rows -> the column names ``parse_hi`` reads."""
    header = next(reader, [])
    while header and not header[0].strip().startswith("#Contest ID"):
        header = next(reader, [])  # the "#FormatVersion 1" line sits above it
    columns = {}
    for index, name in enumerate(header):
        key = _TAB_COLUMNS.get(name.strip().lstrip("#").strip())
        if key:
            columns[index] = key
    rows = []
    for values in reader:
        if not any(value.strip() for value in values):
            continue
        rows.append({key: values[index] for index, key in columns.items() if index < len(values)})
    return rows


def _rows(text: str) -> list[dict]:
    if text.lstrip("\ufeff").lstrip().startswith("#FormatVersion"):
        return _tab_rows(csv.reader(io.StringIO(text), delimiter="\t", quotechar='"'))
    buf = io.StringIO(text)
    first = buf.readline()
    if first.strip() not in ('"Format#1"', "Format#1"):
        buf.seek(0)
    # csv.reader on a file-like object preserves embedded newlines in quoted fields.
    return list(
        csv.DictReader(
            buf,
            fieldnames=[
                "contest_id",
                "office",
                "contest_num",
                "type",
                "blank1",
                "f1",
                "f2",
                "f3",
                "f4",
                "f5",
                "f6",
                "registered_voters",
                "precincts_reporting",
                "precincts_total",
                "candidate_seq",
                "candidate_raw",
                "party_num",
                "blank2",
                "votes_day",
                "votes_absentee",
                "votes_total",
            ],
        )
    )


def parse_hi(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    by_contest: dict[str, list[dict]] = {}
    for row in rows:
        cid = (row.get("contest_id") or "").strip().strip('"')
        if not cid:
            continue
        by_contest.setdefault(cid, []).append(row)

    results: list[RaceResult] = []
    seen: set[str] = set()
    for rows in by_contest.values():
        first = rows[0]
        office = (first.get("office") or "").replace('"', "")
        key = race_key(office)
        if str(key) in seen:
            continue
        cands = []
        for row in rows:
            raw = (row.get("candidate_raw") or "").replace('"', "")
            name, party = _parse_name(raw)
            if not name:
                continue
            votes = c.to_int(row.get("votes_total"))
            cands.append(CandidateResult(name=name, party=party, votes=votes))
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                _norm(office),
                cands,
                fetched_at,
                source=HiSoe.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=c.to_int(first.get("precincts_reporting")),
                precincts_total=c.to_int(first.get("precincts_total")),
                registered_voters=c.to_int(first.get("registered_voters")) or None,
            )
        )
    return results


def summary_url(date: str) -> str:
    """Text export for an election date; 2026 and later moved to the results host."""
    year = int(date.split("-")[0])
    if year >= 2026:
        return f"{CURRENT}/{year}%20General/summary.txt"
    return f"{BASE}/{year}/General/summary.txt"


async def _summary(client: httpx.AsyncClient, date: str) -> str:
    url = summary_url(date)
    try:
        return await c.get_text(client, url)
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"HI: summary text unavailable ({exc})") from exc


@register
class HiSoe(ResultsSource):
    name = "hi_soe"
    states = frozenset({"HI"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        hi_keys = [key for key in keys if key.state == "HI"]
        if not hi_keys:
            return []
        text = await _summary(client, c.election_date())
        parsed = parse_hi(_rows(text), fetched_at=c.utcnow())
        return c.select(parsed, hi_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "HI":
            return []
        text = await _summary(client, c.election_date())
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in parse_hi(_rows(text), fetched_at=c.utcnow())
        ]
