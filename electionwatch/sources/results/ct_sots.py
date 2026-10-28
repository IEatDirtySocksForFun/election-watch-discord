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
from collections import defaultdict
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://ct.elstats.civera.com"
HUMAN = "https://electionhistory.ct.gov/search"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


def _district_number(text: str) -> str:
    match = re.search(r"\b(\d+)\b", text or "")
    return f"{int(match.group(1)):02d}" if match else "00"


def race_key(office: str, district: str | None = None) -> RaceKey:
    office_name = _norm(office)
    district_text = _norm(district or "")
    special = c.is_special(office_name) or c.is_special(district_text)
    if office_name == "PRESIDENT":
        return c.local_key("CT", "us-president")
    if office_name in ("UNITED STATES SENATOR", "U.S. SENATOR", "US SENATOR"):
        return RaceKey("CT", Level.SENATE, "SEN", None, special)
    if office_name in (
        "REPRESENTATIVE IN CONGRESS",
        "U.S. REPRESENTATIVE",
        "US REPRESENTATIVE",
        "UNITED STATES REPRESENTATIVE",
    ):
        return RaceKey("CT", Level.HOUSE, "HOUSE", _district_number(district_text), special)
    if office_name == "GOVERNOR":
        return RaceKey("CT", Level.GOVERNOR, "GOV", None, special)
    if office_name in ("LIEUTENANT GOVERNOR", "LT. GOVERNOR"):
        return RaceKey("CT", Level.GOVERNOR, "LTGOV", None, special)
    if office_name == "ATTORNEY GENERAL":
        return RaceKey("CT", Level.GOVERNOR, "AG", None, special)
    if office_name == "SECRETARY OF STATE":
        return RaceKey("CT", Level.GOVERNOR, "SOS", None, special)
    if office_name in ("TREASURER", "STATE TREASURER"):
        return RaceKey("CT", Level.GOVERNOR, "TREAS", None, special)
    if office_name in ("COMPTROLLER", "STATE COMPTROLLER"):
        return RaceKey("CT", Level.GOVERNOR, "COMP", None, special)
    if office_name == "STATE SENATOR":
        return RaceKey("CT", Level.STATE, "SD", _district_number(district_text), special)
    if office_name in ("STATE REPRESENTATIVE", "REPRESENTATIVE"):
        return RaceKey("CT", Level.STATE, "HD", _district_number(district_text), special)
    match = re.match(r"^CONSTITUTIONAL\s+AMENDMENT\s+(\d+)", office_name)
    if match:
        return c.local_key("CT", f"constitutional-amendment-{int(match.group(1))}")
    return c.local_key("CT", f"{office} {district}".strip() if district else office)


def _search_json(year: int) -> str:
    return json.dumps(
        {
            "global": {"years": {"from": year, "to": year}},
            "contests": {"candidates": [], "offices": [], "divisions": []},
            "voterStats": False,
            "stages": [],
            "specialElectionsOnly": False,
        }
    )


def parse_ct(rows: list[dict], *, fetched_at: datetime, date: str | None = None) -> list[RaceResult]:
    """Pure parse of the Connecticut ElectionStats search CSV rows.

    The search covers the whole year (August primaries included): pass `date` to keep only
    that election's rows, otherwise whichever contest comes first claims a key like CT GOV.
    """
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
        if date and not (row.get("election_date") or "").startswith(date):
            continue
        if cand_name.lower() in ("total votes cast", "total ballots cast"):
            continue
        key = (cid, office, district)
        by_contest.setdefault(key, {"office": office, "district": district, "date": row.get("election_date")})
        totals[(cid, office, district, cand_id, cand_name, row.get("candidate_party_name") or "")] += c.to_int(
            row.get("votes")
        )

    results: list[RaceResult] = []
    seen: set[str] = set()
    for (cid, office, district), contest_info in by_contest.items():
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
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=c.norm_party(party),
                    votes=votes,
                )
            )
            if updated is None:
                updated = c.parse_updated_at(contest_info.get("date"))
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=CtSots.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                source_updated_at=updated,
            )
        )
    return results


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
        ct_keys = [key for key in keys if key.state == "CT"]
        if not ct_keys:
            return []
        text = await _search_csv(client, c.election_date())
        rows = list(csv.DictReader(text.splitlines()))
        return c.select(parse_ct(rows, fetched_at=c.utcnow(), date=c.election_date()), ct_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "CT":
            return []
        text = await _search_csv(client, c.election_date())
        rows = list(csv.DictReader(text.splitlines()))
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in parse_ct(rows, fetched_at=c.utcnow(), date=c.election_date())
        ]
