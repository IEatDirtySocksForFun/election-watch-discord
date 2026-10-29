"""Harris County, Texas — the county's own election-night JSON (HarrisVotes).

Harris County (Houston; 13.7% of Texas's 2024 US Senate vote) does not use the Clarity CDN.
Its results page, https://www.harrisvotes.com/Election-Results/Live-Results, embeds
https://app.harrisvotes.com/electionresults/electionday, an AngularJS page that reads static
JSON files from the county's own file host:

    https://appfiles.harrisvotes.com/harrisvotes/prd/Data/<code>/resultsbyballot.json
    https://appfiles.harrisvotes.com/harrisvotes/prd/Data/<code>/pr.json         precinct list
    https://appfiles.harrisvotes.com/harrisvotes/prd/Data/<code>/pv-<id>.json    per precinct

``<code>`` is the election's month + two-digit year: ``1124`` = November 2024, ``1126`` =
November 2026 (the same code names the county's PDF reports, ``Data/1126/cumulative.pdf``).

``resultsbyballot.json`` (85 KB for the 2024 general, 224 contests)::

    {"et": "General and Special Elections", "ec": "1124", "edt": "November 05, 2024",
     "o": 1,                      # 1 = official (canvassed), 0 = unofficial
     "vc": 700, "vt": 700,        # vote centers reporting / total
     "dt": "2024-11-15T17:30:00Z",
     "contests": [{"i": 7282, "t": "United States Senator", "tp": 1172, "pr": 1172,
                   "ca": ["TED CRUZ (REP)", "COLIN ALLRED (DEM)", ...],
                   "tv": [666027, 841784, ...],          # totals per candidate
                   "ab": [...], "ev": [...], "ed": [...], # mail / early / election day
                   "pct": ["43.00%", ...]}, ...]}

Contest titles use the same wording as the Clarity counties ("United States
Representative, District 7"), so `tx_counties.race_key` maps those titles and the county merges into
the same partial statewide results. This module is pure; `tx_counties` does the fetching.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Callable

from electionwatch.models import CandidateResult, RaceKey, RaceResult

from . import _common as c

BASE = "https://appfiles.harrisvotes.com/harrisvotes/prd/Data"
HUMAN = "https://www.harrisvotes.com/Election-Results/Live-Results"

_CAND = re.compile(r"^(.*?)\s*\(([A-Za-z]{1,4})\)\s*$")


def election_code(iso_date: str) -> str:
    """'2026-11-03' -> '1126' (month + two-digit year, the county's own election code)."""
    election_day = date.fromisoformat(iso_date)
    return f"{election_day.month:02d}{election_day.year % 100:02d}"


def results_url(code: str) -> str:
    return f"{BASE}/{code}/resultsbyballot.json"


def payload_date(payload) -> str | None:
    """'November 05, 2024' -> '2024-11-05' (None when absent/unparseable)."""
    raw = str((payload or {}).get("edt") or "").strip() if isinstance(payload, dict) else ""
    try:
        return datetime.strptime(raw, "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def split_candidate(raw: str) -> tuple[str, str | None]:
    """'TED CRUZ (REP)' -> ('TED CRUZ', 'REP'); 'Analisa Roche (W)' -> (..., 'WRI')."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    match = _CAND.match(text)
    if not match:
        return text, None
    return match.group(1).strip(), c.norm_party(match.group(2))


def parse_updated(payload) -> datetime | None:
    raw = (payload or {}).get("dt") if isinstance(payload, dict) else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_contests(
    payload,
    *,
    key_fn: Callable[[str], RaceKey | None],
    source: str,
    fetched_at: datetime,
    url: str = HUMAN,
) -> list[RaceResult]:
    """Pure parse of resultsbyballot.json -> one RaceResult per contest."""
    if not isinstance(payload, dict):
        return []
    updated = parse_updated(payload)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for con in payload.get("contests") or []:
        if not isinstance(con, dict):
            continue
        title = re.sub(r"\s+", " ", str(con.get("t") or "")).strip()
        if not title:
            continue
        key = key_fn(title)
        if key is None or str(key) in seen:
            continue
        names = con.get("ca") or []
        totals = con.get("tv") or []
        cands = []
        for i, raw in enumerate(names):
            name, party = split_candidate(raw)
            if not name:
                continue
            cands.append(CandidateResult(name=name, party=party, votes=c.to_int(totals[i] if i < len(totals) else 0)))
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=source,
                url=url,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=c.to_int(con.get("pr")) or None,
                precincts_total=c.to_int(con.get("tp")) or None,
                source_updated_at=updated,
            )
        )
    return results
