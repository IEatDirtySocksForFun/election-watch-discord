"""Connecticut — Secretary of State ElectionStats search CSV.

Connecticut publishes historical and current election results through the
ElectionStats platform. A single search CSV export lists every contest and
candidate with city/town-level detail:

    https://ct.elstats.civera.com/api/download_search.csv?search=<json>

The adapter aggregates the city/town rows to statewide totals. Contest IDs are
discovered by searching the election year; the CSV itself carries the office
and district names needed to map races.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from collections import defaultdict

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://ct.elstats.civera.com"
HUMAN = "https://electionhistory.ct.gov/search"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


def _district_number(text: str) -> str:
    m = re.search(r"\b(\d+)\b", text or "")
    return f"{int(m.group(1)):02d}" if m else "00"


def race_key(office: str, district: str | None = None) -> RaceKey:
    o = _norm(office)
    d = _norm(district or "")
    special = c.is_special(o) or c.is_special(d)
    if o == "PRESIDENT":
        return c.local_key("CT", "us-president")
    if o in ("UNITED STATES SENATOR", "U.S. SENATOR", "US SENATOR"):
        return RaceKey("CT", Level.SENATE, "SEN", None, special)
    if o in ("REPRESENTATIVE IN CONGRESS", "U.S. REPRESENTATIVE", "US REPRESENTATIVE",
             "UNITED STATES REPRESENTATIVE"):
        return RaceKey("CT", Level.HOUSE, "HOUSE", _district_number(d), special)
    if o == "GOVERNOR":
        return RaceKey("CT", Level.GOVERNOR, "GOV", None, special)
    if o in ("LIEUTENANT GOVERNOR", "LT. GOVERNOR"):
        return RaceKey("CT", Level.GOVERNOR, "LTGOV", None, special)
    if o == "ATTORNEY GENERAL":
        return RaceKey("CT", Level.GOVERNOR, "AG", None, special)
    if o == "SECRETARY OF STATE":
        return RaceKey("CT", Level.GOVERNOR, "SOS", None, special)
    if o in ("TREASURER", "STATE TREASURER"):
        return RaceKey("CT", Level.GOVERNOR, "TREAS", None, special)
    if o in ("COMPTROLLER", "STATE COMPTROLLER"):
        return RaceKey("CT", Level.GOVERNOR, "COMP", None, special)
    if o == "STATE SENATOR":
        return RaceKey("CT", Level.STATE, "SD", _district_number(d), special)
    if o in ("STATE REPRESENTATIVE", "REPRESENTATIVE"):
        return RaceKey("CT", Level.STATE, "HD", _district_number(d), special)
    m = re.match(r"^CONSTITUTIONAL\s+AMENDMENT\s+(\d+)", o)
    if m:
        return c.local_key("CT", f"constitutional-amendment-{int(m.group(1))}")
    return c.local_key("CT", f"{office} {district}".strip() if district else office)


def _search_json(year: int) -> str:
    return json.dumps({
        "global": {"years": {"from": year, "to": year}},
        "contests": {"candidates": [], "offices": [], "divisions": []},
        "voterStats": False,
        "stages": [],
        "specialElectionsOnly": False,
    })


def parse_ct(rows: list[dict], *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the Connecticut ElectionStats search CSV rows."""
    # Aggregate by contest + candidate across all divisions and vote channels.
    by_contest: dict[tuple[str, str], dict] = {}
    totals: dict[tuple[str, str, str, str, str, str], int] = defaultdict(int)
    for row in rows:
        cid = (row.get("contest_id") or "").strip()
        office = (row.get("office_name") or "").strip()
        district = (row.get("district_name") or "").strip()
        cand_id = (row.get("candidate_id") or "").strip()
        cand_name = (row.get("candidate_name") or "").strip()
        if not cid or not office or not cand_name:
            continue
        if cand_name.lower() in ("total votes cast", "total ballots cast"):
            continue
        key = (cid, office, district)
        by_contest.setdefault(key, {"office": office, "district": district, "date": row.get("election_date")})
        totals[(cid, office, district, cand_id, cand_name, row.get("candidate_party_name") or "")] += c.to_int(row.get("votes"))

    out: list[RaceResult] = []
    seen: set[str] = set()
    for (cid, office, district), info in by_contest.items():
        title = f"{office} {district}".strip() if district else office
        key = race_key(office, district)
        if str(key) in seen:
            continue
        cands = []
        updated: datetime | None = None
        for (r_cid, r_office, r_district, cand_id, cand_name, party), votes in totals.items():
            if r_cid != cid or r_office != office or r_district != district:
                continue
            if not cand_name:
                continue
            cands.append(CandidateResult(
                name=cand_name,
                party=c.norm_party(party),
                votes=votes,
            ))
            if updated is None:
                updated = c.parse_updated_at(info.get("date"))
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=CtSots.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            source_updated_at=updated,
        ))
    return out


async def _search_csv(client: httpx.AsyncClient, date: str) -> str:
    year = int(date.split("-")[0])
    url = f"{BASE}/api/download_search.csv?search={_search_json(year)}"
    try:
        return await c.get_text(client, url)
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"CT: search CSV failed ({exc})") from exc


@register
class CtSots(ResultsSource):
    name = "ct_sots"
    states = frozenset({"CT"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ct_keys = [k for k in keys if k.state == "CT"]
        if not ct_keys:
            return []
        text = await _search_csv(client, c.election_date())
        rows = list(csv.DictReader(text.splitlines()))
        return c.select(parse_ct(rows, fetched_at=c.utcnow()), ct_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "CT":
            return []
        text = await _search_csv(client, c.election_date())
        rows = list(csv.DictReader(text.splitlines()))
        return [
            (r.key, r.title or str(r.key))
            for r in parse_ct(rows, fetched_at=c.utcnow())
        ]
