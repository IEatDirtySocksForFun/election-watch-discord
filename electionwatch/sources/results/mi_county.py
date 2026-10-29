"""Michigan — Clarity county election-night feeds, merged.

Michigan's statewide site is out of reach for an automated client: mielections.us
times out (and mvic.sos.state.mi.us answers 403 with a Cloudflare interstitial), and the
SOS's results/data page is behind the state WAF. The counties run separate reporting
systems though, and the four largest counties that use Clarity/Scytl publish the standard
vendor feeds:

    https://results.enr.clarityelections.com/MI/<County>/elections.json      election index
    https://results.enr.clarityelections.com/MI/<County>/<EID>/current_ver.txt
    .../<EID>/<version>/json/en/summary.json                                 contest results
    .../<EID>/<version>/json/en/electionsettings.json                        "websiteupdatedat"

``elections.json`` is the discovery hook: it lists every configured election for the
jurisdiction (EID, name, date), so the election id for the tracked date is found instead
of hard-coded. Pin ids with ``MI_CLARITY_FEEDS`` (JSON list of
``{"county": "Ottawa", "election_id": "127391", "date": "..."}``) when a county does not
list an election or publishes under an unexpected date.

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
_CONGRESS = re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$")
_STATE_SENATE = re.compile(r"^STATE SENATOR\s+(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$")
_STATE_HOUSE = re.compile(
    r"^(?:REPRESENTATIVE IN STATE LEGISLATURE|STATE REPRESENTATIVE)\s+"
    r"(\d+)(?:ST|ND|RD|TH)?\s*DISTRICT$"
)


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
        configured = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("county"):
                raise SourceUnavailable("MI_CLARITY_FEEDS entries need a county")
            if entry.get("date") and entry["date"] != date:
                continue
            configured.append(entry)
        return configured
    return [{"county": county} for county in COUNTIES]


def iso_date(value: str) -> str | None:
    """'11/3/2026 12:00:00 AM' -> '2026-11-03'."""
    match = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", value or "")
    if not match:
        return None
    return f"{int(match.group(3)):04d}-{int(match.group(1)):02d}-{int(match.group(2)):02d}"


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
    contest_name = re.sub(r"\s+", " ", (name or "").strip()).upper()
    special = c.is_special(contest_name)
    if contest_name in _GOV:
        return RaceKey("MI", Level.GOVERNOR, "GOV", None, special)
    if contest_name in ("UNITED STATES SENATOR", "U.S. SENATOR", "US SENATOR"):
        return RaceKey("MI", Level.SENATE, "SEN", None, special)
    if contest_name == "SECRETARY OF STATE":
        return RaceKey("MI", Level.GOVERNOR, "SOS", None, special)
    match = _CONGRESS.match(contest_name)
    if match:
        return RaceKey("MI", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = _STATE_SENATE.match(contest_name)
    if match:
        return RaceKey("MI", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = _STATE_HOUSE.match(contest_name)
    if match:
        return RaceKey("MI", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
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
    total_counties = len([county for county, county_results in by_county.items() if county_results])
    if not total_counties:
        return []
    merged: dict[str, dict] = {}
    for county_results in by_county.values():
        for race_result in county_results:
            entry = merged.setdefault(
                str(race_result.key),
                {
                    "key": race_result.key,
                    "title": race_result.title or str(race_result.key),
                    "cands": {},
                    "prt": 0,
                    "ptl": 0,
                    "updated": None,
                },
            )
            for cand in race_result.candidates:
                slot = entry["cands"].setdefault(
                    (cand.name, cand.party), {"votes": 0, "incumbent": cand.incumbent, "winner": cand.winner}
                )
                slot["votes"] += cand.votes
                slot["winner"] = slot["winner"] or cand.winner
                slot["incumbent"] = slot["incumbent"] or cand.incumbent
            if race_result.precincts_reporting:
                entry["prt"] += race_result.precincts_reporting
            if race_result.precincts_total:
                entry["ptl"] += race_result.precincts_total
            if race_result.source_updated_at and (
                entry["updated"] is None or race_result.source_updated_at > entry["updated"]
            ):
                entry["updated"] = race_result.source_updated_at

    combined: list[RaceResult] = []
    for entry in merged.values():
        cands = [
            CandidateResult(
                name=name, party=party, votes=slot["votes"], incumbent=slot["incumbent"], winner=slot["winner"]
            )
            for (name, party), slot in entry["cands"].items()
        ]
        combined.append(
            c.make_result(
                entry["key"],
                partial_title(entry["title"], total_counties),
                cands,
                fetched_at,
                source=source,
                url=HUMAN,
                precincts_reporting=entry["prt"] or None,
                precincts_total=entry["ptl"] or None,
                source_updated_at=entry["updated"],
            )
        )
    return combined


def rekey(results: list[RaceResult]) -> list[RaceResult]:
    """Re-key parsed Clarity results with Michigan contest names."""
    rekeyed: list[RaceResult] = []
    for race_result in results:
        key = race_key(race_result.title or "")
        rekeyed.append(
            RaceResult(
                key=key,
                candidates=race_result.candidates,
                source=race_result.source,
                fetched_at=race_result.fetched_at,
                source_updated_at=race_result.source_updated_at,
                precincts_reporting=race_result.precincts_reporting,
                precincts_total=race_result.precincts_total,
                expected_vote_pct=race_result.expected_vote_pct,
                total_votes=race_result.total_votes,
                registered_voters=race_result.registered_voters,
                turnout_pct=race_result.turnout_pct,
                url=race_result.url,
                title=race_result.title,
            )
        )
    return rekeyed


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
            except SourceUnavailable as exc:  # a county that has not published yet
                errors.append(f"{county}: {exc}")
                by_county[county] = []
        if not any(by_county.values()):
            raise SourceUnavailable("mi_county: no county feed published for " + date + " (" + "; ".join(errors) + ")")
        return merge_counties(by_county, fetched_at=c.utcnow(), source=MiCounty.name)

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "MI" for key in keys):
            return []
        return c.select(await self._all(client), [key for key in keys if key.state == "MI"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MI":
            return []
        return [(race_result.key, race_result.title or str(race_result.key)) for race_result in await self._all(client)]
