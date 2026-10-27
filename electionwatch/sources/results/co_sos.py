"""Colorado — Secretary of State election results (Clarity ENR).

Colorado publishes through the Clarity/Scytl ENR vendor, so the mechanics are the same
as `clarity.py` — a per-election base URL and a data version that changes on each upload:

    https://results.enr.clarityelections.com/CO/elections.json        all published elections
    https://results.enr.clarityelections.com/CO/<EID>/current_ver.txt -> "382192"
    .../CO/<EID>/<version>/json/en/summary.json                       contest results
    .../CO/<EID>/<version>/json/en/electionsettings.json              "websiteupdatedat"

This module exists (instead of a `clarity` ELECTIONS entry) for two reasons:

* Colorado's contest names do not fit the vendor adapter's generic patterns — US House is
  "Representative to the 119th United States Congress - District 1" (no "Congressional
  District", no "House of Representatives"), so the vendor mapping would file it as a
  local race. `race_key()` here knows the Colorado phrasing.
* The 2026 general's election id is not published yet, so the id is discovered from
  CO/elections.json (matched on the election date) rather than hard-coded; ELECTIONS only
  pins the ids we have verified.

Everything not federal / statewide executive / state legislature — regents, State Board of
Education, district attorneys, judicial retention, amendments and propositions — becomes a
stable LOCAL slug.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from .clarity import parse_settings

BASE = "https://results.enr.clarityelections.com"
STATE = "CO"
HUMAN = f"{BASE}/{STATE}/"

ELECTIONS: dict[str, str] = {
    # date -> Clarity election id (missing = discover from elections.json).
    "2024-11-05": "122598",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "TREASURER": "TREAS",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
}

_HOUSE = re.compile(r"^REPRESENTATIVE TO THE \d+(?:ST|ND|RD|TH) UNITED STATES CONGRESS$")
_SENATE = re.compile(r"^STATE SENATOR\b")
_REP = re.compile(r"^STATE REPRESENTATIVE\b")

_PRIMARY_SUFFIX = re.compile(r"\s*-\s*(?:REP|DEM|LIB|GRE|GRN|CON|CST|IND|NPA|NP)\s*$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    base = re.sub(r"\s+-\s+(?:CONGRESSIONAL )?DISTRICT\s*\d+$", "", n).strip()
    if base in ("U.S. SENATE", "US SENATE", "U.S. SENATOR", "US SENATOR", "UNITED STATES SENATOR"):
        return RaceKey(STATE, Level.SENATE, "SEN", None, special)
    if base in _STATEWIDE:
        return RaceKey(STATE, Level.GOVERNOR, _STATEWIDE[base], None, special)
    if _HOUSE.match(base):
        return RaceKey(STATE, Level.HOUSE, "HOUSE", c.district_from(n) or "AL", special)
    if _SENATE.match(n):
        return RaceKey(STATE, Level.STATE, "SD", c.district_from(n) or "00", special)
    if _REP.match(n):
        return RaceKey(STATE, Level.STATE, "HD", c.district_from(n) or "00", special)
    return c.local_key(STATE, n)


def parse_co(summary, settings=None, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Colorado Clarity summary.json (+ optional electionsettings.json)."""
    contests = summary if isinstance(summary, list) else (
        (summary or {}).get("Contests") or (summary or {}).get("contests") or [])
    updated = parse_settings(settings)
    out: list[RaceResult] = []
    seen: set[str] = set()
    for con in contests:
        if not isinstance(con, dict):
            continue
        name = str(con.get("C") or "").strip()
        if not name or _PRIMARY_SUFFIX.search(name):
            continue
        key = race_key(name)
        if str(key) in seen:
            continue
        choices = con.get("CH") or []
        votes = con.get("V") or []
        parties = con.get("P") or []
        pcts = con.get("PCT") or []
        winners = con.get("W") or []
        cands = []
        for i, choice in enumerate(choices):
            cand_name, incumbent = c.strip_incumbent(str(choice or "").strip())
            if not cand_name or cand_name.upper() == "NO CANDIDATE FILED":
                continue
            cands.append(CandidateResult(
                name=cand_name,
                party=c.norm_party(parties[i] if i < len(parties) else None),
                votes=c.to_int(votes[i] if i < len(votes) else 0),
                pct=c.to_pct(pcts[i] if i < len(pcts) else None),
                incumbent=incumbent,
                winner=c.to_int(winners[i] if i < len(winners) else 0) == 1,
            ))
        if not cands:
            continue
        seen.add(str(key))
        total = con.get("T")
        if isinstance(total, list):
            total = total[0] if total else None
        out.append(c.make_result(
            key, name, cands, fetched_at,
            source=CoSos.name,
            url=HUMAN,
            total_votes=c.to_int(total) or sum(x.votes for x in cands),
            precincts_reporting=c.to_int(con.get("PR")) or None,
            precincts_total=c.to_int(con.get("TP")) or None,
            source_updated_at=updated,
        ))
    return out


async def _election_id(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    rows = await c.get_json(client, f"{BASE}/{STATE}/elections.json")
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        stamp = str(row.get("Date") or "").split()[0]
        try:
            if stamp and c.iso_date(stamp) == date and row.get("EID"):
                return str(row["EID"])
        except ValueError:
            continue
    raise SourceUnavailable(f"CO: CO/elections.json has no election dated {date}")


async def _feed(client: httpx.AsyncClient) -> list[RaceResult]:
    election_id = await _election_id(client, c.election_date())
    base = f"{BASE}/{STATE}/{election_id}"
    version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
    if not version or not re.fullmatch(r"[\w.\-]+", version):
        raise SourceUnavailable(f"CO: bad current_ver.txt from {base}")
    summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
    try:
        settings = await c.get_json(client, f"{base}/{version}/json/en/electionsettings.json")
    except SourceUnavailable:
        settings = None
    results = parse_co(summary, settings, fetched_at=c.utcnow())
    for result in results:
        result.url = f"{HUMAN}{election_id}/"
    return results


@register
class CoSos(ResultsSource):
    name = "co_sos"
    states = frozenset({"CO"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = [k for k in keys if k.state == STATE]
        if not wanted:
            return []
        return c.select(await _feed(client), wanted)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != STATE:
            return []
        return [(r.key, r.title or str(r.key)) for r in await _feed(client)]
