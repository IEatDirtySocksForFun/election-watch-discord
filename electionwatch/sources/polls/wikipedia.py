"""Wikipedia "opinion polling" adapter (verified 2026-10-27).

Each 2026 Senate / gubernatorial article carries a ``Polling`` table under its
``General election`` heading, with the pollster (plus "(R)"/"(D)" marker),
fieldwork window, sample/population, margin of error and a column per
candidate. That makes Wikipedia the best free source for *internal* and
partisan-sponsored polls, which most aggregators drop.

Method: one MediaWiki ``action=parse`` call per article with
``prop=text|wikitext``. The HTML is parsed with selectolax (table structure),
the wikitext supplies the candidate -> party map (each party's primary section
lists its candidates) because the poll columns themselves only name people.

It is a table scrape of a wiki page, so the adapter is disabled by default.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import Level, Poll, PollResult, RaceKey, normalize_party
from electionwatch.racekeys import STATES
from electionwatch.sources.base import PollSource, SourceUnavailable, register

from ._common import (
    clean_name,
    node_text,
    norm_space,
    parse_date_range,
    parse_percent,
    parse_sample,
    split_party_marker,
    strip_refs,
)

log = logging.getLogger(__name__)

YEAR = 2026
API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "election-watch-discord/0.1 (polling bot; +https://github.com/IEatDirtySocksForFun/election-watch-discord)"

PARAMS = {
    "action": "parse",
    "prop": "text|wikitext",
    "format": "json",
    "formatversion": "2",
    "redirects": "1",
    "disableeditsection": "1",
}

# Level-2 section of an article -> party of the candidates listed in it.
_SECTION_PARTY = {
    "republican primary": "REP",
    "democratic primary": "DEM",
    "libertarian primary": "LIB",
    "libertarian convention": "LIB",
    "green primary": "GRN",
    "green convention": "GRN",
    "constitution primary": "CON",
    "constitution convention": "CON",
    "u.s. taxpayers convention": "UST",
    "independents": "IND",
    "independent": "IND",
}

# Poll-table columns that are not candidates.
_META_COLUMNS = (
    "poll source",
    "source of poll",
    "date(s)",
    "dates administered",
    "date",
    "sample",
    "margin",
    "lead",
    "spread",
    "other",
    "others",
    "undecided",
    "unsure",
    "difference",
)


def article_titles(key: RaceKey) -> list[str]:
    """Candidate article titles for a race, most specific first."""
    name = STATES.get(key.state)
    if not name:
        return []
    if key.level is Level.SENATE:
        if key.special:
            return [
                f"{YEAR} United States Senate special election in {name}",
                f"{YEAR} United States Senate election in {name}",
            ]
        return [f"{YEAR} United States Senate election in {name}"]
    if key.level is Level.GOVERNOR and key.office == "GOV":
        return [f"{YEAR} {name} gubernatorial election"]
    return []


def article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


def _canon(name: str) -> str:
    n = norm_space(name).replace("''", "")
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)  # drop "(author, born 1960)" disambiguation
    return n.casefold()


_H2 = re.compile(r"^==\s*(?!=)(?P<title>.+?)\s*==\s*$", re.M)
_BULLET = re.compile(
    r"^\*+\s*(?:\[\[(?P<link>[^\]|]+)(?:\|(?P<label>[^\]]+))?\]\]"
    r"|(?P<plain>[A-Z][^,<(\[\]|]*))"
)


def candidate_parties(wikitext: str) -> tuple[dict[str, str], dict[str, tuple[str, int]]]:
    """Map candidate name -> party from an article's primary-section candidate lists.

    Returns ``(by_full_name, by_last_name)``; the last-name map carries a count so
    callers can ignore ambiguous surnames ("Brown" in a race with two Browns).
    """
    by_name: dict[str, str] = {}
    current: str | None = None
    for line in (wikitext or "").splitlines():
        heading = _H2.match(line)
        if heading:
            current = _SECTION_PARTY.get(norm_space(heading.group("title")).casefold())
            continue
        if current is None or not line.startswith("*"):
            continue
        bullet = _BULLET.match(line)
        if not bullet:
            continue
        raw = bullet.group("label") or bullet.group("link") or bullet.group("plain") or ""
        name = _canon(clean_name(raw))
        if name:
            by_name.setdefault(name, current)

    by_last: dict[str, tuple[str, int]] = {}
    for name, party in by_name.items():
        last = name.split()[-1]
        seen, count = by_last.get(last, (party, 0))
        by_last[last] = (seen, count + 1)
    return by_name, by_last


def _party_for(name: str, by_name: dict[str, str], by_last: dict[str, tuple[str, int]]) -> str | None:
    canon = _canon(name)
    if canon in by_name:
        return by_name[canon]
    parts = canon.split()
    if not parts:
        return None
    party, count = by_last.get(parts[-1], (None, 0))
    return party if count == 1 else None


def _is_meta(header: str) -> bool:
    low = header.casefold()
    return not low or any(token in low for token in _META_COLUMNS)


def _children(node):
    child = node.child
    while child is not None:
        if child.tag != "-text":
            yield child
        child = child.next


def _row_cells(tr) -> list:
    return [c for c in _children(tr) if c.tag in ("td", "th")]


def _parse_table(
    table, key: RaceKey, url: str, by_name: dict[str, str], by_last: dict[str, tuple[str, int]]
) -> list[Poll]:
    rows = table.css("tr")
    if not rows:
        return []
    header_at = None
    header_cells: list = []
    for i, tr in enumerate(rows):
        cells = _row_cells(tr)
        if cells and all(c.tag == "th" for c in cells):
            header_at, header_cells = i, cells
            break
    if header_at is None:
        return []
    headers = [norm_space(strip_refs(node_text(c))) for c in header_cells]
    first = headers[0].casefold()
    if "poll source" not in first:
        return []  # aggregate-polls / predictions / results tables
    if "source of poll" in first:
        return []

    candidate_cols = [i for i, h in enumerate(headers) if not _is_meta(h)]
    if not candidate_cols:
        return []
    sample_col = next((i for i, h in enumerate(headers) if "sample" in h.casefold()), None)

    polls: list[Poll] = []
    for tr in rows[header_at + 1 :]:
        cells = _row_cells(tr)
        if len(cells) != len(headers):
            continue  # separator row ("Republican primary runoff") or rowspan grouping
        raw_source = node_text(cells[0])
        markers = {normalize_party(m) for m in re.findall(r"\((R|D)\)", raw_source)}
        # A "(D)"/"(R)" pair means a bipartisan poll pair (e.g. Beacon/Shaw): neither side.
        partisan = markers.pop() if len(markers) == 1 else None
        pollster, trailing = split_party_marker(re.sub(r"\s*\((?:R|D)\)", "", raw_source))
        pollster = clean_name(pollster)
        if not pollster:
            continue
        start_date, end_date = parse_date_range(node_text(cells[1]))
        if end_date is None:
            continue
        sample, population = (None, None)
        if sample_col is not None:
            sample, population = parse_sample(node_text(cells[sample_col]))

        results: list[PollResult] = []
        for col in candidate_cols:
            pct = parse_percent(node_text(cells[col]))
            if pct is None:
                continue
            raw_name, header_marker = split_party_marker(headers[col])
            name = clean_name(raw_name)
            if not name:
                continue
            party = header_marker or _party_for(name, by_name, by_last)
            results.append(PollResult(name=name, party=normalize_party(party), pct=pct))
        if len(results) < 2:
            continue  # a one-candidate "poll" is a table artefact, not a poll
        polls.append(
            Poll(
                key=key,
                pollster=pollster,
                results=results,
                end_date=end_date,
                start_date=start_date if start_date != end_date else None,
                sample_size=sample,
                population=population,
                sponsor=None,
                partisan=partisan or normalize_party(trailing),
                source="wikipedia",
                url=url,
            )
        )
    return polls


def parse_wikipedia_polls(html: str, wikitext: str, key: RaceKey, url: str) -> list[Poll]:
    """All general-election poll rows of one article, newest first."""
    by_name, by_last = candidate_parties(wikitext)
    tree = HTMLParser(html or "")
    nodes = list(tree.root.traverse()) if tree.root is not None else []
    has_general = any(n.tag == "h2" and norm_space(n.text()).casefold() == "general election" for n in nodes)
    polls: list[Poll] = []
    section: str | None = None
    for node in nodes:
        if node.tag == "h2":
            section = norm_space(node.text()).casefold()
            continue
        if node.tag != "table":
            continue
        if has_general and section != "general election":
            continue
        if "wikitable" not in (node.attributes.get("class") or ""):
            continue
        polls.extend(_parse_table(node, key, url, by_name, by_last))
    polls.sort(key=lambda p: (p.end_date, p.pollster), reverse=True)
    return polls


@register
class WikipediaPollSource(PollSource):
    """Opinion-polling tables from 2026 state election articles."""

    name = "wikipedia"
    scrape = True
    enabled_by_default = False

    async def fetch_polls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[Poll]:
        if not keys:
            return []
        polls: list[Poll] = []
        attempted = errors = 0
        for key in keys:
            for title in article_titles(key):
                attempted += 1
                try:
                    resp = await client.get(
                        API_URL,
                        params={**PARAMS, "page": title},
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    )
                except httpx.HTTPError as exc:
                    errors += 1
                    log.warning("wikipedia: %s failed: %s", title, exc)
                    continue
                if resp.status_code >= 400:
                    errors += 1
                    log.warning("wikipedia: %s HTTP %s", title, resp.status_code)
                    continue
                data = resp.json()
                if "error" in data:
                    log.info("wikipedia: no article %r (%s)", title, data["error"].get("code"))
                    continue
                page = data.get("parse") or {}
                resolved = page.get("title") or title
                polls.extend(
                    parse_wikipedia_polls(
                        page.get("text") or "",
                        page.get("wikitext") or "",
                        key,
                        article_url(resolved),
                    )
                )
                break  # first existing title wins (special -> regular fallback)
        if attempted and errors == attempted:
            raise SourceUnavailable("wikipedia: every article request failed")
        log.info("wikipedia: %d polls for %d keys", len(polls), len(keys))
        return polls
