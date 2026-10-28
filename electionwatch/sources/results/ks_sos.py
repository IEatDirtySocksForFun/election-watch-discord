"""Kansas — Secretary of State unofficial results site (sos.ks.gov/ent/).

Kansas has no JSON/CSV feed: the SOS publishes one server-rendered HTML page per race
category, all under `https://www.sos.ks.gov/ent/`:

    statewide.html      Governor / Lt. Governor, SOS, AG, Treasurer, Insurance
    national.html       U.S. Senate, U.S. House
    ks-house.html       Kansas House
    ks-boe.html         State Board of Education
    judicial.html       district court judges
    constitutional.html ballot questions

Each page carries the same markup, one block per contest:

    <div class='race' id='div1001'>
      <h3>United States Senate</h3>
      <p>Precincts Reporting: 4150 of 4150</p>
      <div class='race-candidate-list'>
        <div class='row'>
          <div class='col-6'><p>Damon Anderson</p></div>
          <div class='col-2'><p>5,736</p></div>
          <div class='col-1'><p>3%</p></div>
          <div class='col-3'><table ...></table></div>
        </div>
        ... one row per candidate ...
        <div class='row'><div class='col-6'><p>Total Votes</p></div>
          <div class='col-2'><p>612,155</p></div> ...
      </div>
    </div>

and `Last Update: <span id='time-stamp'>08/05/2026 4:25 AM</span>` in the header.

Things worth knowing:

- The site serves **one election at a time** (the current one); older elections are PDFs.
  ELECTION_DATE therefore cannot select an election here - whatever the site shows is what
  the adapter returns. This is recorded in the results docs.
- Primary pages split an office by party ("United States Senate (D)", "(R)"); those are
  skipped, exactly like the Clarity feeds, because the bot tracks the general election.
- Candidates carry no party on the page (the party is the contest suffix), so `party` is
  None for general-election contests.
- The category pages are discovered from the navigation of the first page, so a renaming
  of a tab does not need a code change.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://www.sos.ks.gov/ent/"
BOOTSTRAP = BASE + "statewide.html"
HUMAN = "https://www.sos.ks.gov/elections/election-results.html"

#: Used when the navigation cannot be read (county pages are never fetched).
FALLBACK_PAGES = (
    "national.html",
    "statewide.html",
    "ks-house.html",
    "ks-boe.html",
    "judicial.html",
    "constitutional.html",
)

_SKIP_PAGES = {"favorite-races.html", "democratic.html", "republican.html"}
_PAGE_LINK = re.compile(r"href=['\"]([a-z0-9-]+\.html)['\"]")

_PRIMARY_SUFFIX = re.compile(r"\s*\((?:D|R|DEM|REP|LIB|LBT|G|GRN|I|IND|NPA)\)\s*$", re.I)
_PRECINCTS = re.compile(r"Precincts\s+Reporting:\s*([\d,]+)\s+of\s+([\d,]+)", re.I)

_STATEWIDE = (
    ("GOVERNOR", "GOV"),
    ("SECRETARY OF STATE", "SOS"),
    ("ATTORNEY GENERAL", "AG"),
    ("STATE TREASURER", "TREAS"),
    ("COMMISSIONER OF INSURANCE", "INSCOM"),
    ("COMMISSIONER OF AGRICULTURE", "AGCOM"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _trailing_number(text: str) -> str | None:
    match = re.search(r"(\d{1,3})\s*$", text or "")
    return f"{int(match.group(1)):02d}" if match else None


def race_key(name: str) -> RaceKey:
    """Map a Kansas contest heading to a RaceKey."""
    office_name = _norm(name)
    special = c.is_special(office_name)
    if office_name.startswith("UNITED STATES SENATE"):
        return RaceKey("KS", Level.SENATE, "SEN", None, special)
    if "UNITED STATES HOUSE OF REPRESENTATIVES" in office_name:
        return RaceKey("KS", Level.HOUSE, "HOUSE", _trailing_number(office_name) or "00", special)
    for title, office in _STATEWIDE:
        if office_name.startswith(title):
            return RaceKey("KS", Level.GOVERNOR, office, None, special)
    if office_name.startswith("KANSAS SENATE") or office_name.startswith("STATE SENATE"):
        return RaceKey("KS", Level.STATE, "SD", _trailing_number(office_name) or "00", special)
    if office_name.startswith("KANSAS HOUSE OF REPRESENTATIVES"):
        return RaceKey("KS", Level.STATE, "HD", _trailing_number(office_name) or "00", special)
    return c.local_key("KS", office_name)


def nav_pages(html: str) -> list[str]:
    """Race-category pages linked from the site's navigation."""
    pages = [page for page in dict.fromkeys(_PAGE_LINK.findall(html or "")) if page not in _SKIP_PAGES]
    return pages or list(FALLBACK_PAGES)


def _last_update(page: HTMLParser) -> datetime | None:
    node = page.css_first("#time-stamp")
    if node is None:
        return None
    return c.parse_updated_at(node.text(strip=True), tz="America/Chicago")


def _candidate_rows(page: HTMLParser) -> tuple[list[CandidateResult], int | None]:
    cands: list[CandidateResult] = []
    total: int | None = None
    for row in page.css("div.race-candidate-list > div.row"):
        label = row.css_first("div.col-6 p")
        votes = row.css_first("div.col-2 p")
        if label is None or votes is None:
            continue
        name = (label.text() or "").strip()
        if not name:
            continue
        if _norm(name) == "TOTAL VOTES":
            total = c.to_int(votes.text())
            continue
        raw_pct = row.css_first("div.col-1 p")
        cands.append(
            CandidateResult(
                name=name,
                party=None,
                votes=c.to_int(votes.text()),
                pct=c.to_pct(raw_pct.text() if raw_pct is not None else None),
            )
        )
    return cands, total


def parse_ks(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one Kansas category page."""
    page = HTMLParser(html or "")
    updated = _last_update(page)
    results: list[RaceResult] = []
    seen: set[str] = set()
    for race in page.css("div.race"):
        heading = race.css_first("h3")
        name = (heading.text() if heading is not None else "").strip()
        name = re.sub(r"\s+", " ", name)
        if not name or _PRIMARY_SUFFIX.search(name):
            continue  # party primary half of an office
        precincts = reporting = None
        match = _PRECINCTS.search(race.text(separator=" ", strip=True))
        if match:
            reporting, precincts = c.to_int(match.group(1)), c.to_int(match.group(2))
        cands, total = _candidate_rows(race)
        if not cands:
            continue
        key = race_key(name)
        if str(key) in seen:  # two ballot questions with the same heading
            box = race.css_first("input[name='chkbxFavoriteRace']")
            race_id = (box.attributes.get("id") if box is not None else "") or ""
            key = c.local_key("KS", f"{name} {race_id}")
            if str(key) in seen:
                continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                name,
                cands,
                fetched_at,
                source=KsSos.name,
                url=HUMAN,
                total_votes=total if total is not None else sum(candidate.votes for candidate in cands),
                precincts_reporting=reporting,
                precincts_total=precincts,
                source_updated_at=updated,
            )
        )
    return results


@register
class KsSos(ResultsSource):
    name = "ks_sos"
    states = frozenset({"KS"})
    scrape = True  # HTML-only site; the SOS publishes no machine-readable results
    enabled_by_default = True  # ... but it is the state's own official results site

    async def _pages(self, client: httpx.AsyncClient) -> list[str]:
        html = await c.get_text(client, BOOTSTRAP)
        return nav_pages(html)

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "KS" for key in keys):
            return []
        results: list[RaceResult] = []
        failures = 0
        pages = await self._pages(client)
        for page in pages:
            try:
                html = await c.get_text(client, BASE + page)
            except SourceUnavailable:
                failures += 1
                continue
            results.extend(parse_ks(html, fetched_at=c.utcnow()))
        if pages and failures == len(pages):
            raise SourceUnavailable(f"KS: none of {len(pages)} category pages could be fetched")
        return c.select(results, [key for key in keys if key.state == "KS"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "KS":
            return []
        seen: dict[str, tuple[RaceKey, str]] = {}
        for page in await self._pages(client):
            try:
                html = await c.get_text(client, BASE + page)
            except SourceUnavailable:
                continue
            for result in parse_ks(html, fetched_at=c.utcnow()):
                seen.setdefault(str(result.key), (result.key, result.title or str(result.key)))
        return list(seen.values())
