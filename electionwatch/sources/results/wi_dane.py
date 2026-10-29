"""Wisconsin — Dane County Clerk results page (elections.countyofdane.com).

Wisconsin has no statewide election-night reporting system: the WEC site
(elections.wi.gov) is behind Cloudflare and, by law, municipal clerks report to the 72
county clerks, each of whom posts the unofficial count for one county. Dane County (Madison,
the state's second-largest county) publishes a server-rendered page per contest that a
plain GET returns:

    https://elections.countyofdane.com/Election-Result

    <div class="row" id="race0004">
      <h4>Governor / Lieutenant Governor</h4>
      ... Precincts Reporting (0 of 282) ...
      <tr><td><strong>REP Tom Tiffany /David Varnam</strong> <em>(REP)</em></td>
          <td>0.0%</td><td class="text-right">0</td></tr>

No API and no JSON are offered, so this is a scrape: ``scrape = True`` and the adapter is
off by default. Every county that reports to the state this way is a *partial* picture, so
results are titled ``"<contest> (partial: 1 county)"`` and link to the WEC results page.

The 2026 general blocks are already online but still at zero votes; the page is otherwise
the same markup the county serves on election night.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

URL = "https://elections.countyofdane.com/Election-Result"
#: The state page the partial count links to.
HUMAN = "https://elections.wi.gov/elections-voting/results"

#: Counties merged by this adapter; the title suffix is derived from it.
COUNTIES = ("Dane",)

_STATEWIDE = {
    "GOVERNOR / LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "STATE SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
}

_US_SENATE = ("U.S. SENATOR", "US SENATOR", "UNITED STATES SENATOR")
_US_HOUSE = re.compile(r"^REPRESENTATIVE IN CONGRESS DISTRICT\s+(\d+)$", re.I)
_STATE_SENATE = re.compile(r"^STATE SENATOR DISTRICT\s+(\d+)$", re.I)
_STATE_ASSEMBLY = re.compile(r"^REPRESENTATIVE TO THE ASSEMBLY DISTRICT\s+(\d+)$", re.I)

_PARTY_SUFFIX = re.compile(r"\s*\((?:REP|DEM|LIB|GRN|GRE|CON|CST|IND|NON|NP|WRI)\)\s*$", re.I)
_PRIMARY_PREFIX = re.compile(r"^(?:REP|DEM|LIB|GRN|GRE|CON|CST|IND|NON)\s+", re.I)
_PRECINCTS = re.compile(r"Precincts Reporting \((\d+) of (\d+)\)")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str) -> RaceKey:
    """Map a Dane County contest title to a canonical key."""
    office_name = _norm(name)
    special = c.is_special(office_name)
    if office_name in _US_SENATE:
        return RaceKey("WI", Level.SENATE, "SEN", None, special)
    if office_name in _STATEWIDE:
        return RaceKey("WI", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    match = _US_HOUSE.match(office_name)
    if match:
        return RaceKey("WI", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = _STATE_SENATE.match(office_name)
    if match:
        return RaceKey("WI", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = _STATE_ASSEMBLY.match(office_name)
    if match:
        return RaceKey("WI", Level.STATE, "HD", f"{int(match.group(1)):02d}", special)
    return c.local_key("WI", name)


def partial_title(title: str) -> str:
    """'Governor / Lieutenant Governor (partial: 1 county)'."""
    county_count = len(COUNTIES)
    return (
        f"{title} (partial: {county_count} county)"
        if county_count == 1
        else f"{title} (partial: {county_count} counties)"
    )


def _clean_candidate(cell) -> tuple[str, str | None]:
    """'REP Tom Tiffany /David Varnam (REP)' -> ('Tom Tiffany /David Varnam', 'REP')."""
    party = None
    small = cell.css_first("em")
    if small is not None:
        party = c.norm_party(small.text(strip=True).strip("() "))
    strong = cell.css_first("strong")
    name = strong.text(strip=True) if strong is not None else cell.text(strip=True)
    name = _PARTY_SUFFIX.sub("", name or "").strip()
    if party and _norm(name).startswith(f"{party} "):
        name = name[len(party) :].strip()  # the county prints the party twice
    name = _PRIMARY_PREFIX.sub("", name).strip()
    return c.strip_incumbent(name)[0], party


def _vote_cell_pct(cell) -> float | None:
    match = _PCT.search(cell.text())
    return c.to_pct(match.group(1)) if match else None


def parse_dane(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the Dane County results page."""
    tree = HTMLParser(html or "")
    results: list[RaceResult] = []
    seen: set[str] = set()
    for block in tree.css("div.row[id]"):
        if not (block.attributes.get("id") or "").startswith("race"):
            continue
        heading = block.css_first("h4")
        if heading is None:
            continue
        title = re.sub(r"\s+", " ", heading.text(strip=True))
        if not title or _norm(title).startswith("BALLOTS CAST"):
            continue  # turnout line, not a contest
        key = race_key(title)
        if str(key) in seen:
            continue
        cands: list[CandidateResult] = []
        for row in block.css("tbody tr"):
            cells = row.css("td")
            if len(cells) < 3:
                continue
            name, party = _clean_candidate(cells[0])
            if not name or _norm(name).startswith("WRITE-IN"):
                continue
            cands.append(
                CandidateResult(
                    name=name,
                    party=party,
                    votes=c.to_int(cells[-1].text(strip=True)),
                    pct=_vote_cell_pct(cells[1]),
                    incumbent=False,
                    winner=False,  # the county marks no winner before the canvass
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        precincts = _PRECINCTS.search(block.text())
        results.append(
            c.make_result(
                key,
                partial_title(title),
                cands,
                fetched_at,
                source=WiDane.name,
                url=HUMAN,
                precincts_reporting=int(precincts.group(1)) if precincts else None,
                precincts_total=int(precincts.group(2)) if precincts else None,
            )
        )
    return results


@register
class WiDane(ResultsSource):
    name = "wi_dane"
    states = frozenset({"WI"})
    scrape = True
    enabled_by_default = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "WI" for key in keys):
            return []
        try:
            resp = await client.get(URL, headers={"Accept": "text/html"})
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"GET {URL} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise SourceUnavailable(f"GET {URL} -> HTTP {resp.status_code}")
        parsed = parse_dane(resp.text, fetched_at=c.utcnow())
        return c.select(parsed, [key for key in keys if key.state == "WI"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "WI":
            return []
        try:
            response = await client.get(URL, headers={"Accept": "text/html"})
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"GET {URL} failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceUnavailable(f"GET {URL} -> HTTP {response.status_code}")
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in parse_dane(response.text, fetched_at=c.utcnow())
        ]
