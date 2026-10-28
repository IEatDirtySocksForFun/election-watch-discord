"""Idaho — Secretary of State results archive (HTML, data embedded as JSON).

Idaho's live results app (results.voteidaho.gov) is an Angular SPA whose API is
per-jurisdiction (one entry per county plus a statewide "id" slug) and returns
contest metadata only - the actual vote numbers are loaded through an endpoint the
bundle does not expose. The SOS also publishes a static, per-election archive that
*does* carry the numbers, and it is the same data the app shows:

    https://archive.voteidaho.gov/results/2024/general/index.html
        ./congressional.html   "District 1 US Representative" ...
        ./legislative.html     "District 1" > "District 1 Senator",
                               "District 1 Representative A", "... B"
        ./presidential.html    "President and Vice President"
        (+ statewide.html / measures.html / county.html in some years)

Each contest is a heading followed by a Quarto/reactable widget whose data sits in
a JSON script block, so the parse is JSON, not table scraping:

    <h3>District 1 US Representative</h3>
    <script type="application/json" data-for="htmlwidget-...">
      {"x":{"tag":{"name":"Reactable","attribs":{"data":{
          "ContestantName":["Russ Fulcher (REP)","Kaylee Peterson (DEM)", ...],
          "TotalVotes":[331049,118656,9594,6933],
          "Percent":[0.7104,0.2546, ...]}}}}}

Party is in the candidate name's parentheses. The archive is a finished-results
snapshot (it appears after certification), so polling it is cheap in frequency
terms but the pages are big (congressional ~8 MB, legislative ~38 MB): fetch()
only downloads the pages the requested races live on. Idaho House districts elect
two members from separate positions, so those races use district "01A"/"01B".
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

INDEX = "https://archive.voteidaho.gov/results/{year}/{kind}/index.html"

#: month of election day -> the archive's directory name for that election.
KINDS = {3: "primary", 5: "primary", 11: "general"}

#: pages that never hold the offices this bot tracks.
SKIP_PAGES = {"county.html", "measures.html", "turnout.html", "xml.html", "close-races.html", "index.html"}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "STATE CONTROLLER": "COMP",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
}

_HEADING = re.compile(r"<(h[34])[^>]*>(.*?)</\1>", re.S)
_SCRIPT = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.S)
_PARTY = re.compile(r"\(([A-Za-z]{2,4})\)\s*$")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", fragment)).strip()


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).upper()


def race_key(office: str) -> RaceKey:
    """Map an archive heading ('District 1 Senator') to a RaceKey."""
    office_name = _upper(office)
    special = c.is_special(office_name)
    match = re.match(r"^DISTRICT (\d{1,2}) US REPRESENTATIVE$", office_name)
    if match:
        return RaceKey("ID", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^DISTRICT (\d{1,2}) SENATOR$", office_name)
    if match:
        return RaceKey("ID", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = re.match(r"^DISTRICT (\d{1,2}) REPRESENTATIVE ([AB])$", office_name)
    if match:
        return RaceKey("ID", Level.STATE, "HD", f"{int(match.group(1)):02d}{match.group(2)}", special)
    if office_name in ("PRESIDENT", "PRESIDENT AND VICE PRESIDENT", "PRESIDENTIAL ELECTORS"):
        return c.local_key("ID", "us-president")
    if office_name in _STATEWIDE:
        return RaceKey("ID", Level.GOVERNOR, _STATEWIDE[office_name], None, special)
    if office_name == "UNITED STATES SENATOR":
        return RaceKey("ID", Level.SENATE, "SEN", None, special)
    return c.local_key("ID", office_name)


def parse_id(html: str, *, fetched_at: datetime, url: str | None = None) -> list[RaceResult]:
    """Pure parse of one archive page (headings + reactable JSON)."""
    results: list[RaceResult] = []
    seen: set[str] = set()
    heads = list(_HEADING.finditer(html or ""))
    for i, match in enumerate(heads):
        heading = _text(match.group(2))
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(html)
        contest = None
        for blob in _SCRIPT.finditer(html, match.end(), stop):
            try:
                payload = json.loads(blob.group(1))
                contest = payload["x"]["tag"]["attribs"]["data"]
            except (ValueError, KeyError, TypeError):
                continue
            if isinstance(contest, dict) and contest.get("ContestantName"):
                break
            contest = None
        if not contest:
            continue
        cands: list[CandidateResult] = []
        for name, votes in zip(contest.get("ContestantName") or [], contest.get("TotalVotes") or []):
            label = (name or "").strip()
            if not label or "write-in" in label.lower():
                continue
            party = None
            pm = _PARTY.search(label)
            if pm:
                party = c.norm_party(pm.group(1))
                label = label[: pm.start()].strip()
            cands.append(CandidateResult(name=label, party=party, votes=c.to_int(votes)))
        if not cands:
            continue
        key = race_key(heading)
        if str(key) in seen:
            continue
        seen.add(str(key))
        results.append(
            c.make_result(
                key,
                heading,
                cands,
                fetched_at,
                source=IdSos.name,
                url=url,
                total_votes=sum(candidate.votes for candidate in cands),
            )
        )
    return results


def archive_index(date_iso: str) -> str | None:
    """Index URL of the archive for `date_iso`, or None if that election has no kind."""
    day = date.fromisoformat(date_iso)
    kind = KINDS.get(day.month)
    if not kind:
        return None
    return INDEX.format(year=day.year, kind=kind)


def page_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(filename, absolute URL) for the results pages linked from an archive index."""
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in HTMLParser(html or "").css("a"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href.endswith(".html"):
            continue
        name = href.rsplit("/", 1)[-1]
        if name in SKIP_PAGES or name in seen:
            continue
        seen.add(name)
        links.append((name, urljoin(base_url, href)))
    return links


def _needed_files(keys: list[RaceKey]) -> set[str] | None:
    """Archive pages that can hold the requested races (None = no restriction)."""
    need: set[str] = set()
    for key in keys:
        if key.level is Level.HOUSE:
            need.add("congressional.html")
        elif key.level is Level.STATE:
            need.add("legislative.html")
        elif key.level in (Level.SENATE, Level.GOVERNOR):
            return None  # statewide offices: depends on the year's page names
    return need or None


async def _pages(client: httpx.AsyncClient, date_iso: str) -> list[tuple[str, str]]:
    index = archive_index(date_iso)
    if not index:
        raise SourceUnavailable(f"ID: no archive kind known for {date_iso}")
    try:
        page = await c.get_text(client, index)
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"ID: the SOS archive has nothing for {date_iso} yet ({exc})") from exc
    links = page_links(page, index)
    if not links:
        raise SourceUnavailable(f"ID: {index} lists no results pages")
    return links


async def _all_races(
    client: httpx.AsyncClient, date_iso: str, fetched_at: datetime, keys: list[RaceKey] | None = None
) -> list[RaceResult]:
    wanted = _needed_files(keys) if keys else None
    results: list[RaceResult] = []
    seen: set[str] = set()
    for name, url in await _pages(client, date_iso):
        if wanted is not None and name not in wanted:
            continue
        html = await c.get_text(client, url)
        for race_result in parse_id(html, fetched_at=fetched_at, url=url):
            if str(race_result.key) in seen:
                continue
            seen.add(str(race_result.key))
            results.append(race_result)
    return results


@register
class IdSos(ResultsSource):
    name = "id_sos"
    states = frozenset({"ID"})
    scrape = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        id_keys = [key for key in keys if key.state == "ID"]
        if not id_keys:
            return []
        date_iso = c.election_date()
        return c.select(await _all_races(client, date_iso, c.utcnow(), id_keys), id_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "ID":
            return []
        return [
            (race_result.key, race_result.title or str(race_result.key))
            for race_result in await _all_races(client, c.election_date(), c.utcnow())
        ]
