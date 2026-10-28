"""Illinois — State Board of Elections "Vote Totals" pages (HTML).

The SBE publishes results as server-rendered ASP.NET pages, one page per office
category, no API and no JSON:

    https://www.elections.il.gov/electionoperations/ElectionVoteTotals.aspx
        -> "Federal / Statewide" (US Senate, Governor, the other statewide
           executives, and every US House district as "1ST CONGRESS" ...)
    ...ElectionVoteTotals.aspx?ID=<election>&OfficeType=<category>
        -> "Senate" (state Senate, "2ND SENATE" ...) and
           "District All" (state House, "1ST REPRESENTATIVE" ...)

Each office is a block:

    <div class="infoHeaders ..."><asp:Label>UNITED STATES SENATOR</asp:Label></div>
    ...
    <table class="SearchListTable" id="...gvTotals_13071">
      <tr><th>Candidate</th><th>Party</th><th>Total Votes</th><th>Percent ...</th></tr>
      <tr><td><a>JULIANA STRATTON</a></td><td>DEMOCRATIC</td><td>507,689</td><td>40.39%</td></tr>

The percent column is "Percent of Party Vote" in a primary, so the adapter ignores
it and lets RaceResult.finalize() compute the share of the race's votes.

Discovery: the page defaults to the newest election and lists every election in a
dropdown. The URL id for an *older* election is an opaque token handed out by an
ASP.NET postback, so the adapter uses the default page and checks the selected
election's name. On election night that is the election being tracked; if it is
not, the adapter reports itself unavailable (and IL_ELECTION_ID can pin the opaque
ID for a past election: "?ID=<token>"). 2026 general election: the SBE has not
published that election's page yet (checked 2026-10-27 with the 2026 primary live).
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://www.elections.il.gov/electionoperations/"
PAGE = "ElectionVoteTotals.aspx"
HUMAN = BASE + PAGE

#: date -> the SBE's own name for that election (the dropdown label).
ELECTIONS: dict[str, str] = {
    "2024-11-05": "2024 GENERAL ELECTION",
    "2026-03-17": "2026 GENERAL PRIMARY",
    "2026-11-03": "2026 GENERAL ELECTION",
}

#: Office categories polled besides the default "Federal / Statewide" page.
CATEGORIES = ("Senate", "District All")

_STATEWIDE = {
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "COMPTROLLER": "COMP",
    "TREASURER": "TREAS",
}

_BLOCK = re.compile(
    r'class="infoHeaders[^"]*"[^>]*><asp:Label[^>]*>([^<]+)</asp:Label>'
    r'.*?<table[^>]*class="SearchListTable"[^>]*>(.*?)</table>',
    re.S,
)
_DISTRICT = re.compile(r"^(\d{1,3})(?:ST|ND|RD|TH)\s+(CONGRESS|SENATE|REPRESENTATIVE)$")


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def race_key(office: str) -> RaceKey:
    """Map an Illinois office heading to a RaceKey."""
    office_name = _upper(office)
    special = c.is_special(office_name)
    if office_name == "UNITED STATES SENATOR":
        return RaceKey("IL", Level.SENATE, "SEN", None, special)
    if office_name in _STATEWIDE:
        return RaceKey("IL", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    if office_name.startswith("PRESIDENTIAL ELECTORS") or office_name == "PRESIDENT":
        return c.local_key("IL", "us-president")
    match = _DISTRICT.match(office_name)
    if match:
        num = f"{int(match.group(1)):02d}"
        kind = match.group(2)
        if kind == "CONGRESS":
            return RaceKey("IL", Level.HOUSE, "HOUSE", num, special)
        if kind == "SENATE":
            return RaceKey("IL", Level.STATE, "SD", num, special)
        return RaceKey("IL", Level.STATE, "HD", num, special)
    return c.local_key("IL", office_name)


def election_label(html: str) -> str | None:
    """The election the page is showing ('2026 GENERAL PRIMARY')."""
    tree = HTMLParser(html or "")
    node = tree.css_first("select#ContentPlaceHolder1_ddlElections option[selected]")
    if node is None:
        node = tree.css_first("select#ContentPlaceHolder1_ddlElections option")
    return node.text(strip=True) if node is not None else None


def categories(html: str) -> list[str]:
    """Category page URLs (Senate / District All) linked from a results page."""
    tree = HTMLParser(html or "")
    links: list[str] = []
    for anchor in tree.css("a"):
        href = anchor.attributes.get("href") or ""
        if "OfficeType=" not in href:
            continue
        label = anchor.text(strip=True)
        if label in CATEGORIES and href not in links:
            links.append(href)
    return links


def parse_il(html: str, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Pure parse of one SBE Vote Totals page."""
    results: list[RaceResult] = []
    seen: set[str] = set()
    for name, body in _BLOCK.findall(html or ""):
        office = _upper(name)
        if not office or re.fullmatch(r"\d{4} [A-Z ]+", office):
            continue  # the page's own election banner, not an office
        tree = HTMLParser(f"<table>{body}</table>")
        cands: list[CandidateResult] = []
        for tr in tree.css("tr"):
            cells = [td.text(strip=True) for td in tr.css("td")]
            if len(cells) < 4 or not cells[0]:
                continue
            if cells[0].upper() in ("VOTE TOTALS", "TOTAL", "TOTALS"):
                continue  # the page's own footer row: total votes for the office
            cands.append(
                CandidateResult(
                    name=cells[0],
                    party=c.norm_party(cells[1]),
                    votes=c.to_int(cells[2]),
                )
            )
        if not cands:
            continue
        key = race_key(office)
        if str(key) in seen:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                office,
                cands,
                fetched_at,
                source=IlSbe.name,
                url=url,
                total_votes=sum(candidate.votes for candidate in cands),
            )
        )
    return results


def page_url(date: str) -> str:
    pinned = os.environ.get("IL_ELECTION_ID")
    if pinned:
        return f"{HUMAN}?ID={pinned}"
    return HUMAN


async def _get(client: httpx.AsyncClient, url: str) -> str:
    try:
        return await c.get_text(client, url)
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"IL: {url} failed ({exc})") from exc


async def _pages(client: httpx.AsyncClient, date: str) -> list[tuple[str, str]]:
    expected = ELECTIONS.get(date)
    if not expected:
        raise SourceUnavailable(
            f"IL: no SBE election name configured for {date}; add it to ELECTIONS "
            f"(the dropdown on {HUMAN} lists every election)"
        )
    first = await _get(client, page_url(date))
    label = election_label(first)
    if label and _upper(label) != expected.upper():
        raise SourceUnavailable(
            f"IL: the SBE page shows {label!r}, not {expected!r} - the {expected} "
            f"results are not published yet (set IL_ELECTION_ID to pin a past election)"
        )
    pages = [(first, page_url(date))]
    for href in categories(first):
        url = href if href.startswith("http") else BASE + href
        pages.append((await _get(client, url), url))
    return pages


async def _all_races(client: httpx.AsyncClient, date: str, fetched_at: datetime) -> list[RaceResult]:
    results: list[RaceResult] = []
    seen: set[str] = set()
    for html, url in await _pages(client, date):
        for race_result in parse_il(html, fetched_at=fetched_at, url=url):
            if str(race_result.key) in seen:
                continue
            seen.add(str(race_result.key))
            results.append(race_result)
    return results


@register
class IlSbe(ResultsSource):
    name = "il_sbe"
    states = frozenset({"IL"})
    scrape = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        il_keys = [key for key in keys if key.state == "IL"]
        if not il_keys:
            return []
        return c.select(await _all_races(client, c.election_date(), c.utcnow()), il_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "IL":
            return []
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in await _all_races(client, c.election_date(), c.utcnow())
        ]
