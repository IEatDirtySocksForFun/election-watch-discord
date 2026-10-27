"""Helpers shared by the county-feed adapters (tx_counties, nj_counties, ...).

Some states block their statewide election-night site but the counties publish the same
Clarity ENR files on the vendor CDN. One adapter per state merges those counties:

    https://results.enr.clarityelections.com/<ST>/<County>/elections.json     election list
    https://results.enr.clarityelections.com/<ST>/<County>/<EID>/current_ver.txt   -> "355860"
    https://results.enr.clarityelections.com/<ST>/<County>/<EID>/<ver>/json/en/summary.json
    .../electionsettings.json                                               "websiteupdatedat"

`elections.json` is optional per jurisdiction (some counties publish an empty list), so
every county also accepts a pinned election id, and the whole set can be overridden from
the environment.

Everything here is pure except `fetch_summary`; the adapters own the county list and the
state-specific race-key mapping.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Callable

import httpx

from electionwatch.models import CandidateResult, RaceKey, RaceResult
from electionwatch.sources.base import SourceUnavailable

from . import _common as c

BASE = "https://results.enr.clarityelections.com"

#: Contest names a county publishes for candidates that did not file; they carry no votes.
#: "Rejected write-ins" (Hood County) is a tally of write-ins that were not counted, and
#: "Unassigned write-ins" is the vendor's zero-vote placeholder row.
_PLACEHOLDER = re.compile(
    r"^NO (?:CANDIDATE|NOMINATION)(?: FILED| MADE)?$|^(?:REJECTED|UNASSIGNED) WRITE-?INS?$",
    re.I)

#: Some counties repeat the party in the candidate name ("REP Ted Cruz") even though the
#: contest also carries a party column. Kept as-is, the same candidate would not merge with
#: "Ted Cruz" from the next county.
_PARTY_PREFIX = re.compile(
    r"^(REP|DEM|LIB|GRN|GRE|CON|CST|IND|NPA|NP|WRI|REF|FWD)\.?\s+(\S.*)$", re.I)


def split_party_prefix(name: str, party: str | None) -> str:
    """'REP Ted Cruz' with party REP -> 'Ted Cruz'. Only strips a prefix that repeats the
    contest's own party column, so a title like 'Rep. John Lewis' is left alone."""
    if not party:
        return name
    m = _PARTY_PREFIX.match(name or "")
    if m and c.norm_party(m.group(1)) == party:
        return m.group(2).strip()
    return name


#: Words that mean an elections.json entry is *not* the general election for the date.
_NOT_GENERAL = re.compile(
    r"PRIMARY|RUNOFF|SPECIAL|SCHOOL|MUNICIPAL|CITY|TOWN|RECALL|COUNCIL|CONVENTION", re.I)


def _iso_date(raw: str) -> str:
    """'11/3/2026 12:00:00 AM' -> '2026-11-03' (Clarity writes US dates)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    return m.group(0) if m else ""


def parse_elections(payload) -> list[dict]:
    """Pure parse of a jurisdiction's elections.json -> [{"eid", "name", "date"}]."""
    if isinstance(payload, dict):
        payload = payload.get("Elections") or payload.get("elections") or []
    out: list[dict] = []
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        eid = str(row.get("EID") or row.get("Eid") or row.get("eid") or "").strip()
        if not eid:
            continue
        out.append({
            "eid": eid,
            "name": str(row.get("ElectionName") or row.get("name") or "").strip(),
            "date": _iso_date(str(row.get("Date") or row.get("date") or "")),
        })
    return out


def _eid_order(entry: dict) -> int:
    try:
        return int(entry["eid"])
    except (KeyError, TypeError, ValueError):
        return 0


def find_election(entries: list[dict], date: str) -> str | None:
    """Election id for `date` from an elections.json listing, or None.

    Exact date first (the newest id wins when a county lists several uploads for the day).
    Counting sometimes files the general election under its certification date, so as a
    fallback accept a same-year entry named "general" that is not a primary/runoff/special.
    """
    exact = sorted((e for e in entries if e["date"] == date), key=_eid_order)
    if exact:
        return exact[-1]["eid"]
    year = date[:4]
    loose = sorted(
        (e for e in entries
         if e["date"].startswith(year)
         and "GENERAL" in e["name"].upper()
         and not _NOT_GENERAL.search(e["name"].upper().replace("GENERAL", ""))),
        key=_eid_order)
    return loose[-1]["eid"] if loose else None


def county_feeds_from_env(var: str) -> list[dict] | None:
    """Optional JSON override: '[{"county": "Dallas", "election_id": "127398"}]'."""
    raw = os.environ.get(var)
    if not raw:
        return None
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise SourceUnavailable(f"{var} is not valid JSON") from exc
    if not isinstance(entries, list):
        raise SourceUnavailable(f"{var} must be a JSON list")
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("county") or not entry.get("election_id"):
            raise SourceUnavailable(f"{var} entries need county and election_id")
        out.append(entry)
    return out


def parse_contests(
    summary,
    *,
    key_fn: Callable[[str], RaceKey | None],
    source: str,
    url: str | None,
    fetched_at: datetime,
    updated: datetime | None = None,
) -> list[RaceResult]:
    """Pure parse of one Clarity summary.json (list of contest objects)."""
    contests = summary if isinstance(summary, list) else (
        (summary or {}).get("Contests") or (summary or {}).get("contests") or [])
    out: list[RaceResult] = []
    seen: set[str] = set()
    for con in contests:
        if not isinstance(con, dict):
            continue
        name = str(con.get("C") or "").strip()
        if not name:
            continue
        key = key_fn(name)
        if key is None or str(key) in seen:
            continue
        choices = con.get("CH") or []
        votes = con.get("V") or []
        parties = con.get("P") or []
        pcts = con.get("PCT") or []
        cands = []
        for i, choice in enumerate(choices):
            cand_name, incumbent = c.strip_incumbent(str(choice or "").strip())
            party = c.norm_party(parties[i] if i < len(parties) else None)
            cand_name = split_party_prefix(cand_name, party)
            if not cand_name or _PLACEHOLDER.match(cand_name):
                continue
            cands.append(CandidateResult(
                name=cand_name,
                party=party,
                votes=c.to_int(votes[i] if i < len(votes) else 0),
                pct=c.to_pct(pcts[i] if i < len(pcts) else None),
                incumbent=incumbent,
            ))
        if not cands:
            continue
        seen.add(str(key))
        total = con.get("T")
        if isinstance(total, list):
            total = total[0] if total else None
        out.append(c.make_result(
            key, name, cands, fetched_at,
            source=source,
            url=url,
            total_votes=c.to_int(total) or sum(x.votes for x in cands),
            precincts_reporting=c.to_int(con.get("PR")) or None,
            precincts_total=c.to_int(con.get("TP")) or None,
            source_updated_at=updated,
        ))
    return out


def _cand_id(name: str) -> str:
    """County feeds differ in punctuation only ("HARRIS & WALZ" vs "HARRIS - WALZ")."""
    return re.sub(r"[^A-Z0-9]+", " ", (name or "").upper()).strip()


def merge(results: list[RaceResult], *, source: str, url: str | None) -> list[RaceResult]:
    """Sum the same race across counties into one partial result.

    Votes are added per (candidate name, party); percentages are recomputed from the merged
    totals. Precinct counts are county precincts, so summing gives the reporting picture for
    the counties that publish the race. `winner` survives only when every county agrees.
    Titles get the " (partial: N counties)" suffix the bot uses to flag partial coverage.
    """
    groups: dict[str, list[RaceResult]] = {}
    for r in results:
        groups.setdefault(str(r.key), []).append(r)

    out: list[RaceResult] = []
    for parts in groups.values():
        tally: dict[str, CandidateResult] = {}
        for part in parts:
            for cand in part.candidates:
                ident = _cand_id(cand.name)
                merged = tally.get(ident)
                if merged is None:
                    tally[ident] = CandidateResult(
                        name=cand.name.strip(), party=cand.party, votes=cand.votes,
                        pct=None, incumbent=cand.incumbent, winner=cand.winner)
                else:
                    merged.votes += cand.votes
                    # Counties disagree on whether a minor candidate has a party code.
                    merged.party = merged.party or cand.party
                    merged.winner = merged.winner and cand.winner
                    merged.incumbent = merged.incumbent or cand.incumbent
        cands = list(tally.values())
        if not cands:
            continue
        n = len(parts)
        base = next((p.title for p in parts if p.title), str(parts[0].key))
        title = f"{base} (partial: {n} counties)"
        stamps = [p.source_updated_at for p in parts if p.source_updated_at]
        pr = sum(p.precincts_reporting or 0 for p in parts) or None
        pt = sum(p.precincts_total or 0 for p in parts) or None
        out.append(c.make_result(
            parts[0].key, title, cands, parts[0].fetched_at,
            source=source,
            url=url,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=pr,
            precincts_total=pt,
            source_updated_at=max(stamps) if stamps else None,
        ))
    return out


async def election_id(
    client: httpx.AsyncClient,
    state: str,
    county: str,
    date: str,
    pinned: str | None = None,
) -> str | None:
    """Pinned id, else the county's elections.json; None when the county lists nothing."""
    if pinned:
        return str(pinned)
    url = f"{BASE}/{state}/{county}/elections.json"
    try:
        payload = await c.get_json(client, url)
    except SourceUnavailable:
        return None
    return find_election(parse_elections(payload), date)


async def fetch_summary(
    client: httpx.AsyncClient,
    state: str,
    county: str,
    eid: str,
) -> tuple[object, object | None, str]:
    """(summary, electionsettings, feed base) for one county feed; raises SourceUnavailable."""
    base = f"{BASE}/{state}/{county}/{eid}"
    version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
    if not version or not re.fullmatch(r"[\w.\-]+", version):
        raise SourceUnavailable(f"{state}/{county}/{eid}: bad current_ver.txt")
    summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
    try:
        settings = await c.get_json(client, f"{base}/{version}/json/en/electionsettings.json")
    except SourceUnavailable:
        settings = None
    return summary, settings, base
