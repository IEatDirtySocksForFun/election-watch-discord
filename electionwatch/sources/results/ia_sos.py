"""Iowa — Secretary of State election results (Clarity ENR).

Iowa's official results site (electionresults.iowa.gov) is a Clarity/Scytl ENR feed, so
the mechanics mirror `clarity.py`:

    https://results.enr.clarityelections.com/IA/elections.json        all published elections
    https://results.enr.clarityelections.com/IA/<EID>/current_ver.txt -> "382192"
    .../IA/<EID>/<version>/json/en/summary.json                       contest results
    .../IA/<EID>/<version>/json/en/electionsettings.json              "websiteupdatedat"

A state module rather than a `clarity` ELECTIONS entry, because:

* Iowa's contest names ("United States Representative District 1", "State Senator
  District 2", "State Representative District 1") miss the vendor adapter's generic
  patterns and would otherwise be filed as local races.
* The 2026 general's election id is not published yet, so it is discovered from
  IA/elections.json by date; ELECTIONS only pins verified ids.

Judges, school/city public measures and constitutional amendments become stable LOCAL
slugs. `clarity` still lists IA in its `states` set (a stale placeholder from before this
module existed, kept because that file is append-only); with no IA feed configured there
it raises SourceUnavailable, which the poller logs and skips.
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
STATE = "IA"
HUMAN = f"{BASE}/{STATE}/"

ELECTIONS: dict[str, str] = {
    # date -> Clarity election id (missing = discover from elections.json).
    "2024-11-05": "122322",
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "AUDITOR OF STATE": "AUD",
    "STATE TREASURER": "TREAS",
    "TREASURER OF STATE": "TREAS",
    "SECRETARY OF AGRICULTURE": "AGCOM",
    "ATTORNEY GENERAL": "AG",
}

_SENATE = re.compile(r"^UNITED STATES SENATOR$")
_HOUSE = re.compile(r"^UNITED STATES REPRESENTATIVE\b")
_STATE_SENATE = re.compile(r"^STATE SENATOR\b")
_STATE_HOUSE = re.compile(r"^STATE REPRESENTATIVE\b")

_PRIMARY_SUFFIX = re.compile(r"\s*-\s*(?:REP|DEM|LIB|GRE|GRN|CON|CST|IND|NPA|NP)\s*$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str) -> RaceKey:
    office_name = _norm(name)
    special = c.is_special(office_name)
    # "State Senator District 30" -> office/district only, term wording dropped.
    base = re.sub(r"\s*(?:-|,)?\s*DISTRICT\s*\d+$", "", office_name).strip()
    if _SENATE.match(base):
        return RaceKey(STATE, Level.SENATE, "SEN", None, special)
    if base in _STATEWIDE:
        return RaceKey(STATE, Level.GOVERNOR, _STATEWIDE[base], None, special)
    if _HOUSE.match(office_name):
        return RaceKey(STATE, Level.HOUSE, "HOUSE", c.district_from(office_name) or "AL", special)
    if _STATE_SENATE.match(office_name):
        return RaceKey(STATE, Level.STATE, "SD", c.district_from(office_name) or "00", special)
    if _STATE_HOUSE.match(office_name):
        return RaceKey(STATE, Level.STATE, "HD", c.district_from(office_name) or "00", special)
    return c.local_key(STATE, office_name)


def parse_ia(summary, settings=None, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Iowa Clarity summary.json (+ optional electionsettings.json)."""
    contests = (
        summary
        if isinstance(summary, list)
        else ((summary or {}).get("Contests") or (summary or {}).get("contests") or [])
    )
    updated = parse_settings(settings)
    results: list[RaceResult] = []
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
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=c.norm_party(parties[i] if i < len(parties) else None),
                    votes=c.to_int(votes[i] if i < len(votes) else 0),
                    pct=c.to_pct(pcts[i] if i < len(pcts) else None),
                    incumbent=incumbent,
                    winner=c.to_int(winners[i] if i < len(winners) else 0) == 1,
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        total = con.get("T")
        if isinstance(total, list):
            total = total[0] if total else None
        results.append(
            c.make_result(
                key,
                name,
                cands,
                fetched_at,
                source=IaSos.name,
                url=HUMAN,
                total_votes=c.to_int(total) or sum(candidate.votes for candidate in cands),
                precincts_reporting=c.to_int(con.get("PR")) or None,
                precincts_total=c.to_int(con.get("TP")) or None,
                source_updated_at=updated,
            )
        )
    return results


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
    raise SourceUnavailable(f"IA: IA/elections.json has no election dated {date}")


async def _feed(client: httpx.AsyncClient) -> list[RaceResult]:
    election_id = await _election_id(client, c.election_date())
    base = f"{BASE}/{STATE}/{election_id}"
    version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
    if not version or not re.fullmatch(r"[\w.\-]+", version):
        raise SourceUnavailable(f"IA: bad current_ver.txt from {base}")
    summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
    try:
        settings = await c.get_json(client, f"{base}/{version}/json/en/electionsettings.json")
    except SourceUnavailable:
        settings = None
    results = parse_ia(summary, settings, fetched_at=c.utcnow())
    for race_result in results:
        race_result.url = f"{HUMAN}{election_id}/"
    return results


@register
class IaSos(ResultsSource):
    name = "ia_sos"
    states = frozenset({"IA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = [key for key in keys if key.state == STATE]
        if not wanted:
            return []
        return c.select(await _feed(client), wanted)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != STATE:
            return []
        return [(race_result.key, race_result.title or str(race_result.key)) for race_result in await _feed(client)]
