"""Michigan — Clarity county election-night feeds, merged.

Michigan's statewide site is out of reach for an automated client: mielections.us
times out (and mvic.sos.state.mi.us answers 403 with a Cloudflare interstitial), and the
SOS's results/data page is behind the state WAF. The counties run their own reporting
systems though, and the four largest of them that use Clarity/Scytl publish the standard
vendor feeds:

    https://results.enr.clarityelections.com/MI/<County>/elections.json      election index
    https://results.enr.clarityelections.com/MI/<County>/<EID>/current_ver.txt
    .../<EID>/<version>/json/en/summary.json                                 contest results
    .../<EID>/<version>/json/en/electionsettings.json                        "websiteupdatedat"

``elections.json`` is the discovery hook: it lists every configured election for the
jurisdiction (EID, name, date), so the election id for the tracked date is found instead
of hard-coded. Pin ids with ``MI_CLARITY_FEEDS`` (JSON list of
``{"county": "Ottawa", "election_id": "127391", "date": "..."}``) when a county does not
list them or publishes under an unexpected date.

Each county only reports its own precincts, so results are a partial picture: every
contest is titled ``"<contest> (partial: N counties)"`` and links to the state results
page. Parsing is the shared Clarity parser; contest names are re-keyed to MI ones
("GOVERNOR AND LIEUTENANT GOVERNOR" -> GOV).

Wayne County (Detroit) runs a different ES&S "ResultsSW.aspx" app and is not included.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from .clarity import parse_clarity

BASE = "https://results.enr.clarityelections.com"
HUMAN = "https://www.michigan.gov/sos/elections/election-results-and-data"

#: Clarity jurisdictions in Michigan, biggest first. Verified present on the vendor host.
COUNTIES = ("Oakland", "Macomb", "Genesee", "Ottawa")

_GOV = ("GOVERNOR AND LIEUTENANT GOVERNOR", "GOVERNOR / LIEUTENANT GOVERNOR", "GOVERNOR")

#: Michigan Clarity contest names ("Representative in Congress 2nd District").
_CONGRESS = re.compile(
    r"^REPRESENTATIVE IN CONGRESS\s+(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$")
_STATE_SENATE = re.compile(r"^STATE SENATOR\s+(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$")
_STATE_HOUSE = re.compile(
    r"^(?:REPRESENTATIVE IN STATE LEGISLATURE|STATE REPRESENTATIVE)\s+"
    r"(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$")


def feeds(date: str) -> list[dict]:
    """Configured county feeds for an election date (MI_CLARITY_FEEDS overrides)."""
    raw = os.environ.get("MI_CLARITY_FEEDS")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise SourceUnavailable("MI_CLARITY_FEEDS is not valid JSON") from exc
        if not isinstance(entries, list):
            raise SourceUnavailable("MI_CLARITY_FEEDS must be a JSON list")
        out = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("county"):
                raise SourceUnavailable("MI_CLARITY_FEEDS entries need a county")
            if entry.get("date") and entry["date"] != date:
                continue
            out.append(entry)
        return out
    return [{"county": county} for county in COUNTIES]


def iso_date(value: str) -> str | None:
    """'11/3/2026 12:00:00 AM' -> '2026-11-03'."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if not m:
        return None
    return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def pick_election(rows, date: str) -> str | None:
    """The EID of the election on `date` from a jurisdiction's elections.json."""
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if iso_date(str(row.get("Date") or "")) == date and row.get("EID"):
            return str(row["EID"])
    return None


def race_key(name: str) -> RaceKey:
    """Clarity names re-keyed to Michigan ones."""
    n = re.sub(r"\s+", " ", (name or "").strip()).upper()
    special = c.is_special(n)
    if n in _GOV:
        return RaceKey("MI", Level.GOVERNOR, "GOV", None, special)
    if n in ("UNITED STATES SENATOR", "U.S. SENATOR", "US SENATOR"):
        return RaceKey("MI", Level.SENATE, "SEN", None, special)
    if n == "SECRETARY OF STATE":
        return RaceKey("MI", Level.GOVERNOR, "SOS", None, special)
    m = _CONGRESS.match(n)
    if m:
        return RaceKey("MI", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey("MI", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = _STATE_HOUSE.match(n)
    if m:
        return RaceKey("MI", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    from .clarity import race_key as clarity_race_key
    return clarity_race_key("MI", name)


def partial_title(title: str, counties: int) -> str:
    word = "county" if counties == 1 else "counties"
    return f"{title} (partial: {counties} {word})"


def merge_counties(
    by_county: dict[str, list[RaceResult]],
    *,
    fetched_at: datetime,
    source: str,
) -> list[RaceResult]:
    """Sum the per-county results into one partial statewide picture."""
    total_counties = len([n for n, rs in by_county.items() if rs])
    if not total_counties:
        return []
    merged: dict[str, dict] = {}
    for results in by_county.values():
        for r in results:
            entry = merged.setdefault(str(r.key), {
                "key": r.key, "title": r.title or str(r.key), "cands": {},
                "prt": 0, "ptl": 0, "updated": None,
            })
            for cand in r.candidates:
                slot = entry["cands"].setdefault((cand.name, cand.party), {
                    "votes": 0, "incumbent": cand.incumbent, "winner": cand.winner})
                slot["votes"] += cand.votes
                slot["winner"] = slot["winner"] or cand.winner
                slot["incumbent"] = slot["incumbent"] or cand.incumbent
            if r.precincts_reporting:
                entry["prt"] += r.precincts_reporting
            if r.precincts_total:
                entry["ptl"] += r.precincts_total
            if r.source_updated_at and (entry["updated"] is None
                                        or r.source_updated_at > entry["updated"]):
                entry["updated"] = r.source_updated_at

    out: list[RaceResult] = []
    for entry in merged.values():
        cands = [CandidateResult(name=name, party=party, votes=slot["votes"],
                                 incumbent=slot["incumbent"], winner=slot["winner"])
                 for (name, party), slot in entry["cands"].items()]
        out.append(c.make_result(
            entry["key"], partial_title(entry["title"], total_counties), cands, fetched_at,
            source=source,
            url=HUMAN,
            precincts_reporting=entry["prt"] or None,
            precincts_total=entry["ptl"] or None,
            source_updated_at=entry["updated"],
        ))
    return out


def rekey(results: list[RaceResult]) -> list[RaceResult]:
    """Re-key parsed Clarity results with Michigan contest names."""
    out: list[RaceResult] = []
    for r in results:
        key = race_key(r.title or "")
        out.append(RaceResult(
            key=key, candidates=r.candidates, source=r.source, fetched_at=r.fetched_at,
            source_updated_at=r.source_updated_at,
            precincts_reporting=r.precincts_reporting, precincts_total=r.precincts_total,
            expected_vote_pct=r.expected_vote_pct, total_votes=r.total_votes,
            registered_voters=r.registered_voters, turnout_pct=r.turnout_pct,
            url=r.url, title=r.title,
        ))
    return out


async def _election_id(client: httpx.AsyncClient, county: str, date: str) -> str | None:
    rows = await c.get_json(client, f"{BASE}/MI/{county}/elections.json")
    return pick_election(rows if isinstance(rows, list) else [], date)


async def _county(
    client: httpx.AsyncClient,
    county: str,
    date: str,
    pinned: str | None,
) -> list[RaceResult]:
    eid = pinned or await _election_id(client, county, date)
    if not eid:
        return []
    base = f"{BASE}/MI/{county}/{eid}"
    version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
    if not version or not re.fullmatch(r"[\w.\-]+", version):
        raise SourceUnavailable(f"MI/{county}: bad current_ver.txt")
    summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
    try:
        settings = await c.get_json(client, f"{base}/{version}/json/en/electionsettings.json")
    except SourceUnavailable:
        settings = None
    fetched_at = c.utcnow()
    return rekey(parse_clarity(summary, settings, state="MI", fetched_at=fetched_at))


@register
class MiCounty(ResultsSource):
    name = "mi_county"
    states = frozenset({"MI"})
    scrape = False

    async def _all(self, client: httpx.AsyncClient) -> list[RaceResult]:
        date = c.election_date()
        by_county: dict[str, list[RaceResult]] = {}
        errors: list[str] = []
        for entry in feeds(date):
            county = str(entry["county"])
            try:
                by_county[county] = await _county(client, county, date, entry.get("election_id"))
            except SourceUnavailable as exc:   # a county that has not published yet
                errors.append(f"{county}: {exc}")
                by_county[county] = []
        if not any(by_county.values()):
            raise SourceUnavailable(
                "mi_county: no county feed published for " + date + " (" + "; ".join(errors) + ")")
        return merge_counties(by_county, fetched_at=c.utcnow(), source=MiCounty.name)

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "MI" for k in keys):
            return []
        return c.select(await self._all(client), [k for k in keys if k.state == "MI"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MI":
            return []
        return [(r.key, r.title or str(r.key)) for r in await self._all(client)]
