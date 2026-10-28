"""New Mexico — Secretary of State results site (electionresults.sos.nm.gov).

New Mexico runs the same ASP.NET "Results SW" family of pages as North Dakota, with its own
markup, one URL per contest group:

    https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type=FED&map=CTY   federal
    https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type=LGX&map=CTY   legislature
    (type=ECX education commissioners, JDX district judges, MGX magistrate judges,
     CLX local bond questions - not tracked)

The pages always describe the election that is currently published; on 2026-10-27 that was
the 2026-11-03 general election with 0 of 2204 precincts reported, so the fixture shows the
real candidate set with zero counts.

Contest block:

    <div class="wrapper-inside wrapper-border">
      <div class="display-results-box-a"><h1>United States Senator </h1></div>
      <div class="col display-results-box-c">
        <div class="precinct-fully">Precincts Fully: 0 / 2204</div>
        <div class="precinct-partial">Partially: 0 / 2204</div>
      </div>
      <div class="section group">
        <div class="col display-results-box-d"><h1>BEN R LUJAN</h1>
          <h2 class="Democrat">Democrat</h2></div>
        <div class="col display-results-box-e">... bar graph ...</div>
        <div class="col display-results-box-f"><h1>0</h1></div>
      </div>
      ... one "section group" per candidate ...

Notes:

- The page marks no winner (no winner icon exists in this markup), so `winner` is always
  False and the bot's own call logic decides; percentages are not published either.
- Candidate names arrive in upper case ("BEN R LUJAN"); they are kept verbatim.
- There is no "last updated" stamp; `source_updated_at` stays None.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

URL = "https://electionresults.sos.nm.gov/resultsSW.aspx?text=All&type={}&map=CTY"
PAGES = ("FED", "LGX")
HUMAN = "https://electionresults.sos.nm.gov/"

_PRECINCTS = re.compile(r"Precincts\s+Fully:\s*([\d,]+)\s*/\s*([\d,]+)", re.I)
_DISTRICT = re.compile(r"DISTRICT\s*0*(\d{1,3})\b", re.I)

_STATEWIDE = (
    ("GOVERNOR", "GOV"),
    ("LIEUTENANT GOVERNOR", "LTGOV"),
    ("ATTORNEY GENERAL", "AG"),
    ("SECRETARY OF STATE", "SOS"),
    ("STATE TREASURER", "TREAS"),
    ("STATE AUDITOR", "AUD"),
    ("COMMISSIONER OF PUBLIC LANDS", "LANDCOM"),
    ("SUPERINTENDENT OF INSURANCE", "INSCOM"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _district(text: str) -> str | None:
    match = _DISTRICT.search(text or "")
    return f"{int(match.group(1)):02d}" if match else None


def race_key(title: str) -> RaceKey:
    """Map a New Mexico contest heading to a RaceKey."""
    office_name = _norm(title)
    special = c.is_special(office_name)
    if office_name.startswith("UNITED STATES SENATOR") or office_name.startswith("U.S. SENATOR"):
        return RaceKey("NM", Level.SENATE, "SEN", None, special)
    if "UNITED STATES REPRESENTATIVE" in office_name or "REPRESENTATIVE IN CONGRESS" in office_name:
        return RaceKey("NM", Level.HOUSE, "HOUSE", _district(office_name) or "00", special)
    if office_name.startswith("PRESIDENT"):
        return RaceKey("NM", Level.LOCAL, "LOCAL", "us-president")
    for prefix, code in _STATEWIDE:
        if office_name.startswith(prefix):
            return RaceKey("NM", Level.GOVERNOR, code, None, special)
    if office_name.startswith("STATE SENATOR") or office_name.startswith("STATE SENATE"):
        return RaceKey("NM", Level.STATE, "SD", _district(office_name) or "00", special)
    if office_name.startswith(("STATE REPRESENTATIVE", "STATE REPRESENTATIVES")):
        return RaceKey("NM", Level.STATE, "HD", _district(office_name) or "00", special)
    return c.local_key("NM", office_name)


def parse_nm(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one resultsSW.aspx contest page."""
    page = HTMLParser(html or "")
    results: list[RaceResult] = []
    seen: set[str] = set()
    for contest in page.css("div.wrapper-inside.wrapper-border"):
        heading = contest.css_first("div.display-results-box-a h1")
        if heading is None:
            heading = contest.css_first("h1")
        title = re.sub(r"\s+", " ", (heading.text() if heading is not None else "").strip())
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        reporting = total = None
        match = _PRECINCTS.search(contest.text(separator=" ", strip=True))
        if match:
            reporting, total = c.to_int(match.group(1)), c.to_int(match.group(2))
        cands: list[CandidateResult] = []
        for row in contest.css("div.section.group"):
            name_node = row.css_first("div.display-results-box-d h1")
            if name_node is None:
                continue
            name = re.sub(r"\s+", " ", name_node.text()).strip()
            if not name:
                continue
            party_node = row.css_first("div.display-results-box-d h2")
            votes_node = row.css_first("div.display-results-box-f h1")
            cands.append(
                CandidateResult(
                    name=name,
                    party=c.norm_party(party_node.text() if party_node is not None else None),
                    votes=c.to_int(votes_node.text() if votes_node is not None else None),
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=NmSos.name,
                url=HUMAN,
                precincts_reporting=reporting,
                precincts_total=total,
            )
        )
    return results


@register
class NmSos(ResultsSource):
    name = "nm_sos"
    states = frozenset({"NM"})
    scrape = True  # HTML only
    enabled_by_default = True  # the state's own official results site

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "NM" for key in keys):
            return []
        results: list[RaceResult] = []
        pages = 0
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page))
            except SourceUnavailable:
                continue
            pages += 1
            results.extend(parse_nm(html, fetched_at=c.utcnow()))
        if not pages:
            raise SourceUnavailable(f"NM: no results page fetched ({URL.format('FED')})")
        return c.select(results, [key for key in keys if key.state == "NM"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "NM":
            return []
        seen: dict[str, tuple[RaceKey, str]] = {}
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page))
            except SourceUnavailable:
                continue
            for result in parse_nm(html, fetched_at=c.utcnow()):
                seen.setdefault(str(result.key), (result.key, result.title or str(result.key)))
        return list(seen.values())
