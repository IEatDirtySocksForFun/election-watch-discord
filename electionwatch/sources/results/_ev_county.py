"""Shared parser for Enhanced Voting county exports.

Enhanced Voting (formerly Scytl) hosts the election-night results of several counties on a
CDN, in the same JSON shape as the Georgia Secretary of State export:

    https://app.enhancedvoting.com/cdn/results/<jurisdiction>/export-<slug>.json

e.g. ``shelby-county-tn/export-General11052024.json`` (2.2 MB for the 2024 general).
Shape (trimmed in ``tests/fixtures/tn_counties/``)::

    {"electionDate": "2024-11-05T00:00:00", "electionName": "State General",
     "results": {"id": "...", "name": "...", "ballotItems": [
        {"type": "Local", "id": "1", "name": "United States Senate",
         "contestType": "Candidate", "precinctsParticipating": 142,
         "precinctsReporting": 142, "voteFor": 1,
         "ballotOptions": [
            {"name": "REP Marsha Blackburn", "politicalParty": "Republican",
             "voteCount": 120568, "groupResults": [...], "precinctResults": [...]}]}]},
     "localResults": [...]}

Two quirks, both handled here:

* Candidate names repeat the party that also arrives as ``politicalParty`` ("REP Marsha
  Blackburn"), so the prefix is stripped when it matches the party column.
* The party column carries full names ("Republican", "Democratic", "Nonpartisan"), which
  ``_common.norm_party`` folds to the usual codes.

The precinct-level breakdown (``precinctResults``) and ``localResults`` are not parsed; the
bot reports county totals only.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from electionwatch.models import CandidateResult, RaceKey, RaceResult

from . import _common as c
from ._county import split_party_prefix

BASE = "https://app.enhancedvoting.com/cdn/results"
HUMAN = "https://app.enhancedvoting.com/results/public"

#: Georgia-style party suffix in a candidate name ("Jane Doe (Dem)").
_PARTY_SUFFIX = re.compile(
    r"\s*\((?:REP|DEM|LIB|GRN|GRE|IND|CON|CST|GOP|NPA|REF|WRI|WRITE-?IN)\)\s*$", re.I)

#: County municipal races are nonpartisan; the vendor writes the word out.
_PARTY_ALIASES = {"NONPARTISAN": "NPA"}


def party(value: str | None) -> str | None:
    code = c.norm_party(value)
    return _PARTY_ALIASES.get(code, code) if code else None


def clean_title(name: str) -> str:
    """Contest name in English: drop a bilingual suffix and collapse whitespace."""
    return re.sub(r"\s+", " ", str(name or "").split("/")[0]).strip()


def clean_name(raw: str) -> tuple[str, bool]:
    """'REP Jane Doe (I)' -> ('REP Jane Doe', True); the party suffix and the
    incumbent marker can appear in either order, so strip both twice."""
    name = str(raw or "").strip()
    strike = False
    for _ in range(2):
        stripped = _PARTY_SUFFIX.sub("", name).strip()
        stripped, incumbent = c.strip_incumbent(stripped)
        strike = strike or incumbent
        if stripped == name:
            break
        name = stripped
    return name, strike


def export_url(jurisdiction: str, slug: str) -> str:
    return f"{BASE}/{jurisdiction}/export-{slug}.json"


def parse_items(
    payload,
    *,
    key_fn: Callable[[str], RaceKey],
    source: str,
    url: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of one Enhanced Voting export document.

    ``key_fn`` maps the contest name to a ``RaceKey``; every other field is vendor-generic.
    """
    if not isinstance(payload, dict):
        return []
    items = (payload.get("results") or {}).get("ballotItems") or []
    out: list[RaceResult] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = clean_title(item.get("name"))
        if not title:
            continue
        key = key_fn(title)
        if str(key) in seen:      # the same office can appear per district
            continue
        cands: list[CandidateResult] = []
        for option in item.get("ballotOptions") or []:
            if not isinstance(option, dict):
                continue
            party_code = party(option.get("politicalParty"))
            name, incumbent = clean_name(option.get("name"))
            name = split_party_prefix(name, party_code)
            if not name:
                continue
            cands.append(CandidateResult(
                name=name,
                party=party_code,
                votes=c.to_int(option.get("voteCount")),
                incumbent=incumbent,
                winner=False,     # the export publishes no winner flag
            ))
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=source,
            url=url,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=c.to_int(item.get("precinctsReporting")) or None,
            precincts_total=c.to_int(item.get("precinctsParticipating")) or None,
        ))
    return out
