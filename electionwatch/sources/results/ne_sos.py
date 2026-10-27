"""Nebraska — Secretary of State election results (electionresults.nebraska.gov).

Nebraska has no JSON feed: the unofficial results are server-rendered HTML, one page per
contest family, one block per contest:

    https://electionresults.nebraska.gov/resultsSW.aspx?type=SW&map=CTY   statewide offices
    ...?type=CG&map=DIST   US House          ...?type=LD&map=DIST   Legislature
    ...?type=SE&map=DIST   State Board of Education
    ...?type=CC&map=CTY    county/community college, judicial, NRD, PPD, regents, ...

Each contest block is:

    <div class="wrapper-inside wrapper-border">
      <div class="display-results-box-a"><h1>For United States Senator - 6  Year Term</h1></div>
      <div class="display-results-box-c">
        <div class="precinct-fully">Precincts Fully: 0 / 1322</div>
        <div class="precinct-partial">Partially: 0 / 1322</div>
      </div>
      <div class="section group">                       <!-- one per candidate -->
        <div class="col display-results-box-d"><h1>Pete  Ricketts </h1><h2>Republican</h2></div>
        <div class="col display-results-box-f"><h1>0</h1></div>
      </div>
      ...
      <div class="section group">
        <div class="col display-results-box-totalvotes"><h2>total votes</h2></div>
        <div class="col display-results-box-total"><h2>0</h2></div>
      </div>
    </div>

`precincts_reporting` is the "Precincts Fully" count (Nebraska's own headline figure);
partial precincts are not added in, so expected_vote_pct never overstates completion.
Nebraska's Legislature is unicameral and non-partisan: its contests map to the state
senate (SD) level so the key round-trips through racekeys. Everything else that is not a
federal or statewide executive office (State Board of Education, regents, judges, county
and natural-resource districts, ballot questions) becomes a stable LOCAL slug.

The site publishes no last-updated timestamp, so source_updated_at stays None.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://electionresults.nebraska.gov/resultsSW.aspx"
HUMAN = "https://electionresults.nebraska.gov/"
UA = "election-watch-discord/0.1 (+https://github.com/election-watch-discord)"

#: contest family -> map mode, in the order the site lists them.
PAGES: dict[str, str] = {
    "SW": "CTY",   # US Senate, Governor, Secretary of State, Treasurer, AG, Auditor
    "CG": "DIST",  # US House
    "LD": "DIST",  # Legislature (unicameral)
    "SE": "DIST",  # State Board of Education
    "PS": "DIST",  # Public Service Commission
    "RG": "DIST",  # Regents
    "CC": "CTY", "ED": "CTY", "JD": "CTY", "LC": "CTY",
    "NR": "CTY", "PA": "CTY", "PP": "CTY", "RD": "CTY", "UT": "CTY",
}

#: Families that only ever hold local contests.
LOCAL_PAGES = tuple(t for t in PAGES if t not in ("SW", "CG", "LD"))

_STATEWIDE = {
    "GOVERNOR AND LT. GOVERNOR": "GOV",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "TREASURER": "TREAS",
    "ATTORNEY GENERAL": "AG",
    "AUDITOR OF PUBLIC ACCOUNTS": "AUD",
    "AUDITOR": "AUD",
}

_SENATE = re.compile(r"^UNITED STATES SENATOR\b")
_HOUSE = re.compile(r"^REPRESENTATIVE IN CONGRESS\b")
_LEGISLATURE = re.compile(r"^MEMBER OF THE LEGISLATURE\b")

_PRECINCT_FULL = re.compile(r"([\d,]+)\s*/\s*([\d,]+)")
_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    return _WS.sub(" ", (text or "").replace("\u00a0", " ")).strip()


def _bare(name: str) -> str:
    """'For United States Senator - 6  Year Term' -> 'UNITED STATES SENATOR'."""
    n = _clean(name).upper()
    n = re.sub(r"^FOR\s+", "", n)
    return re.split(r"\s+-\s+", n)[0].strip()


def race_key(name: str) -> RaceKey:
    n = _clean(name).upper()
    bare = _bare(n)
    special = c.is_special(n)
    if _SENATE.match(bare):
        return RaceKey("NE", Level.SENATE, "SEN", None, special)
    if bare in _STATEWIDE:
        return RaceKey("NE", Level.GOVERNOR, _STATEWIDE[bare], None, special)
    if _HOUSE.match(bare):
        return RaceKey("NE", Level.HOUSE, "HOUSE", c.district_from(n) or "AL", special)
    if _LEGISLATURE.match(bare):
        return RaceKey("NE", Level.STATE, "SD", c.district_from(n) or "00", special)
    # Keep the readable label ("Member of the State Board of Education - District 05"),
    # minus the site's "For " prefix, as the local slug.
    return c.local_key("NE", re.sub(r"^FOR\s+", "", n).strip() or n)


def _precincts(block) -> tuple[int | None, int | None]:
    node = block.css_first("div.precinct-fully")
    m = _PRECINCT_FULL.search(_clean(node.text() if node else ""))
    if not m:
        return None, None
    return c.to_int(m.group(1)) or None, c.to_int(m.group(2)) or None


def parse_ne_page(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one resultsSW.aspx page."""
    tree = HTMLParser(html or "")
    out: list[RaceResult] = []
    seen: set[str] = set()
    for block in tree.css("div.wrapper-inside"):
        title_node = block.css_first("div.display-results-box-a h1")
        title = _clean(title_node.text() if title_node else "")
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        cands: list[CandidateResult] = []
        for group in block.css("div.section.group"):
            name_node = group.css_first("div.display-results-box-d h1")
            if name_node is None:
                continue
            name, incumbent = c.strip_incumbent(_clean(name_node.text()))
            if not name:
                continue
            party_node = group.css_first("div.display-results-box-d h2")
            votes_node = group.css_first("div.display-results-box-f h1")
            cands.append(CandidateResult(
                name=name,
                party=c.norm_party(_clean(party_node.text() if party_node else "")),
                votes=c.to_int(_clean(votes_node.text() if votes_node else "")),
                incumbent=incumbent,
            ))
        if not cands:
            continue
        seen.add(str(key))
        total_node = block.css_first("div.display-results-box-total h2")
        total = c.to_int(_clean(total_node.text() if total_node else ""))
        reporting, precinct_total = _precincts(block)
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=NeSos.name,
            url=HUMAN,
            total_votes=total or sum(x.votes for x in cands),
            precincts_reporting=reporting,
            precincts_total=precinct_total,
        ))
    return out


async def _page(client: httpx.AsyncClient, family: str) -> str:
    url = f"{BASE}?type={family}&map={PAGES[family]}"
    try:
        resp = await client.get(url, headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": UA,
        })
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise SourceUnavailable(f"GET {url} -> HTTP {resp.status_code}")
    return resp.text


def _families(wanted: list[RaceKey]) -> list[str]:
    families: list[str] = []
    levels = {k.level for k in wanted}
    if Level.SENATE in levels or Level.GOVERNOR in levels:
        families.append("SW")
    if Level.HOUSE in levels:
        families.append("CG")
    if Level.STATE in levels:
        families.append("LD")
    if Level.LOCAL in levels:
        families.extend(LOCAL_PAGES)
    return families


@register
class NeSos(ResultsSource):
    name = "ne_sos"
    states = frozenset({"NE"})
    scrape = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = [k for k in keys if k.state == "NE"]
        if not wanted:
            return []
        results: list[RaceResult] = []
        for family in _families(wanted):
            results.extend(parse_ne_page(await _page(client, family), fetched_at=c.utcnow()))
        return c.select(results, wanted)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "NE":
            return []
        seen: dict[str, tuple[RaceKey, str]] = {}
        for family in PAGES:
            for result in parse_ne_page(await _page(client, family), fetched_at=c.utcnow()):
                seen.setdefault(str(result.key), (result.key, result.title or str(result.key)))
        return list(seen.values())
