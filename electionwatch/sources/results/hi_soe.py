"""Hawaii — Office of Elections certified summary text file.

Hawaii publishes certified results as a fixed-width/CSV text file:

    https://files.hawaii.gov/elections/files/results/<YYYY>/General/summary.txt

The file begins with a "Format#1" header, then one quoted-CSV row per candidate.
Candidate names carry a party prefix such as (D), (R), (G), and may span multiple
lines when a vice-presidential running mate is listed. The adapter normalizes
those names and maps offices to RaceKey values.
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

BASE = "https://files.hawaii.gov/elections/files/results"
HUMAN = "https://elections.hawaii.gov/election-results"

_ROMAN = {"I": "01", "II": "02", "III": "03", "IV": "04", "V": "05"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r", "\n").strip())


def _parse_name(raw: str) -> tuple[str, str]:
    """'(D) HARRIS, Kamala D. For PRESIDENT ...' -> ('HARRIS, Kamala D.', 'DEM')."""
    text = _norm(raw)
    m = re.match(r"^\(([A-Z])\)\s*(.+)$", text)
    prefix = m.group(1) if m else ""
    body = m.group(2) if m else text
    # President rows include running mate on subsequent lines; keep the first line.
    first = body.split("\n")[0].strip()
    first = re.sub(r"\s+For\s+.*$", "", first, flags=re.I)
    party_map = {
        "D": "DEM", "R": "REP", "G": "GRN", "L": "LIB",
        "S": "NPA", "N": "NPA", "W": "WRI",
    }
    return first, party_map.get(prefix, c.norm_party(prefix))


def race_key(office_raw: str) -> RaceKey:
    office = _norm(office_raw).upper()
    special = c.is_special(office)
    if office in ("PRESIDENT AND VICE PRESIDENT",):
        return c.local_key("HI", "us-president")
    if office in ("U.S. SENATOR", "U.S.\nSENATOR", "UNITED STATES SENATOR"):
        return RaceKey("HI", Level.SENATE, "SEN", None, special)
    m = re.match(r"^U\.S\.\s*REPRESENTATIVE,\s*DIST\s*(I+|\d+)", office)
    if m:
        dist = _ROMAN.get(m.group(1)) or f"{int(m.group(1)):02d}"
        return RaceKey("HI", Level.HOUSE, "HOUSE", dist, special)
    m = re.match(r"^STATE\s+SENATOR,\s*DIST\s*(\d+)", office)
    if m:
        return RaceKey("HI", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^STATE\s+REPRESENTATIVE,\s*DIST\s*(\d+)", office)
    if m:
        return RaceKey("HI", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^QUESTION\s*#?\s*(\d+)", office)
    if m:
        return c.local_key("HI", f"question-{int(m.group(1))}")
    return c.local_key("HI", office)


def _rows(text: str) -> list[dict]:
    buf = io.StringIO(text)
    first = buf.readline()
    if first.strip() not in ('"Format#1"', 'Format#1'):
        buf.seek(0)
    # csv.reader on a file-like object preserves embedded newlines in quoted fields.
    return list(csv.DictReader(
        buf,
        fieldnames=[
            "contest_id", "office", "contest_num", "type", "blank1",
            "f1", "f2", "f3", "f4", "f5", "f6",
            "registered_voters", "precincts_reporting", "precincts_total",
            "candidate_seq", "candidate_raw", "party_num", "blank2",
            "votes_day", "votes_absentee", "votes_total",
        ],
    ))


def parse_hi(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    by_contest: dict[str, list[dict]] = {}
    for row in rows:
        cid = (row.get("contest_id") or "").strip().strip('"')
        if not cid:
            continue
        by_contest.setdefault(cid, []).append(row)

    out: list[RaceResult] = []
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
        out.append(c.make_result(
            key, _norm(office), cands, fetched_at,
            source=HiSoe.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=c.to_int(first.get("precincts_reporting")),
            precincts_total=c.to_int(first.get("precincts_total")),
            registered_voters=c.to_int(first.get("registered_voters")) or None,
        ))
    return out


async def _summary(client: httpx.AsyncClient, date: str) -> str:
    year = date.split("-")[0]
    url = f"{BASE}/{year}/General/summary.txt"
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
        hi_keys = [k for k in keys if k.state == "HI"]
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
            (r.key, r.title or str(r.key))
            for r in parse_hi(_rows(text), fetched_at=c.utcnow())
        ]
