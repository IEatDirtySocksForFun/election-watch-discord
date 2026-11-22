"""Massachusetts — Secretary of the Commonwealth election archive ("PD43+").

Massachusetts has no election-night results feed: the Elections Division's results page states
that results are published only after they are certified, and that nothing is published on
election night. PD43+, the Secretary's archive at electionstats.state.ma.us, carries every
contest of an election once the count is certified. One page lists all of them for a date:

    https://electionstats.state.ma.us/elections/search/date:2026-11-03

    <tr id="election-id-173531" class="election_item general_party">
      <td class="year first">2026</td>
      <td class="">U.S. Senate</td>
      <td class="">Statewide</td>
      <td class="party_border_top">General Election</td>
      <td class="candidates_container_cell">
        <table class="candidates"> ... one row per candidate ...
          <td class="candidate"><div class="name">Edward J. Markey</div>
              <div class="party">Democratic</div></td>
          <td class="number">1,718,276</td><td class="number">63.7%</td>

Per contest the page also prints "All Others", "Blanks" and "Total Votes Cast" rows; the
first two are ballot statistics, not candidates, so they are dropped and the total becomes
the race's `total_votes`. A winner row is flagged by the `is_winner` class on its `<tr>`:
the archive declares winners, so the parser copies the flag instead of ranking the count.

Keys: statewide offices use the shared codes (GOV, AG, SOS, TREAS, AUD); U.S. House uses
the numbered district from "1st Congressional"; state senate and state house districts are
named, not numbered ("Cape and Islands", "1st Barnstable"), so their RaceKey district is
the slugified name (MA SD-cape-and-islands, MA HD-1st-barnstable), as Vermont does. Every
other office on the ballot (Governor's Council, district attorney, county offices, charter
commissions) becomes a local race keyed by the office and district names.

There is no JSON or CSV to read for the whole election, so this is a scrape:
`scrape = True`, off by default, one request per refresh for the whole state. The PDF
precinct exports are not read; the archive publishes no precinct counts.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.parties import party_code
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

SEARCH = "https://electionstats.state.ma.us/elections/search/date:{date}"
HUMAN = "https://electionstats.state.ma.us/"

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF THE COMMONWEALTH": "SOS",
    "TREASURER": "TREAS",
    "AUDITOR": "AUD",
}
_US_SENATE = ("U.S. SENATE", "US SENATE", "UNITED STATES SENATE")
_US_HOUSE = ("U.S. HOUSE", "US HOUSE", "UNITED STATES HOUSE")
_STATE_SENATE = ("STATE SENATE", "STATE SENATOR")
_STATE_HOUSE = ("STATE REPRESENTATIVE", "STATE REP", "STATE REPRESENTATIVES")


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\u00a0", " ")).strip().upper()


def _district(name: str) -> str:
    return c.district_from(name) or "00"


def _party(label: str | None) -> str | None:
    """'Independent/Unenrolled' -> the first named party; 'Unenrolled' -> NP."""
    first = _norm(label).split("/")[0].strip()
    if not first:
        return None
    return party_code(first) or None


def race_key(office: str, district: str = "", special: bool = False) -> RaceKey:
    """Map a PD43+ office and district to a RaceKey."""
    name = _norm(office)
    where = _norm(district)
    if name in _US_SENATE:
        return RaceKey("MA", Level.SENATE, "SEN", None, special)
    if name in _US_HOUSE:
        return RaceKey("MA", Level.HOUSE, "HOUSE", _district(where), special)
    if name in _STATEWIDE:
        return RaceKey("MA", Level.GOVERNOR, _STATEWIDE[name], None, special)
    if name in _STATE_SENATE:
        return RaceKey("MA", Level.STATE, "SD", c.slug(where), special)
    if name in _STATE_HOUSE:
        return RaceKey("MA", Level.STATE, "HD", c.slug(where), special)
    label = name if where in ("", "STATEWIDE") else f"{name} {where}"
    return c.local_key("MA", label.replace("'", ""))


def _title(office: str, district: str) -> str:
    return office if _norm(district) in ("", "STATEWIDE") else f"{office} - {district}"


def parse_ma(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one dated PD43+ search page."""
    tree = HTMLParser(html or "")
    results: list[RaceResult] = []
    seen: set[str] = set()
    for row in tree.css('tr[id^="election-id-"]'):
        cells = row.css("td")
        if len(cells) < 4:
            continue
        office = cells[1].text(strip=True)
        district = cells[2].text(strip=True)
        stage = cells[3].text(strip=True)
        if not _norm(office):
            continue

        candidates: list[CandidateResult] = []
        total_votes: int | None = None
        for candidate_row in row.css("table.candidates tbody tr"):
            classes = candidate_row.attributes.get("class") or ""
            cell = candidate_row.css_first("td.candidate")
            numbers = candidate_row.css("td.number")
            if cell is None or not numbers:
                continue
            votes = c.to_int(numbers[0].text())
            if "n_total_votes" in classes:
                total_votes = votes
                continue
            if "non_candidate" in classes:  # All Others, Blanks, and other statistics
                continue
            name_cell = cell.css_first("div.name")
            party_cell = cell.css_first("div.party")
            name = (name_cell or cell).text(strip=True)
            if not name:
                continue
            candidates.append(
                CandidateResult(
                    name=name,
                    party=_party(party_cell.text(strip=True)) if party_cell else None,
                    votes=votes,
                    winner="is_winner" in classes,
                )
            )

        if not candidates:
            continue
        key = race_key(office, district, special=c.is_special(stage) or c.is_special(district))
        if str(key) in seen:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                _title(office, district),
                candidates,
                fetched_at,
                source=MaPd43.name,
                url=HUMAN,
                total_votes=total_votes if total_votes is not None else sum(c.votes for c in candidates),
            )
        )
    return results


def _url(date: str) -> str:
    return SEARCH.format(date=date)


async def _fetch_page(client: httpx.AsyncClient, date: str) -> list[RaceResult]:
    url = _url(date)
    page = await c.get_text(client, url)
    results = parse_ma(page, fetched_at=c.utcnow())
    if not results:
        raise SourceUnavailable(f"ma_pd43: no contests for {date} on {url}")
    return results


@register
class MaPd43(ResultsSource):
    name = "ma_pd43"
    states = frozenset({"MA"})
    scrape = True  # HTML only; no JSON or CSV export for a whole election
    enabled_by_default = False  # a certified archive, read when the owner turns it on

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ma_keys = [key for key in keys if key.state == "MA"]
        if not ma_keys:
            return []
        return c.select(await _fetch_page(client, c.election_date()), ma_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MA":
            return []
        try:
            results = await _fetch_page(client, c.election_date())
        except SourceUnavailable:
            return []
        return [(race_result.key, race_result.title or str(race_result.key)) for race_result in results]
