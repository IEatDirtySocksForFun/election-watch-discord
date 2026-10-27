"""Rhode Island — Board of Elections results (ri.gov, JSON downloads).

RI.gov's results site publishes one JSON document per contest group on S3 (the
links on https://www.ri.gov/election/results/2024/general_election/data/):

    https://rigov.s3.amazonaws.com/election/results/<YYYY>/general_election/statewide.json
    https://rigov.s3.amazonaws.com/election/results/<YYYY>/general_election/ga_senator.json
    https://rigov.s3.amazonaws.com/election/results/<YYYY>/general_election/ga_representative.json
    (plus one <town>.json per municipality, not fetched)

2024 general verified on 2026-10-27. Shape:

    {"election_name": "General Election", "election_date": "November 05, 2024",
     "last_updated": "November 22, 2024 05:00 PM",
     "precinct_count": "453", "precincts_reporting": "453",
     "contests": [
       {"name": "Senator in Congress", "contest_number": "2",
        "precinct_count": "414", "precincts_reporting": "414",
        "total_votes": "491948", "votes_allowed": "1",
        "candidates": [{"name": "DEM Sheldon Whitehouse", "party_code": "DEM",
                        "votes": "294665"}, ...]}]}

`statewide.json` carries president, U.S. Senate, both U.S. House districts,
statewide offices and the ballot questions; the two general-assembly files carry
all 38 senate and 75 house districts. Candidate names in the general-assembly
files repeat the party code as a prefix ("DEM Jacob Bissaillon") while the
statewide file usually does not ("Kamala D. Harris"); the prefix is stripped
only when it matches party_code. Percentages are not published and are computed
from the vote totals.

The 2026 general-election JSON was not published on 2026-10-27 (S3 returned 403
for the key, the RI.gov page 404); the URL is date-derived, so nothing needs to
change when it appears.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://rigov.s3.amazonaws.com/election/results"
HUMAN = "https://www.ri.gov/election/results/"

FILES = ("statewide.json", "ga_senator.json", "ga_representative.json")

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "ATTORNEY GENERAL": "AG",
    "GENERAL TREASURER": "TREAS",
    "STATE TREASURER": "TREAS",
}

_CONGRESS = re.compile(r"^REPRESENTATIVE IN CONGRESS DISTRICT (\d+)$", re.I)
_GA_SEN = re.compile(r"^SENATOR IN GENERAL ASSEMBLY DISTRICT (\d+)$", re.I)
_GA_REP = re.compile(r"^REPRESENTATIVE IN GENERAL ASSEMBLY DISTRICT (\d+)$", re.I)
_QUESTION = re.compile(r"^(\d+)\.\s*(.+)$")

_PARTIES = {"KEN": "KEN", "GRN": "GRN", "NON": "NPA", "S&L": "S&L", "PAR": "PAR",
            "MOD": "MOD", "LIB": "LIB", "CON": "CON", "IND": "IND"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().upper()


def _party(value: str | None) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    return _PARTIES.get(raw) or c.norm_party(raw)


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    if n.startswith("PRESIDENTIAL ELECTORS"):
        return c.local_key("RI", "us-president")
    if n in ("SENATOR IN CONGRESS", "US SENATOR", "UNITED STATES SENATOR"):
        return RaceKey("RI", Level.SENATE, "SEN")
    m = _CONGRESS.match(n)
    if m:
        return RaceKey("RI", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    if n in _STATEWIDE:
        return RaceKey("RI", Level.GOVERNOR, _STATEWIDE[n])
    m = _GA_SEN.match(n)
    if m:
        return RaceKey("RI", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _GA_REP.match(n)
    if m:
        return RaceKey("RI", Level.STATE, "HD", f"{int(m.group(1)):02d}")
    m = _QUESTION.match(n)
    if m:
        return c.local_key("RI", f"ballot-question-{int(m.group(1))} {m.group(2)}")
    return c.local_key("RI", n)


def _clean_name(raw: str, party_code: str | None) -> str:
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    code = (party_code or "").strip()
    if code and name.upper().startswith(code.upper() + " "):
        name = name[len(code) + 1:].strip()
    return name


def parse_ri(docs, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the RI statewide + general-assembly documents."""
    if isinstance(docs, dict):
        docs = [docs]
    if not isinstance(docs, (list, tuple)):
        return []

    out: list[RaceResult] = []
    seen: set[str] = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        updated = c.parse_updated_at(doc.get("last_updated"), tz="America/New_York")
        for contest in doc.get("contests") or []:
            title = re.sub(r"\s+", " ", str(contest.get("name") or "")).strip()
            if not title:
                continue
            key = race_key(title)
            if str(key) in seen:
                continue
            cands = []
            for cand in contest.get("candidates") or []:
                name = _clean_name(cand.get("name"), cand.get("party_code"))
                if not name:
                    continue
                cands.append(CandidateResult(
                    name=name,
                    party=_party(cand.get("party_code")),
                    votes=c.to_int(cand.get("votes")),
                    winner=False,  # RI publishes no winner flag in these files
                ))
            if not cands:
                continue
            seen.add(str(key))
            out.append(c.make_result(
                key, title, cands, fetched_at,
                source=RiBoe.name,
                url=HUMAN,
                total_votes=c.to_int(contest.get("total_votes")) or sum(x.votes for x in cands),
                precincts_reporting=c.to_int(contest.get("precincts_reporting")) or None,
                precincts_total=c.to_int(contest.get("precinct_count")) or None,
                source_updated_at=updated,
            ))
    return out


def election_base(date: str) -> str:
    return f"{BASE}/{date[:4]}/general_election"


async def fetch_docs(client: httpx.AsyncClient, date: str) -> list[dict]:
    base = election_base(date)
    docs: list[dict] = []
    for name in FILES:
        try:
            doc = await c.get_json(client, f"{base}/{name}")
        except SourceUnavailable:
            if name == "statewide.json":
                raise SourceUnavailable(
                    f"ri_boe: {base}/statewide.json is not published yet; Rhode Island "
                    f"posts the general-election JSON on election night") from None
            continue  # general-assembly files appear later in the night
        docs.append(doc)
    if not docs:
        raise SourceUnavailable(f"ri_boe: no result documents under {base}")
    return docs


@register
class RiBoe(ResultsSource):
    name = "ri_boe"
    states = frozenset({"RI"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ri_keys = [k for k in keys if k.state == "RI"]
        if not ri_keys:
            return []
        docs = await fetch_docs(client, c.election_date())
        return c.select(parse_ri(docs, fetched_at=c.utcnow()), ri_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "RI":
            return []
        try:
            docs = await fetch_docs(client, c.election_date())
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key))
                for r in parse_ri(docs, fetched_at=c.utcnow())]
