"""North Carolina — State Board of Elections ENR (er.ncsbe.gov).

The ENR site is file-based, no auth and no JS required:

    https://er.ncsbe.gov/enr/elections.txt                          all elections (JSON array)
    https://er.ncsbe.gov/enr/<YYYYMMDD>/data/contest.txt            contest list
    https://er.ncsbe.gov/enr/<YYYYMMDD>/data/results_0.txt          statewide results
    https://er.ncsbe.gov/enr/<YYYYMMDD>/data/county.txt             turnout / last updated

The .txt files are a JSON array with one object per line (no outer brackets). Results
rows are one per candidate per contest:

    {"cid":"0","cnm":"NC GOVERNOR (VOTE FOR 1)","bnm":"Josh Stein","pty":"DEM",
     "vct":"3069496","pct":"0.5490","prt":"2658","ptl":"2658","ogl":"COS",...}

`pct` is a 0-1 fraction, `prt`/`ptl` are precincts reporting/total for that contest.
`county.txt` row cid "0" carries the statewide ballots cast, registered voters and the
"imp" timestamp, so turnout comes from there rather than from summed candidate votes.

Discovery: elections.txt lists "11/03/2026" first with arc "0" (not archived), so the
folder for the tracked date is found without a hard-coded election id.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://er.ncsbe.gov/enr"
HUMAN = "https://er.ncsbe.gov/"

ELECTIONS: dict[str, str | None] = {
    # date -> ENR folder (YYYYMMDD); None = discover from elections.txt.
    "2024-11-05": "20241105",
    "2026-03-03": "20260303",
    "2026-11-03": "20261103",
}

_STATEWIDE = {
    "NC GOVERNOR": "GOV",
    "NC LIEUTENANT GOVERNOR": "LTGOV",
    "NC ATTORNEY GENERAL": "AG",
    "NC AUDITOR": "AUD",
    "NC COMMISSIONER OF AGRICULTURE": "AGCOM",
    "NC COMMISSIONER OF INSURANCE": "INSCOM",
    "NC SECRETARY OF STATE": "SOS",
    "NC STATE TREASURER": "TREAS",
    "NC SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
}


def _bare(name: str) -> str:
    """'NC GOVERNOR (VOTE FOR 1)' -> 'NC GOVERNOR'."""
    return re.sub(r"\s*\(VOTE FOR \d+\)\s*$", "", (name or "").strip(), flags=re.I).upper()


def race_key(name: str) -> RaceKey:
    n = _bare(name)
    special = "UNEXPIRED" in n or "SPECIAL" in n
    n = re.sub(r"\s*\((?:UNEXPIRED|SPECIAL)\)\s*$", "", n).strip()
    if n in ("US SENATE", "US SENATOR", "UNITED STATES SENATE"):
        return RaceKey("NC", Level.SENATE, "SEN", None, special)
    m = re.match(r"^US HOUSE OF REPRESENTATIVES DISTRICT (\d+)$", n)
    if m:
        return RaceKey("NC", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    if n in _STATEWIDE:
        return RaceKey("NC", Level.GOVERNOR, _STATEWIDE[n], None, special)
    m = re.match(r"^NC STATE SENATE DISTRICT (\d+)$", n)
    if m:
        return RaceKey("NC", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^NC HOUSE OF REPRESENTATIVES DISTRICT (\d+)$", n)
    if m:
        return RaceKey("NC", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    return c.local_key("NC", n)


def _rows(text: str) -> list[dict]:
    """NC ships a JSON array without the brackets, one object per line."""
    raw = (text or "").strip()
    if not raw:
        return []
    if not raw.startswith("["):
        raw = "[" + raw.rstrip().rstrip(",") + "]"
    try:
        rows = json.loads(raw)
    except ValueError as exc:
        raise SourceUnavailable("NC: ENR data file is not the expected JSON array") from exc
    return [r for r in rows if isinstance(r, dict)]


def parse_nc(
    contest_rows: list[dict],
    result_rows: list[dict],
    county_rows: list[dict] | None = None,
    *,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of contest.txt + results_0.txt (+ county.txt for turnout)."""
    county_rows = county_rows or []
    statewide = next((r for r in county_rows if str(r.get("cid")) == "0"), None)
    registered = c.to_int((statewide or {}).get("rtl")) or None
    turnout = c.to_pct((statewide or {}).get("bpt"))
    updated = c.parse_updated_at((statewide or {}).get("imp"), tz="America/New_York")

    out: list[RaceResult] = []
    seen: set[str] = set()
    # contest.txt is the source of truth for which contests exist; results rows repeat the name.
    ordered = [_bare(r.get("cnm", "")) for r in contest_rows if str(r.get("cid")) == "0"]
    by_contest: dict[str, list[dict]] = {}
    for row in result_rows:
        if str(row.get("cid")) != "0":
            continue
        by_contest.setdefault(_bare(row.get("cnm", "")), []).append(row)

    names = list(dict.fromkeys([n for n in ordered if n])) + [n for n in by_contest if n and n not in ordered]
    for name in names:
        rows = by_contest.get(name)
        if not rows:
            continue
        key = race_key(name)
        if str(key) in seen:
            continue
        cands = []
        for row in rows:
            cand_name, incumbent = c.strip_incumbent(str(row.get("bnm") or "").strip())
            if not cand_name:
                continue  # "NO CANDIDATE FILED" placeholder rows
            cands.append(
                CandidateResult(
                    name=cand_name,
                    party=c.norm_party(row.get("pty")),
                    votes=c.to_int(row.get("vct")),
                    pct=c.to_pct(row.get("pct"), fraction=True),
                    incumbent=incumbent,
                    winner=False,  # NC publishes no winner flag in the ENR files
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        first = rows[0]
        out.append(
            c.make_result(
                key,
                name,
                cands,
                fetched_at,
                source=NcSbe.name,
                url=HUMAN,
                total_votes=sum(x.votes for x in cands),
                precincts_reporting=c.to_int(first.get("prt")) or None,
                precincts_total=c.to_int(first.get("ptl")) or None,
                registered_voters=registered,
                turnout_pct=turnout,
                source_updated_at=updated,
            )
        )
    return out


async def _folder(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    rows = _rows(await c.get_text(client, f"{BASE}/elections.txt"))
    target = c.us_date(date)
    match = next((r for r in rows if r.get("edt") == target), None)
    if not match:
        raise SourceUnavailable(f"NC: elections.txt has no election dated {target}")
    y, m, d = (int(x) for x in date.split("-"))
    return f"{y:04d}{m:02d}{d:02d}"


async def _data(client: httpx.AsyncClient, folder: str, name: str) -> list[dict]:
    return _rows(await c.get_text(client, f"{BASE}/{folder}/data/{name}"))


@register
class NcSbe(ResultsSource):
    name = "nc_sbe"
    states = frozenset({"NC"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "NC" for k in keys):
            return []
        folder = await _folder(client, c.election_date())
        contests = await _data(client, folder, "contest.txt")
        results = await _data(client, folder, "results_0.txt")
        try:
            county = await _data(client, folder, "county.txt")
        except SourceUnavailable:
            county = []
        parsed = parse_nc(contests, results, county, fetched_at=c.utcnow())
        return c.select(parsed, [k for k in keys if k.state == "NC"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "NC":
            return []
        folder = await _folder(client, c.election_date())
        contests = await _data(client, folder, "contest.txt")
        return [(race_key(r["cnm"]), _bare(r["cnm"])) for r in contests if str(r.get("cid")) == "0" and r.get("cnm")]
