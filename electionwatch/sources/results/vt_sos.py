"""Vermont — Secretary of State election results (static.electionresults.vermont.gov).

The Vermont results site is a static JSON app. An index lists every election, and
each election has a metadata document whose `path` fields point at one file per
contest group:

    https://static.electionresults.vermont.gov/elections/elections.json
    https://static.electionresults.vermont.gov/elections/<electionGuid>.json
    https://static.electionresults.vermont.gov/elections/<guid>-f-<stamp>.json   federal
    https://static.electionresults.vermont.gov/elections/<guid>-s-<stamp>.json   statewide
    https://static.electionresults.vermont.gov/elections/<guid>-sn-<stamp>.json  state senate
    https://static.electionresults.vermont.gov/elections/<guid>-h-<stamp>.json   state house

2024 general: guid 67a35c79-05a2-4ae3-a77d-97812256188b (verified fixture).
2026 general: guid 68d61e96-1d28-4210-8c1d-8e90512ae105 — already listed in the
index on 2026-10-27 (all-zero results, election not held yet), so the adapter
discovers the guid by date instead of pinning it.

Each political document is `{"d": [{"o": [contest, ...]}]}` where a contest is:

    {"on": "STATE SENATOR", "dc": "ADD 1", "dn": "ADDISON_",
     "cs": [{"tn": "ORWELL", "distReported": "1/1",
             "rc": [{"cid": 12, "cn": "RUTH HARDY", "vc": 257, "isWriteIn": false,
                     "isWinner": true, "pn": "REPUBLICAN"}],
             "wc": [{"cn": "RICHARD NELSON", "vc": 1, "isWriteIn": true}]}]}

Votes are only available per town (`cs` rows), so the parser sums `rc` by candidate
id across every row. `wc` write-ins are lumped into a single "Write-In" candidate.
Statewide races have no `dc`; legislative districts are named ("ADD 1",
"ADD RUT", "CHI SE 1"), so the RaceKey district is the slugified district code
(VT HD-add-1, VT SD-add-1). Vermont has a single at-large U.S. House seat, keyed
VT-AL. Precinct counts are not published (reporting units are towns), so they are
left None; winners are only those the feed flags with isWinner.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://static.electionresults.vermont.gov/elections"
HUMAN = "https://electionresults.vermont.gov/"

#: date -> election guid.  None = discover by date from elections.json.
ELECTIONS: dict[str, str | None] = {
    "2024-11-05": "67a35c79-05a2-4ae3-a77d-97812256188b",
    "2026-11-03": None,
}

_SECTIONS = ("federal", "stateWide", "senate", "house")

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "AUDITOR OF ACCOUNTS": "AUD",
    "ATTORNEY GENERAL": "AG",
}

#: Vermont party labels that are not plain names; first component wins ("DEM/PROG").
_PARTIES = {
    "DEM/PROG": "DEM",
    "REP/LBRT": "REP",
    "PROGRESSIVE": "PROG",
    "PEACE AND JUSTICE": "PJ",
    "WE THE PEOPLE": "WTP",
    "LIBERTY UNION": "LU",
    "GREEN": "GRN",
    "LIBERTARIAN": "LIB",
    "CONSTITUTION": "CON",
    "INDEPENDENT": "IND",
    "REPUBLICAN": "REP",
    "DEMOCRATIC": "DEM",
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().upper()


def _party(value: str | None) -> str | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip().upper()
    if not raw:
        return None
    if raw in _PARTIES:
        return _PARTIES[raw]
    first = raw.split("/")[0]
    return _PARTIES.get(first) or c.norm_party(first)


def race_key(office: str, district_code: str | None = None) -> RaceKey:
    """Map a Vermont office (+ named district code) to a RaceKey."""
    n = _norm(office)
    if n in ("US PRESIDENT AND VICE PRESIDENT", "PRESIDENT AND VICE PRESIDENT",
             "PRESIDENT OF THE UNITED STATES"):
        return c.local_key("VT", "us-president")
    if n in ("US SENATOR", "UNITED STATES SENATOR"):
        return RaceKey("VT", Level.SENATE, "SEN")
    if n in ("REPRESENTATIVE TO CONGRESS", "US REPRESENTATIVE", "US HOUSE"):
        # Vermont has one at-large seat.
        return RaceKey("VT", Level.HOUSE, "HOUSE", "AL")
    if n in _STATEWIDE:
        return RaceKey("VT", Level.GOVERNOR, _STATEWIDE[n])
    if n == "STATE SENATOR" and district_code:
        return RaceKey("VT", Level.STATE, "SD", c.slug(district_code))
    if n == "STATE REPRESENTATIVE" and district_code:
        return RaceKey("VT", Level.STATE, "HD", c.slug(district_code))
    return c.local_key("VT", n + (f" {district_code}" if district_code else ""))


def _contests(doc) -> list[dict]:
    if not isinstance(doc, dict):
        return []
    rows = doc.get("d")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.extend(row.get("o") or [])
    return out


def parse_vt(docs, *, fetched_at: datetime,
             source_updated_at: datetime | None = None) -> list[RaceResult]:
    """Pure parse of the federal/statewide/senate/house documents (any order)."""
    if isinstance(docs, dict):
        docs = list(docs.values())
    if not isinstance(docs, (list, tuple)):
        return []

    out: list[RaceResult] = []
    seen: set[str] = set()
    for doc in docs:
        for contest in _contests(doc):
            office = _norm(contest.get("on"))
            if not office:
                continue
            district = contest.get("dc")
            key = race_key(office, district)
            if str(key) in seen:
                continue

            totals: dict[int, dict] = {}
            write_ins = 0
            for row in contest.get("cs") or []:
                for choice in row.get("rc") or []:
                    cid = choice.get("cid")
                    if cid is None or choice.get("isWriteIn"):
                        write_ins += c.to_int(choice.get("vc"))
                        continue
                    entry = totals.setdefault(cid, {
                        "name": str(choice.get("cn") or "").strip(),
                        "party": _party(choice.get("pn")),
                        "votes": 0,
                        "winner": False,
                    })
                    entry["votes"] += c.to_int(choice.get("vc"))
                    entry["winner"] = entry["winner"] or choice.get("isWinner") is True
                for choice in row.get("wc") or []:
                    write_ins += c.to_int(choice.get("vc"))

            cands = [CandidateResult(
                name=re.sub(r"\s+", " ", entry["name"]).strip(),
                party=entry["party"],
                votes=entry["votes"],
                # Only the source declares winners: ranking a partial count would "call" races.
                winner=entry["winner"],
            ) for entry in totals.values() if entry["name"]]
            if write_ins:
                cands.append(CandidateResult(name="Write-In", party=None, votes=write_ins))
            if not cands:
                continue

            title = office
            if district:
                name = re.sub(r"[_\s]+$", "", str(contest.get("dn") or "")).strip()
                title = f"{office} - {name or district}"

            seen.add(str(key))
            out.append(c.make_result(
                key, title, cands, fetched_at,
                source=VtSos.name,
                url=HUMAN,
                total_votes=sum(x.votes for x in cands),
                source_updated_at=source_updated_at,
            ))
    return out


def _entry_by_date(index, date: str) -> dict | None:
    """Find the statewide general-election entry for `date` in elections.json."""
    if not isinstance(index, list):
        return None
    fallback = None
    for entry in index:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("electionDate") or "")[:10] != date:
            continue
        if entry.get("isGeneralElection") and entry.get("isStateWideElection"):
            return entry
        fallback = fallback or entry
    return fallback


async def resolve_guid(client: httpx.AsyncClient, date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    try:
        index = await c.get_json(client, f"{BASE}/elections.json")
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"vt_sos: election index unavailable ({exc})") from exc
    entry = _entry_by_date(index, date)
    if not entry or not entry.get("electionGuid"):
        raise SourceUnavailable(
            f"vt_sos: no {date} general election in {BASE}/elections.json")
    return entry["electionGuid"]


def _data_url(meta: dict, section: str) -> str | None:
    block = meta.get(section) or {}
    if not block.get("isEnable") or not block.get("path"):
        return None
    # Paths arrive as Windows-style 'elections\\<file>.json'.
    name = str(block["path"]).replace("\\", "/").split("/")[-1]
    return f"{BASE}/{name}"


async def fetch_docs(client: httpx.AsyncClient, guid: str):
    meta = await c.get_json(client, f"{BASE}/{guid}.json")
    if not isinstance(meta, dict):
        raise SourceUnavailable("vt_sos: election metadata is not an object")
    docs = []
    for section in _SECTIONS:
        url = _data_url(meta, section)
        if not url:
            continue
        try:
            docs.append(await c.get_json(client, url))
        except SourceUnavailable as exc:
            raise SourceUnavailable(f"vt_sos: {section} results unreadable ({exc})") from exc
    updated = c.parse_updated_at(meta.get("lastUpdatedDate"), tz="America/New_York")
    return docs, updated


@register
class VtSos(ResultsSource):
    name = "vt_sos"
    states = frozenset({"VT"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        vt_keys = [k for k in keys if k.state == "VT"]
        if not vt_keys:
            return []
        guid = await resolve_guid(client, c.election_date())
        docs, updated = await fetch_docs(client, guid)
        parsed = parse_vt(docs, fetched_at=c.utcnow(), source_updated_at=updated)
        return c.select(parsed, vt_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "VT":
            return []
        try:
            guid = await resolve_guid(client, c.election_date())
            docs, updated = await fetch_docs(client, guid)
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key))
                for r in parse_vt(docs, fetched_at=c.utcnow(), source_updated_at=updated)]
