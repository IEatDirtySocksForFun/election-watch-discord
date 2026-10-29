"""Montana — Secretary of State election-night reporting (electionresults.mt.gov).

Montana runs the same ASP.NET "Results SW" family of pages as ND/NM, one URL per contest
group, always for a single election addressed by its numeric `eid`:

    https://electionresults.mt.gov/resultsSW.aspx?type=FED&map=CTY&eid=450002785
    type=STATE   statewide executive offices      type=SENATE  state senate
    type=HOUSE   state house                      type=PSC     public service commissioner
    type=SCJ     supreme court                    type=JUD     district court judges
    type=BQ      ballot issues                    type=RECOUNT possible recounts

Only the bare host is behind the Cloudflare challenge (`/` answers 403 `cf-mitigated:
challenge`), the per-election pages above answer 200 to a plain request — that is how the
2024 general (eid 450002785) and the 2026 primary (eid 450002928) can still be read.

Election ids are discovered from the Secretary of State's candidate-filing site, whose
election dropdown is the public list of the current cycle's elections and ids:

    https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx
    <option value="450002987">FEDERAL GENERAL 2026 (11/03/2026) (General)</option>

That id opens the matching results pages (450002987 -> "2026 General Election - November
3, 2026"). `MT_EID` pins an election instead, e.g. `MT_EID=450002785` for the 2024 general.

Contest block (unchanged from the ND/NM markup):

    <div class="wrapper-inside wrapper-border">
      <div class="display-results-box-a"><h1>UNITED STATES SENATOR</h1></div>
      <div class="precinct-fully">Precincts Fully: 727 / 727</div>
      <div class="section group">
        <div class="col display-results-box-d"><h1>TIM SHEEHY</h1>
          <h2 class="Republican">Republican</h2></div>
        <div class="col display-results-box-f"><h1>319,682</h1></div>
      </div>
      ...

The first `section group` of every contest is the column header and carries no name, so it
is skipped. The page marks no winner, publishes no percentages and pads some names with a
double space ("JENNIFER  FIELDER"); names are collapsed but otherwise kept verbatim, upper
case included, like the other vendor-family adapters. Precincts reporting is "Fully" plus
"Partially" (both are out of the same total). The header carries the election name/date,
statewide turnout and the "Results last updated: ... MT" stamp (America/Denver).
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

BASE = "https://electionresults.mt.gov"
URL = BASE + "/resultsSW.aspx?type={}&map=CTY&eid={}"
HUMAN = "https://electionresults.mt.gov/"
FILING = "https://candidatefiling.mt.gov/candidatefiling/CandidateList.aspx"
PAGES = ("FED", "STATE", "SENATE", "HOUSE", "PSC", "SCJ", "JUD", "BQ")

_PRECINCTS = re.compile(r"Precincts\s+Fully:\s*([\d,]+)\s*/\s*([\d,]+)", re.I)
_PARTIAL = re.compile(r"Partially:\s*([\d,]+)\s*/\s*([\d,]+)", re.I)
_DISTRICT = re.compile(r"DISTRICT\s*0*(\d{1,3})\b", re.I)
_CONGRESS = re.compile(r"UNITED STATES REPRESENTATIVE\s+(\d{1,2})(?:ST|ND|RD|TH)?\s+CONGRESSIONAL", re.I)
_MEASURE = re.compile(r"^([A-Z]{1,3}-\d{1,3})\b")
_RETENTION = re.compile(r"^SHALL JUDGE (.+?) OF THE", re.I)
_DOB = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
_TURNOUT = re.compile(r"Total Turnout\s+([\d,]+)\s+Registered Voters\s+([\d,]+)", re.I)
_OPTION = re.compile(r"<option[^>]*value=\"(\d+)\"[^>]*>([^<]+)</option>", re.I)
_OPTION_DATE = re.compile(r"\((\d{1,2}/\d{1,2}/\d{4})\)")

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
    "STATE TREASURER": "TREAS",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "STATE SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "COMMISSIONER OF INSURANCE": "INSCOM",
    "COMMISSIONER OF AGRICULTURE": "AGCOM",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _name(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _party(value: str | None) -> str | None:
    if value and re.sub(r"[^A-Za-z]", "", value).upper() == "NONPARTISAN":
        return "NP"  # Montana prints "Non-Partisan"; the model's code for that is NP
    return c.norm_party(value)


def _district(text: str) -> str | None:
    match = _DISTRICT.search(text or "")
    return f"{int(match.group(1)):02d}" if match else None


def race_key(title: str) -> RaceKey:
    """Map a Montana contest heading to a RaceKey."""
    office_name = _norm(title)
    special = c.is_special(office_name)
    if office_name.startswith("PRESIDENT"):
        return RaceKey("MT", Level.LOCAL, "LOCAL", "us-president")
    if office_name in ("UNITED STATES SENATOR", "US SENATOR", "UNITED STATES SENATE"):
        return RaceKey("MT", Level.SENATE, "SEN", None, special)
    match = _CONGRESS.search(office_name)
    if match:
        return RaceKey("MT", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    if office_name.startswith("GOVERNOR"):
        return RaceKey("MT", Level.GOVERNOR, "GOV", None, special)
    for prefix, code in _STATEWIDE.items():
        if prefix != "GOVERNOR" and office_name.startswith(prefix):
            return RaceKey("MT", Level.GOVERNOR, code, None, special)
    if office_name.startswith(("STATE SENATOR", "STATE SENATE")):
        return RaceKey("MT", Level.STATE, "SD", _district(office_name) or "00", special)
    if office_name.startswith(("STATE REPRESENTATIVE", "STATE HOUSE", "STATE REP")):
        return RaceKey("MT", Level.STATE, "HD", _district(office_name) or "00", special)
    match = _MEASURE.match(office_name)
    if match:
        return RaceKey("MT", Level.LOCAL, "LOCAL", c.slug(match.group(1)))
    match = _RETENTION.match(office_name)
    if match:
        return RaceKey("MT", Level.LOCAL, "LOCAL", c.slug(f"retain-judge {match.group(1)}"))
    return c.local_key("MT", office_name)


def page_info(html: str) -> dict:
    """Election date, statewide turnout and the last-updated stamp of one results page."""
    page = HTMLParser(html or "")
    head = page.css_first("div.header-box1")
    date = None
    if head is not None:
        match = _DOB.search(_name(head.text(separator=" ")))
        if match:
            try:
                date = datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
            except ValueError:
                date = None
    turnout_node = page.css_first("div.header-box2")
    registered = ballots = None
    if turnout_node is not None:
        match = _TURNOUT.search(_name(turnout_node.text(separator=" ")))
        if match:
            ballots, registered = c.to_int(match.group(1)), c.to_int(match.group(2))
    updated = None
    stamp = page.css_first("div.last-updated")
    if stamp is not None:
        raw = re.sub(r"^.*?last updated:?\s*", "", _name(stamp.text()), flags=re.I)
        raw = re.sub(r"\s+(?:M[DS]?T|UTC)$", "", raw, flags=re.I)
        updated = c.parse_updated_at(raw, tz="America/Denver")
    return {
        "date": date,
        "ballots_cast": ballots,
        "registered_voters": registered,
        "updated_at": updated,
    }


def parse_mt(html: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one resultsSW.aspx contest page."""
    overview = page_info(html)
    page = HTMLParser(html or "")
    results: list[RaceResult] = []
    seen: set[str] = set()
    turnout = (
        round(100.0 * overview["ballots_cast"] / overview["registered_voters"], 2)
        if overview.get("ballots_cast") and overview.get("registered_voters")
        else None
    )
    for contest in page.css("div.wrapper-inside.wrapper-border"):
        heading = contest.css_first("div.display-results-box-a h1")
        if heading is None:
            heading = contest.css_first("h1")
        title = _name(heading.text()) if heading is not None else ""
        if not title:
            continue
        key = race_key(title)
        if str(key) in seen:
            continue
        text = _name(contest.text(separator=" "))
        reporting = total = None
        match = _PRECINCTS.search(text)
        if match:
            reporting, total = c.to_int(match.group(1)), c.to_int(match.group(2))
            partial = _PARTIAL.search(text)
            if partial:
                reporting += c.to_int(partial.group(1))
        cands: list[CandidateResult] = []
        for row in contest.css("div.section.group"):
            name_node = row.css_first("div.display-results-box-d h1")
            if name_node is None:
                continue
            name = _name(name_node.text())
            if not name:
                continue  # the column header row
            party_node = row.css_first("div.display-results-box-d h2")
            votes_node = row.css_first("div.display-results-box-f h1")
            cands.append(
                CandidateResult(
                    name=name,
                    party=_party(party_node.text() if party_node is not None else None),
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
                source=MtSos.name,
                url=HUMAN,
                total_votes=sum(candidate.votes for candidate in cands),
                precincts_reporting=reporting,
                precincts_total=total,
                registered_voters=overview.get("registered_voters"),
                turnout_pct=turnout,
                source_updated_at=overview.get("updated_at"),
            )
        )
    return results


def discover_eid(html: str, date: str) -> str | None:
    """Election id for an ISO date out of the candidate-filing election dropdown."""
    target = c.us_date(date)
    for value, text in _OPTION.findall(html or ""):
        match = _OPTION_DATE.search(text)
        if match and match.group(1) == target:
            return value
    return None


def _pinned() -> str | None:
    return (os.environ.get("MT_EID") or "").strip() or None


@register
class MtSos(ResultsSource):
    name = "mt_sos"
    states = frozenset({"MT"})
    scrape = True  # HTML pages; the only machine-readable option Montana publishes
    enabled_by_default = True  # the state's own official election-night site

    async def _resolve(self, client: httpx.AsyncClient, date: str) -> tuple[str, bool]:
        """(election id, was pinned). Discovered from candidate filing unless pinned."""
        pinned = _pinned()
        if pinned:
            return pinned, True
        try:
            # The list lives on the page's default election (?e=<id>) and redirects there.
            resp = await client.get(FILING, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"MT: candidate filing list unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise SourceUnavailable(f"MT: {FILING} -> HTTP {resp.status_code}")
        eid = discover_eid(resp.text, date)
        if not eid:
            raise SourceUnavailable(f"MT: no election dated {date} in the candidate-filing list; set MT_EID")
        return eid, False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "MT" for key in keys):
            return []
        date = c.election_date()
        eid, pinned = await self._resolve(client, date)
        results: list[RaceResult] = []
        pages = 0
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page, eid))
            except SourceUnavailable:
                continue  # a group with no contests 302s to the error page
            seen_date = page_info(html).get("date")
            if seen_date and not pinned and seen_date != date:
                raise SourceUnavailable(f"MT: eid {eid} is the {seen_date} election, not {date}")
            parsed = parse_mt(html, fetched_at=c.utcnow())
            if not parsed:
                continue  # the election exists but this group has no contests (yet)
            pages += 1
            results.extend(parsed)
        if not pages:
            raise SourceUnavailable(f"MT: eid {eid} publishes no contests yet ({URL.format('FED', eid)})")
        return c.select(results, [key for key in keys if key.state == "MT"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MT":
            return []
        eid, _ = await self._resolve(client, c.election_date())
        seen: dict[str, tuple[RaceKey, str]] = {}
        for page in PAGES:
            try:
                html = await c.get_text(client, URL.format(page, eid))
            except SourceUnavailable:
                continue
            for race_result in parse_mt(html, fetched_at=c.utcnow()):
                seen.setdefault(str(race_result.key), (race_result.key, race_result.title or str(race_result.key)))
        return list(seen.values())
