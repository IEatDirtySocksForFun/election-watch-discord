"""South Dakota — Secretary of State media results feed (electionresults.sd.gov).

South Dakota's ENR site is the same ASP.NET family as ND/NM/MT, but it also publishes a
plain-text media feed, which is what the state's own "Results Media" page links:

    https://electionresults.sd.gov/ResultsMedia.aspx                       media index
    https://electionresults.sd.gov/resultsCSV.aspx?text=All&type=SWR&map=CTY  statewide races
    ...&type=BQ    statewide ballot questions
    ...&type=LEG   legislative races          (map=DIST)
    ...&type=CTY   county races and ballot questions
    ...&type=MUN   local races and ballot questions

No auth, no JS, no cookies; Content-Type is `application/text` and the body is CSV, one row
per candidate per contest:

    RaceID,RaceName,PartyCode,AreaType,AreaNum,OfficeSeqNo,BallotOrder,CandidateID,
    CandidateName,CurrentDateTime,VoteFor,CandidateVotes,CandidatePercentage,PrecinctsReporting
    14412,"United States Senator","REP","","",110,,24967,"Mike  Rounds ",10/27/2026 4:02:28 PM,
      1,2132,0.50389978728433,="122/671",

- `CandidatePercentage` is a 0-1 fraction (`1` means uncontested / 100%).
- `PrecinctsReporting` is Excel-guarded text, `<reporting>/<total>` for that contest.
- `AreaNum` is the district for legislative races ("District 01") and the county or city for
  county/local races. Local contests reuse the same `RaceName` ("City Council Member") in
  every city, so contests are grouped by (RaceName, AreaNum).
- `VoteFor` is 2 for the two-seat South Dakota House districts; no winner flag is published.

The media page carries the election name/date plus turnout, and the site serves whichever
election it is currently reporting when `eid` is omitted. The adapter therefore omits `eid`
and refuses to report if the page's date is not the tracked election date; `SD_EID` pins an
archived election instead (684 = 2024 general, 651 = 2024 primary).
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://electionresults.sd.gov"
MEDIA = BASE + "/ResultsMedia.aspx"
CSV_URL = BASE + "/resultsCSV.aspx?text=All&type={}&map={}"
HUMAN = "https://electionresults.sd.gov/"
#: Contest groups the media feed publishes, with the map parameter each one needs.
PAGES = (("SWR", "CTY"), ("BQ", "CTY"), ("LEG", "DIST"), ("CTY", "CTY"), ("MUN", "CTY"))

_STATEWIDE = {
    "UNITED STATES SENATOR": "SEN",
    "UNITED STATES SENATE": "SEN",
    "US SENATOR": "SEN",
    "UNITED STATES REPRESENTATIVE": "HOUSE",
    "US REPRESENTATIVE": "HOUSE",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR": "GOV",
    "SECRETARY OF STATE": "SOS",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
    "STATE TREASURER": "TREAS",
    "COMMISSIONER OF SCHOOL AND PUBLIC LANDS": "LANDCOM",
}

_PRECINCTS = re.compile(r"^\s*[=]?\"?\s*([\d,]+)\s*/\s*([\d,]+)\s*\"?\s*$")
_UPDATED = re.compile(r"last\s+updated:?\s*([0-9/]+ [0-9:]+ ?[AP]M)", re.I)
_MEASURE = re.compile(
    r"^(CONSTITUTIONAL AMENDMENT|INITIATED MEASURE|INITIATED LAW|REFERRED LAW|"
    r"REFERRED MEASURE|REFERENDUM)\s+([A-Z]{1,3}|\d{1,3})\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _candidate_name(text: str) -> str:
    """The feed pads names with double spaces and a trailing blank ('Mike  Rounds ')."""
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _area(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _district(text: str) -> str | None:
    m = re.search(r"(\d{1,3})", text or "")
    return f"{int(m.group(1)):02d}" if m else None


def race_key(title: str, area: str = "") -> RaceKey:
    """Map a South Dakota contest heading (+ its AreaNum) to a RaceKey."""
    n = _norm(title)
    special = c.is_special(n)
    if n in _STATEWIDE:
        office = _STATEWIDE[n]
        if office == "SEN":
            return RaceKey("SD", Level.SENATE, "SEN", None, special)
        if office == "HOUSE":
            return RaceKey("SD", Level.HOUSE, "HOUSE", "AL", special)
        return RaceKey("SD", Level.GOVERNOR, office, None, special)
    if n in ("STATE SENATOR", "STATE SENATE"):
        return RaceKey("SD", Level.STATE, "SD", _district(area) or "00", special)
    if n in ("STATE REPRESENTATIVE", "STATE REPRESENTATIVE DISTRICT", "STATE HOUSE"):
        return RaceKey("SD", Level.STATE, "HD", _district(area) or "00", special)
    m = _MEASURE.match(n)
    if m:
        slug = c.slug(f"{m.group(1)} {m.group(2)}")
    else:
        # Local contests repeat one heading in every city: keep the area in the slug.
        slug = c.slug(f"{n} {area}" if area and _norm(area) not in n else n)
    return RaceKey("SD", Level.LOCAL, "LOCAL", slug)


def _precincts(value: str) -> tuple[int | None, int | None]:
    m = _PRECINCTS.match(value or "")
    if not m:
        return None, None
    return c.to_int(m.group(1)) or None, c.to_int(m.group(2)) or None


def media_info(html: str) -> dict:
    """Election date, turnout and last-updated stamp out of ResultsMedia.aspx."""
    doc = HTMLParser(html or "")
    values: dict[str, str] = {}
    for node in doc.css("input[id^=hid]"):
        values[node.attributes.get("id", "")] = (node.attributes.get("value") or "").strip()
    text = re.sub(r"\s+", " ", doc.text(separator=" ", strip=True))
    updated = _UPDATED.search(text)
    raw_date = values.get("hidElectionDate", "")
    try:
        date = c.iso_date(raw_date)
    except ValueError:
        date = None
    return {
        "date": date,
        "election_type": values.get("hidElectionType") or None,
        "registered_voters": c.to_int(values.get("hidVoterTotal")) or None,
        "ballots_cast": c.to_int(values.get("hidVoterTurnout")) or None,
        "updated_at": c.parse_updated_at(updated.group(1) if updated else None,
                                         tz="America/Chicago"),
    }


def _rows(text: str) -> list[dict]:
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return []
    return [row for row in csv.DictReader(io.StringIO(raw)) if row.get("RaceName")]


def parse_sd(
    csv_texts: list[str] | str,
    media_html: str | None = None,
    *,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of the media CSVs (+ the media page for turnout and the timestamp)."""
    texts = [csv_texts] if isinstance(csv_texts, str) else list(csv_texts)
    info = media_info(media_html) if media_html else {}
    out: list[RaceResult] = []
    seen: set[str] = set()
    groups: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for text in texts:
        for row in _rows(text):
            name = _candidate_name(row.get("RaceName") or "")
            area = _area(row.get("AreaNum") or "")
            group = (name, area)
            if group not in groups:
                groups[group] = []
                order.append(group)
            groups[group].append(row)

    for name, area in order:
        rows = groups[(name, area)]
        key = race_key(name, area)
        if str(key) in seen:
            continue
        cands: list[CandidateResult] = []
        for row in rows:
            cand_name = _candidate_name(row.get("CandidateName") or "")
            if not cand_name:
                continue
            cands.append(CandidateResult(
                name=cand_name,
                party=c.norm_party(row.get("PartyCode") or None),
                votes=c.to_int(row.get("CandidateVotes")),
                pct=c.to_pct(row.get("CandidatePercentage"), fraction=True),
                winner=False,  # the feed publishes no winner flag
            ))
        if not cands:
            continue
        seen.add(str(key))
        reporting, total = _precincts(rows[0].get("PrecinctsReporting") or "")
        title = name if not area or _norm(area) in _norm(name) else f"{name} ({area})"
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=SdSos.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in cands),
            precincts_reporting=reporting,
            precincts_total=total,
            registered_voters=info.get("registered_voters"),
            turnout_pct=(
                round(100.0 * info["ballots_cast"] / info["registered_voters"], 2)
                if info.get("ballots_cast") and info.get("registered_voters") else None),
            source_updated_at=info.get("updated_at"),
        ))
    return out


def _eid() -> str | None:
    return (os.environ.get("SD_EID") or "").strip() or None


def _with_eid(url: str, eid: str | None) -> str:
    return f"{url}&eid={eid}" if eid else url


@register
class SdSos(ResultsSource):
    name = "sd_sos"
    states = frozenset({"SD"})
    scrape = False  # the media feed is plain CSV

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "SD" for k in keys):
            return []
        eid = _eid()
        media = await c.get_text(client, _with_eid(MEDIA, eid))
        info = media_info(media)
        wanted_date = c.election_date()
        if info.get("date") and info["date"] != wanted_date and not eid:
            raise SourceUnavailable(
                f"SD: the site is reporting the {info['date']} election, not {wanted_date}; "
                f"set SD_EID to pin an archived election")
        texts: list[str] = []
        errors = 0
        for kind, mapping in PAGES:
            try:
                texts.append(await c.get_text(client, _with_eid(CSV_URL.format(kind, mapping), eid)))
            except SourceUnavailable:
                errors += 1
        if errors == len(PAGES):
            raise SourceUnavailable(f"SD: no media CSV fetched ({CSV_URL.format('SWR', 'CTY')})")
        parsed = parse_sd(texts, media, fetched_at=c.utcnow())
        return c.select(parsed, [k for k in keys if k.state == "SD"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "SD":
            return []
        eid = _eid()
        media = await c.get_text(client, _with_eid(MEDIA, eid))
        texts = []
        for kind, mapping in PAGES:
            try:
                texts.append(await c.get_text(client, _with_eid(CSV_URL.format(kind, mapping), eid)))
            except SourceUnavailable:
                continue
        return [(r.key, r.title or str(r.key))
                for r in parse_sd(texts, media, fetched_at=c.utcnow())]
