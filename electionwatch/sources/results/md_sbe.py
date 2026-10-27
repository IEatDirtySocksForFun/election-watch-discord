"""Maryland — State Board of Elections statewide breakdown files (elections.maryland.gov).

Maryland publishes its certified results as two CSVs per election, linked from the archive
page for that election:

    https://elections.maryland.gov/elections/archive/2024/election_data
      PG24_CongressionalBreakDown.csv     votes split by congressional district
      PG24_LegislativeBreakDown.csv       votes split by legislative district

Both files carry the same rows; they differ only in which geographic partition the vote
columns use:

    "Vote Type (FOR=1,AGAINST=2)","County","County Name","Office Name","Office District",
    "Candidate Name","Party","Winner","Write-In?",
    "Congressional District 1",...,"Congressional District 8"

    "1","00","State of Maryland","President - Vice Pres","",
     "Kamala D. Harris and Tim Walz","DEM","Y","","171555","227194",...

Rows with County "00" are the State of Maryland aggregates for the contests that are
statewide (President, U.S. Senator, U.S. Congress, court retention); county rows carry the
county-level contests and the legislative districts. Because the trailing columns are a
geographic partition of the state, summing the columns of a row gives that contest's
statewide total, and for a district contest the total of the one district it belongs to -
which is how the parser reads both files (congressional partition for statewide/House
contests, legislative partition for State Senate / House of Delegates).

Caveat recorded in docs/results/states-3.md: these files are published with the certified
results, not on election night, and Maryland publishes no JSON feed.

Trimming note: Maryland has no state legislative election in 2024 (four-year terms), so the
legislative partition path is exercised from the same file's columns and covered by unit
tests rather than a 2024 fixture row.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

ARCHIVE = "https://elections.maryland.gov/elections/archive/{year}/election_data"
HUMAN = "https://elections.maryland.gov/elections/archive/{year}/election_data"
CONGRESSIONAL = "_CongressionalBreakDown.csv"
LEGISLATIVE = "_LegislativeBreakDown.csv"

_PARTITION = re.compile(r"^(Congressional|Legislative) District ", re.I)

_STATEWIDE = (
    ("GOVERNOR", "GOV"),
    ("LIEUTENANT GOVERNOR", "LTGOV"),
    ("ATTORNEY GENERAL", "AG"),
    ("SECRETARY OF STATE", "SOS"),
    ("COMPTROLLER", "COMP"),
    ("TREASURER", "TREAS"),
    ("SUPERINTENDENT OF PUBLIC INSTRUCTION", "SUPT"),
)

#: Maryland's own party labels that _common.norm_party leaves alone.
_PARTIES = {"OTC": "OTH", "JUD": None, "NON": "NPA", "UNA": "NPA"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ").strip()).upper()


def _district(text: str) -> str | None:
    """'01' -> '01'; '1A' -> '1A'."""
    raw = (text or "").strip().upper()
    m = re.fullmatch(r"0*(\d{1,2})\s*([A-Z])?", raw)
    if not m:
        return None
    return f"{int(m.group(1)):02d}" + (m.group(2) or "")


def race_key(office: str, district: str | None = None) -> RaceKey:
    """Map a Maryland "Office Name" (+ "Office District") to a RaceKey."""
    n = _norm(office)
    special = c.is_special(n)
    if n.startswith("PRESIDENT"):
        return RaceKey("MD", Level.LOCAL, "LOCAL", "us-president")
    if n in ("U.S. SENATOR", "US SENATOR", "UNITED STATES SENATOR"):
        return RaceKey("MD", Level.SENATE, "SEN", None, special)
    if n in ("U.S. CONGRESS", "US CONGRESS", "REPRESENTATIVE IN CONGRESS"):
        return RaceKey("MD", Level.HOUSE, "HOUSE", _district(district) or "00", special)
    for prefix, code in _STATEWIDE:
        if n.startswith(prefix):
            return RaceKey("MD", Level.GOVERNOR, code, None, special)
    if n.startswith("STATE SENATOR"):
        return RaceKey("MD", Level.STATE, "SD", _district(district) or "00", special)
    if n.startswith(("HOUSE OF DELEGATES", "STATE DELEGATE")):
        return RaceKey("MD", Level.STATE, "HD", _district(district) or "00", special)
    return c.local_key("MD", f"{n} {district or ''}")


def _rows(text: str) -> list[dict[str, str]]:
    return [r for r in csv.DictReader(io.StringIO(text or "")) if r.get("Office Name")]


def _partition(row: dict[str, str], key: RaceKey) -> int:
    """Votes of one row, read through the partition it belongs to."""
    if key.level is Level.HOUSE and key.district:
        wanted = f"CONGRESSIONAL DISTRICT {int(key.district)}"
        total = sum(c.to_int(v) for col, v in row.items()
                    if col and _PARTITION.match(col) and col.upper() == wanted)
        if total:
            return total
    if key.level is Level.STATE and key.office == "SD":
        wanted = f"LEGISLATIVE DISTRICT {int(key.district)}"
        total = sum(c.to_int(v) for col, v in row.items()
                    if col and col.upper() == wanted)
        if total:
            return total
    if key.level is Level.STATE and key.office == "HD" and key.district:
        wanted = f"LEGISLATIVE DISTRICT {key.district.upper()}"
        total = sum(c.to_int(v) for col, v in row.items() if col and col.upper() == wanted)
        if total:
            return total
    return sum(c.to_int(v) for col, v in row.items() if col and _PARTITION.match(col))


def parse_md(
    congressional: str,
    legislative: str | None,
    *,
    fetched_at: datetime,
    url: str | None = None,
) -> list[RaceResult]:
    """Pure parse of the two breakdown CSVs (the legislative one is optional)."""
    tallies: dict[str, dict] = {}

    def add(text: str, statewide_only: bool) -> None:
        for row in _rows(text):
            is_state_row = str(row.get("County") or "").strip() == "00"
            if statewide_only and not is_state_row:
                continue
            if not statewide_only and is_state_row:
                continue  # the same state rows are in both files; count them once
            office = str(row.get("Office Name") or "").strip()
            district = str(row.get("Office District") or "").strip()
            key = race_key(office, district)
            if statewide_only and key.level is Level.STATE:
                continue  # legislative rows live in the county rows of the other file
            name = str(row.get("Candidate Name") or "").strip()
            if not name or name.lower().startswith("other write-in"):
                continue
            slot = tallies.setdefault(str(key), {
                "key": key, "office": office, "district": district, "cands": {}})
            tally = slot["cands"]
            raw_party = re.sub(r"[^A-Za-z]", "", str(row.get("Party") or "")).upper()
            party = _PARTIES[raw_party] if raw_party in _PARTIES else c.norm_party(row.get("Party"))
            entry = tally.setdefault((name, party), {"votes": 0, "winner": False})
            entry["votes"] += _partition(row, key)
            if str(row.get("Winner") or "").strip().upper() == "Y":
                entry["winner"] = True

    add(congressional, statewide_only=True)
    if legislative:
        add(legislative, statewide_only=False)

    out: list[RaceResult] = []
    for slot in tallies.values():
        key = slot["key"]
        title = slot["office"]
        if slot["district"]:
            title = f"{title} District {slot['district']}"
        cands = [
            CandidateResult(name=name, party=party, votes=int(e["votes"]), winner=e["winner"])
            for (name, party), e in slot["cands"].items()
        ]
        out.append(c.make_result(
            key, title, cands, fetched_at,
            source=MdSbe.name,
            url=url,
            total_votes=sum(x.votes for x in cands),
        ))
    return out


async def _files(client: httpx.AsyncClient, date: str) -> tuple[str, str | None]:
    """The two CSV URLs for the tracked election, discovered from the archive page."""
    year = date[:4]
    page = ARCHIVE.format(year=year)
    html = await c.get_text(client, page)
    found = {}
    for href in re.findall(r'href="([^"]*BreakDown\.csv)"', html or "", re.I):
        url = href if href.startswith("http") else f"{page}/{href.rsplit('/', 1)[-1]}"
        if url.lower().endswith(CONGRESSIONAL.lower()):
            found["congressional"] = url
        elif url.lower().endswith(LEGISLATIVE.lower()):
            found["legislative"] = url
    if "congressional" not in found:
        raise SourceUnavailable(f"MD: no *{CONGRESSIONAL} link on {page}")
    return found["congressional"], found.get("legislative")


@register
class MdSbe(ResultsSource):
    name = "md_sbe"
    states = frozenset({"MD"})
    scrape = False  # the results themselves are CSV files; discovery reads the archive page

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "MD" for k in keys):
            return []
        url, legislative_url = await _files(client, c.election_date())
        text = await c.get_text(client, url)
        legislative = await c.get_text(client, legislative_url) if legislative_url else None
        parsed = parse_md(text, legislative, fetched_at=c.utcnow(),
                          url=ARCHIVE.format(year=c.election_date()[:4]))
        return c.select(parsed, [k for k in keys if k.state == "MD"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MD":
            return []
        url, legislative_url = await _files(client, c.election_date())
        text = await c.get_text(client, url)
        legislative = await c.get_text(client, legislative_url) if legislative_url else None
        return [(r.key, r.title or str(r.key))
                for r in parse_md(text, legislative, fetched_at=c.utcnow())]
