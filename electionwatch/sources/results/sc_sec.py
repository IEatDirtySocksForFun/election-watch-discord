"""South Carolina — Election Commission ENR (Clarity/Scytl), white-labelled at enr-scvotes.org.

South Carolina runs the Clarity ENR platform under its own host, so the data files
are the standard Clarity versioned JSON — the same files also exist on the shared
CDN host:

    https://www.enr-scvotes.org/SC/                          (app, Clarity white-label)
    https://results.enr.clarityelections.com/SC/<id>/current_ver.txt
    https://results.enr.clarityelections.com/SC/<id>/<ver>/json/en/summary.json

2024 general: election id 122436, version 359624 (summary.json 212 contests).

This summary flavour differs from the `clarity` adapter's summary format: choices
are `CH` strings with a leading party code, and the party codes, vote counts,
percentages and winner flags live in parallel arrays:

    {"C": "U.S.  House of Representatives, District  1", "K": "20500",
     "P": ["REP", "DEM", "NON"],                # parallel to CH / V / PCT / W
     "CH": ["REP Nancy Mace", "DEM Michael B Moore", "Write-In"],
     "V": [227502, 162582, 1223], "PCT": [58.3, 41.4, 0.3],
     "W": [1, 0, 0], "T": 390777,               # contest total votes
     "TP": 6, "PR": 0}                          # precincts total / reporting

`NON` means no party (write-ins, school board, referenda); the "REP " prefix on a
choice name duplicates P[i] and is stripped from the display name. Contest names
use inconsistent double spaces, so whitespace is normalised before mapping.

2026: the state's elections.json index was empty on 2026-10-27, so the 2026
general election id is discovered from that index at runtime (no id pinned yet).
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from ._county import find_election, parse_elections

BASE = "https://results.enr.clarityelections.com/SC"
HUMAN = "https://www.enr-scvotes.org/SC/"

#: date -> Clarity election id.  None = discover from /SC/elections.json.
ELECTIONS: dict[str, str | None] = {
    "2024-11-05": "122436",   # verified fixture
    "2026-11-03": None,       # not published yet on 2026-10-27 (elections.json == [])
}

_NO_PARTY = {"NON", ""}

_HOUSE = re.compile(
    r"^U\.S\. HOUSE OF REPRESENTATIVES, DISTRICT (\d+)$", re.I)
_SENATE = re.compile(r"^STATE SENATE, DISTRICT (\d+)$", re.I)
_HD = re.compile(r"^STATE HOUSE OF REPRESENTATIVES, DISTRICT (\d+)$", re.I)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().upper()


_STATEWIDE = {
    "GOVERNOR": "GOV", "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV", "ATTORNEY GENERAL": "AG", "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS", "COMPTROLLER GENERAL": "COMP",
    "STATE SUPERINTENDENT OF EDUCATION": "SUPT", "COMMISSIONER OF AGRICULTURE": "AGCOM",
}


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    if n in _STATEWIDE:
        return RaceKey("SC", Level.GOVERNOR, _STATEWIDE[n])
    if n in ("PRESIDENT AND VICE PRESIDENT", "PRESIDENT OF THE UNITED STATES"):
        return c.local_key("SC", "us-president")
    if n in ("U.S. SENATE", "UNITED STATES SENATE"):
        return RaceKey("SC", Level.SENATE, "SEN")
    m = _HOUSE.match(n)
    if m:
        return RaceKey("SC", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    m = _SENATE.match(n)
    if m:
        return RaceKey("SC", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _HD.match(n)
    if m:
        return RaceKey("SC", Level.STATE, "HD", f"{int(m.group(1)):02d}")
    return c.local_key("SC", n)


def _choice(raw: str, party: str | None) -> tuple[str, bool]:
    """'REP Nancy Mace' -> ('Nancy Mace', ...); drops the duplicated party prefix."""
    name = str(raw or "").strip()
    if party and party not in _NO_PARTY:
        prefix = party + " "
        if name.upper().startswith(prefix.upper()):
            name = name[len(prefix):]
    return c.strip_incumbent(name)


def parse_sc(payload, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one SC Clarity summary.json document."""
    if not isinstance(payload, list):
        return []
    out: list[RaceResult] = []
    seen: set[str] = set()
    for contest in payload:
        title = re.sub(r"\s+", " ", str(contest.get("C") or "")).strip()
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        parties = contest.get("P") or []
        names = contest.get("CH") or []
        votes = contest.get("V") or []
        pcts = contest.get("PCT") or []
        winners = contest.get("W") or []
        cands = []
        for i, raw in enumerate(names):
            party = parties[i] if i < len(parties) else None
            party = None if (party or "").upper() in _NO_PARTY else party
            name, incumbent = _choice(raw, party)
            if not name:
                continue
            cands.append(CandidateResult(
                name=name,
                party=party,
                votes=c.to_int(votes[i] if i < len(votes) else None),
                pct=c.to_pct(pcts[i] if i < len(pcts) else None),
                incumbent=incumbent,
                winner=bool(winners[i]) if i < len(winners) else False,
            ))
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=ScSec.name,
            url=HUMAN,
            total_votes=c.to_int(contest.get("T")) or sum(x.votes for x in cands),
            precincts_reporting=c.to_int(contest.get("PR")) or None,
            precincts_total=c.to_int(contest.get("TP")) or None,
        ))
    return out


def summary_url(election_id: str, version: str) -> str:
    return f"{BASE}/{election_id}/{version}/json/en/summary.json"


def _match_index(payload, date: str) -> str | None:
    """Pick the election id for `date` from /SC/elections.json (shape varies)."""
    # Clarity's own index shape ({"EID", "Date": "11/3/2026 12:00:00 AM"}), as for the counties.
    found = find_election(parse_elections(payload), date)
    if found:
        return found
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        raw_date = str(entry.get("ElectionDate") or entry.get("date") or "")[:10]
        if raw_date == date or raw_date[5:] + "-" + raw_date[:4] == date:
            for key in ("ElectionId", "id", "Id"):
                if entry.get(key):
                    return str(entry[key])
    return None


async def resolve_election_id(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    try:
        index = await c.get_json(client, f"{BASE}/elections.json")
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"sc_sec: election index unavailable ({exc})") from exc
    found = _match_index(index, date)
    if not found:
        raise SourceUnavailable(
            f"sc_sec: no {date} entry in {BASE}/elections.json; South Carolina lists "
            f"the election id there when the ENR site opens")
    return found


async def fetch_summary(client: httpx.AsyncClient, election_id: str):
    version = (await c.get_text(client, f"{BASE}/{election_id}/current_ver.txt")).strip()
    if not version.isdigit():
        raise SourceUnavailable(f"sc_sec: unexpected version file {version[:40]!r}")
    return await c.get_json(client, summary_url(election_id, version))


@register
class ScSec(ResultsSource):
    name = "sc_sec"
    states = frozenset({"SC"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        sc_keys = [k for k in keys if k.state == "SC"]
        if not sc_keys:
            return []
        election_id = await resolve_election_id(client, c.election_date())
        try:
            payload = await fetch_summary(client, election_id)
        except SourceUnavailable as exc:
            raise SourceUnavailable(
                f"sc_sec: SC ENR summary for election {election_id} is not readable "
                f"({exc})") from exc
        return c.select(parse_sc(payload, fetched_at=c.utcnow()), sc_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "SC":
            return []
        try:
            election_id = await resolve_election_id(client, c.election_date())
            payload = await fetch_summary(client, election_id)
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key))
                for r in parse_sc(payload, fetched_at=c.utcnow())]
