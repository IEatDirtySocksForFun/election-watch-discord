"""North Dakota — Secretary of State results site (results.sos.nd.gov).

North Dakota runs the "Results SW" ASP.NET application, one URL per contest group, each a
server-rendered page:

    https://results.sos.nd.gov/resultsSW.aspx?text=All&type=SW&map=CTY   statewide contests
    https://results.sos.nd.gov/resultsSW.aspx?text=All&type=LG&map=CTY   legislative districts
    (type=JD judicial, type=CIALL county initiatives, ... - not tracked)

The same URLs serve whatever election the state is currently counting; on 2026-10-27 they
served the 2024 general election, which is what the fixture records.

One block per contest inside `<div class="wrapper-inside wrapper-border">`:

    <div class="display-results-box-a">
      <h1>United States Senator </h1>
      <h2>Vote For 1</h2>
    </div>
    <div class="display-results-box-b">
      <div class="precinct-fully">Precincts Fully: 385 / 385</div>
      <div class="precinct-partial">Partially: 0 / 385</div>
    </div>

    <div class="section group winner">                 <- 'winner' marks the elected candidate
      <div class="col display-results-box-d"><h2>Trump and Vance</h2>
        <img class="winner-icon" ...></div>
      <div class="display-results-row">
        <div class="display-results-box-e"><div class="Republican">Republican</div></div>
        <h2 class="display-results-box-g">246,505</h2>
        <h2 class="display-results-box-f">66.96%</h2>
      </div>
    </div>

Ballot measures ("Related to ...") use the same blocks with Yes/No rows and no party div.
The pages carry no "last updated" stamp, so `source_updated_at` stays None.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

URL = "https://results.sos.nd.gov/resultsSW.aspx?text=All&type={}&map=CTY"
PAGES = ("SW", "LG")
HUMAN = "https://results.sos.nd.gov/"

_PRECINCTS = re.compile(r"Precincts\s+Fully:\s*([\d,]+)\s*/\s*([\d,]+)", re.I)
_DISTRICT = re.compile(r"DISTRICT\s*0*(\d{1,2})\s*([A-Z])?", re.I)

_PARTIES = {
    "REPUBLICAN": "REP", "DEMOCRATICNPL": "DEM", "DEMOCRATIC": "DEM", "DEM": "DEM",
    "LIBERTARIAN": "LIB", "INDEPENDENT": "IND", "INDEPENDENTNOMINATION": "IND",
    "NONPARTISAN": "NPA", "NOPARTY": "NPA", "CONSTITUTION": "CON", "GREEN": "GRN",
    "WRITEIN": "WRI", "OTHER": "OTH",
}

_STATEWIDE = (
    ("GOVERNOR AND LT", "GOV"),
    ("LIEUTENANT GOVERNOR", "LTGOV"),
    ("ATTORNEY GENERAL", "AG"),
    ("SECRETARY OF STATE", "SOS"),
    ("STATE TREASURER", "TREAS"),
    ("STATE AUDITOR", "AUD"),
    ("SUPERINTENDENT OF PUBLIC INSTRUCTION", "SUPT"),
    ("INSURANCE COMMISSIONER", "INSCOM"),
    ("AGRICULTURE COMMISSIONER", "AGCOM"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _district(text: str) -> str | None:
    """'State Representative District 04a' -> '04A'."""
    m = _DISTRICT.search(text or "")
    if not m:
        return None
    return f"{int(m.group(1)):02d}" + (m.group(2) or "").upper()


def race_key(title: str) -> RaceKey:
    """Map a North Dakota contest heading to a RaceKey."""
    n = _norm(title)
    special = c.is_special(n)
    if n.startswith("PRESIDENT") or "VICE-PRESIDENT" in n:
        return RaceKey("ND", Level.LOCAL, "LOCAL", "us-president")
    if n.startswith("UNITED STATES SENATOR"):
        return RaceKey("ND", Level.SENATE, "SEN", None, special)
    if "REPRESENTATIVE IN CONGRESS" in n:
        return RaceKey("ND", Level.HOUSE, "HOUSE", "AL", special)  # one at-large seat
    for prefix, office in _STATEWIDE:
        if n.startswith(prefix):
            return RaceKey("ND", Level.GOVERNOR, office, None, special)
    if n.startswith("STATE SENATOR"):
        return RaceKey("ND", Level.STATE, "SD", _district(n) or "00", special)
    if n.startswith("STATE REPRESENTATIVE"):
        return RaceKey("ND", Level.STATE, "HD", _district(n) or "00", special)
    return c.local_key("ND", n)


def _party(node) -> str | None:
    if node is None:
        return None
    text = (node.text() or "").strip()
    if not text:
        return None
    return _PARTIES.get(re.sub(r"[^A-Za-z]", "", text).upper(), c.norm_party(text))


def parse_nd(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one resultsSW.aspx contest page."""
    doc = HTMLParser(html or "")
    out: list[RaceResult] = []
    seen: set[str] = set()
    for contest in doc.css("div.wrapper-inside.wrapper-border"):
        heading = contest.css_first("h1")
        title = re.sub(r"\s+", " ", (heading.text() if heading is not None else "").strip())
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        reporting = total = None
        m = _PRECINCTS.search(contest.text(separator=" ", strip=True))
        if m:
            reporting, total = c.to_int(m.group(1)), c.to_int(m.group(2))
        cands: list[CandidateResult] = []
        for row in contest.css("div.section.group"):
            name_node = row.css_first("div.display-results-box-d h2")
            if name_node is None:  # "Vote For 1" style headers
                continue
            name = re.sub(r"\s+", " ", name_node.text()).strip()
            if not name:
                continue
            if re.fullmatch(r"write[- ]?in(\s+write[- ]?in)?", name, re.I):
                name = "Write-in"
            votes_node = row.css_first("h2.display-results-box-g")
            pct_node = row.css_first("h2.display-results-box-f")
            cands.append(CandidateResult(
                name=name,
                party=_party(row.css_first("div.display-results-box-e div")),
                votes=c.to_int(votes_node.text() if votes_node is not None else None),
                pct=c.to_pct(pct_node.text() if pct_node is not None else None),
                winner=row.css_first("img.winner-icon") is not None,
            ))
        if not cands:
            continue
        seen.add(str(key))
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=NdSos.name,
            url=HUMAN,
            precincts_reporting=reporting,
            precincts_total=total,
        ))
    return out


@register
class NdSos(ResultsSource):
    name = "nd_sos"
    states = frozenset({"ND"})
    scrape = True  # ASP.NET HTML pages, no JSON feed
    enabled_by_default = True  # the state's own official results site

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "ND" for k in keys):
            return []
        results: list[RaceResult] = []
        pages = 0
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page))
            except SourceUnavailable:
                continue
            pages += 1
            results.extend(parse_nd(html, fetched_at=c.utcnow()))
        if not pages:
            raise SourceUnavailable(f"ND: no results page fetched ({URL.format('SW')})")
        return c.select(results, [k for k in keys if k.state == "ND"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "ND":
            return []
        seen: dict[str, tuple[RaceKey, str]] = {}
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page))
            except SourceUnavailable:
                continue
            for r in parse_nd(html, fetched_at=c.utcnow()):
                seen.setdefault(str(r.key), (r.key, r.title or str(r.key)))
        return list(seen.values())
