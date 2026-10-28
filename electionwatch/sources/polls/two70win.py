"""270toWin poll-list adapter (verified 2026-10-27).

The "Latest <year> <office> Election Polls" pages ($270towin.com/polls/...) are
rendered client-side from ``/polls/php/get-polls-list-v2.php``, which returns a
server-rendered HTML table fragment (no JSON, no API key). One request per
office returns the 40 most recent general-election polls nationwide, with the
candidate's party as a ``fill-D`` / ``fill-R`` / ``fill-I`` class on the row.

Notes:
- only ``limit=40`` is accepted by the endpoint (other values 404);
- candidate cells hold surnames as the site prints them ("Collins *" for the
  incumbent), fieldwork dates are the site's single date column;
- 270toWin lists no sponsor / partisan marker in this fragment, so ``sponsor``
  is None and ``partisan`` stays None (see the Wikipedia adapter for internals).

It is an HTML scrape of a site without a public API, so it is disabled by
default.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import Level, Poll, PollResult, RaceKey, normalize_party
from electionwatch.sources.base import PollSource, SourceUnavailable, register

from ._common import (
    clean_name,
    node_text,
    parse_date_range,
    parse_percent,
    parse_sample,
    state_code,
)

log = logging.getLogger(__name__)

YEAR = 2026  # this bot tracks the Nov 3 2026 midterms
API_URL = "https://www.270towin.com/polls/php/get-polls-list-v2.php"
USER_AGENT = "election-watch-discord/0.1 (polling bot; +https://github.com/election-watch-discord)"

_OFFICES = {Level.SENATE: "S", Level.GOVERNOR: "G", Level.HOUSE: "H"}
_AT_LARGE = frozenset({"AK", "DE", "ND", "SD", "VT", "WY"})
_FILL = re.compile(r"fill-([A-Za-z]+)")
_LOCATION = re.compile(
    rf"/{YEAR}-(?P<office>senate|governor|house)-polls/(?P<state>[a-z0-9\-]+)"
    r"(?:/district-(?P<district>\d+))?/?$"
)
_OFFICE_OF = {"senate": Level.SENATE, "governor": Level.GOVERNOR, "house": Level.HOUSE}


def _children(node):
    """Direct element children, in document order (selectolax only offers descendants)."""
    child = node.child
    while child is not None:
        if child.tag != "-text":
            yield child
        child = child.next


def _parse_row(row, level: Level) -> Poll | None:
    cells = list(_children(row))
    if len(cells) < 5:
        return None
    start_date, end_date = parse_date_range(node_text(cells[0]))
    if end_date is None:
        return None
    if start_date == end_date:
        # The list has one date column (the site's fieldwork-end/publication day);
        # do not invent a one-day field period out of it.
        start_date = None
    if len(cells) > 1 and "general" not in node_text(cells[1]).casefold():
        return None  # primary / convention rows are not general-election polls

    source_cell, location_cell = cells[2], cells[3]
    link = source_cell.css_first("a")
    pollster = _pollster_name(source_cell, link)
    if not pollster:
        return None
    meta = source_cell.css_first("div.poll_metadata")
    sample, population = parse_sample(node_text(meta))
    url = link.attributes.get("href") if link is not None else None

    location = location_cell.css_first("a")
    if location is None:
        return None
    href = location.attributes.get("href") or ""
    match = _LOCATION.search(href)
    if not match:
        return None
    if _OFFICE_OF[match.group("office")] is not level:
        return None  # a row for a different office than the list we asked for
    state = state_code(match.group("state").replace("-", " ")) or state_code(node_text(location))
    if state is None:
        return None
    district: str | None = None
    if level is Level.HOUSE:
        raw = match.group("district")
        if raw:
            district = f"{int(raw):02d}"
        elif state in _AT_LARGE:
            district = "AL"
        else:
            return None

    results: list[PollResult] = []
    for cand_row in row.css("tr[data-candidate-id]"):
        tds = list(_children(cand_row))
        if len(tds) < 2:
            continue
        party = None
        fm = _FILL.search(cand_row.attributes.get("class") or "")
        if fm:
            party = normalize_party(fm.group(1))
        name = clean_name(node_text(tds[0]))
        pct = parse_percent(node_text(tds[1]))
        if not name or pct is None:
            continue
        results.append(PollResult(name=name, party=party, pct=pct))
    if not results:
        return None

    key = RaceKey(
        state=state,
        level=level,
        office={Level.SENATE: "SEN", Level.GOVERNOR: "GOV", Level.HOUSE: "HOUSE"}[level],
        district=district,
    )
    return Poll(
        key=key,
        pollster=pollster,
        results=results,
        end_date=end_date,
        start_date=start_date,
        sample_size=sample,
        population=population,
        sponsor=None,
        partisan=None,
        source="270towin",
        url=url,
    )


def _pollster_name(source_cell, link) -> str:
    """Pollster name from the source cell; the link text is cleaner than flattened text."""
    if link is not None:
        return clean_name(node_text(link))
    text = node_text(source_cell)
    meta = source_cell.css_first("div.poll_metadata")
    if meta is not None and meta.text() and meta.text() in text:
        text = text[: text.find(meta.text())]
    return clean_name(text)


def parse_270towin_polls(html: str, level: Level, keys: list[RaceKey]) -> list[Poll]:
    """Parse one office's poll-list fragment and keep the races in ``keys``."""
    wanted = {race_key for race_key in keys if race_key.level is level}
    if not wanted:
        return []
    index = {(race_key.state, race_key.district): race_key for race_key in wanted}
    tree = HTMLParser(html or "")
    polls: list[Poll] = []
    for row in tree.css("table#polls-list tbody tr[data-poll-id]"):
        poll = _parse_row(row, level)
        if poll is None:
            continue
        key = index.get((poll.key.state, poll.key.district))
        if key is None:
            continue
        poll.key = key
        polls.append(poll)
    polls.sort(key=lambda poll: (poll.end_date, poll.pollster), reverse=True)
    return polls


@register
class Two70toWinPollSource(PollSource):
    """Recent general-election polls from 270toWin's poll-list fragment."""

    name = "270towin"
    scrape = True
    enabled_by_default = False

    async def fetch_polls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[Poll]:
        if not keys:
            return []
        polls: list[Poll] = []
        errors = 0
        attempted = 0
        for level, code in _OFFICES.items():
            if not any(key.level is level for key in keys):
                continue
            attempted += 1
            params = {
                "election_year": YEAR,
                "election_types[]": [code],
                "election_subtypes[]": ["PE", "PR", "GE", "GR"],
                "limit": 40,
            }
            try:
                resp = await client.get(API_URL, params=params, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as exc:
                log.warning("270towin: %s list failed: %s", level.value, exc)
                errors += 1
                continue
            if resp.status_code >= 400:
                log.warning("270towin: %s list HTTP %s", level.value, resp.status_code)
                errors += 1
                continue
            polls.extend(parse_270towin_polls(resp.text, level, keys))
        if attempted and errors == attempted:
            raise SourceUnavailable("270towin: every poll list request failed")
        polls.sort(key=lambda poll: (poll.end_date, poll.pollster), reverse=True)
        log.info("270towin: %d polls for %d keys", len(polls), len(keys))
        return polls
