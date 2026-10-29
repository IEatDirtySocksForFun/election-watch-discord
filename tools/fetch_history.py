#!/usr/bin/env python3
"""Download historical election results for replay testing.

    python tools/fetch_history.py [DATA_DIR] [--states PA,GA] [--years 2020,2024]

DATA_DIR defaults to ~/Downloads/election-test-data. Output layout:

    <data>/<year>/<ST>/<level>.csv     race_key,candidate,party,votes,county
    <data>/<year>/manifest.json        source URL + source name per (state, level)
    <data>/timelines/<year>/<KEY>.jsonl  real NYT election-night series (when published)
    <data>/_cache/...                  raw downloads (kept so reruns are offline + resumable)

<year> is 2014, 2016, 2018, 2020, 2022, 2024 (generals) or 2024-primary. <level> is one of
senate, house, governor (statewide executive), state (legislature), local.

Sources, in priority order per (year, state, level) -- the first one with rows wins:
  1. MIT MEDSL precinct returns (GitHub MEDSL/<year>-elections-official; 2020 is on Harvard
     Dataverse, whose files sit behind a guestbook form -> skipped and noted in the manifest),
     aggregated to county. Covers federal, statewide, legislature and much local.
  2. MIT MEDSL "U.S. Senate statewide 1976-2024" (Harvard Dataverse, no guestbook), county "".
  3. OpenElections (github.com/openelections/openelections-data-<st>) county or precinct CSVs.
Primaries come from OpenElections only.

Idempotent: raw files are cached, and a (year, state) already in the manifest is skipped
unless --force. Polite: descriptive User-Agent, >= 1 s between network requests, retries
with backoff. Set GITHUB_TOKEN to raise the GitHub API rate limit (never printed).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from electionwatch.models import pretty_name  # noqa: E402
from electionwatch.racekeys import STATES, RaceKeyError, parse  # noqa: E402

UA = "election-watch-discord/0.1 (history fetcher for replay tests; +https://github.com/IEatDirtySocksForFun/election-watch-discord)"
DEFAULT_DATA = Path.home() / "Downloads" / "election-test-data"
GENERAL_DATES = {
    "2014": "20141104",
    "2016": "20161108",
    "2018": "20181106",
    "2020": "20201103",
    "2022": "20221108",
    "2024": "20241105",
}
YEARS = ["2014", "2016", "2018", "2020", "2022", "2024", "2024-primary"]
LEVELS = ["senate", "house", "governor", "state", "local"]
FIELDS = ["race_key", "candidate", "party", "votes", "county"]

MEDSL_REPO = "MEDSL/{year}-elections-official"
DATAVERSE = "https://dataverse.harvard.edu"
DV_2020_PRECINCTS = "doi:10.7910/DVN/NT66Z3"  # Precinct-Level Returns 2020 by Individual State
DV_SENATE_STATE = "doi:10.7910/DVN/PEJ5QU"  # U.S. Senate statewide 1976-2024
DV_2016_PRECINCTS = "doi:10.7910/DVN/NH5S2I"  # 2016 Precinct-Level Election Results (guestbook)

# OpenElections trailing filename word -> how the repo splits a state up. Ranks 0-2 are a whole
# state in one file (county summary, no geo word, precinct dump): each complete alone, so only
# one is used. Ranks 3+ are one slice of the state (district, town, county, precinct) and every
# file of the chosen slice is aggregated, because each covers only part of the state.
OE_LEVELS = {
    "county": 0,
    "county-level": 0,
    "counties": 0,
    "district": 3,
    "districts": 3,
    "congressional": 3,
    "town": 4,
    "towns": 4,
    "municipal": 4,
    "municipalities": 4,
    "city": 4,
    "cities": 4,
    "county-ward": 5,
    "county-precinct": 5,
    "precinct": 6,
    "precincts": 6,
    "precinct-level": 6,
    "county-level-precinct": 6,
    "ward": 6,
    "wards": 6,
    "township": 6,
    "townships": 6,
}

# ---------------------------------------------------------------------------------------------
# normalisation (pure functions, unit-tested)

STATEWIDE_OFFICES = {
    "GOVERNOR": "GOV",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR AND LT GOVERNOR": "GOV",
    "GOVERNOR LT GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "LT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "ATTORNEY": "AG",
    "SECRETARY OF STATE": "SOS",
    "SECRETARY OF THE STATE": "SOS",
    "SECRETARY": "SOS",
    "STATE TREASURER": "TREAS",
    "TREASURER": "TREAS",
    "TREASURER OF STATE": "TREAS",
    "STATE AUDITOR": "AUD",
    "AUDITOR": "AUD",
    "AUDITOR OF STATE": "AUD",
    "AUDITOR GENERAL": "AUD",
    "STATE COMPTROLLER": "COMP",
    "COMPTROLLER": "COMP",
    "COMPTROLLER OF PUBLIC ACCOUNTS": "COMP",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "STATE SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "STATE SCHOOL SUPERINTENDENT": "SUPT",
    "SUPERINTENDENT OF EDUCATION": "SUPT",
    "COMMISSIONER OF AGRICULTURE": "AGCOM",
    "AGRICULTURE COMMISSIONER": "AGCOM",
    "COMMISSIONER OF AGRICULTURE AND INDUSTRIES": "AGCOM",
    "SECRETARY OF AGRICULTURE": "AGCOM",
    "INSURANCE COMMISSIONER": "INSCOM",
    "COMMISSIONER OF INSURANCE": "INSCOM",
    "COMMISSIONER OF THE GENERAL LAND OFFICE": "LANDCOM",
    "LAND COMMISSIONER": "LANDCOM",
    "COMMISSIONER OF PUBLIC LANDS": "LANDCOM",
    "RAILROAD COMMISSIONER": "RRC",
}
SENATE_OFFICES = {
    "US SENATE",
    "U S SENATE",
    "UNITED STATES SENATE",
    "US SENATOR",
    "U S SENATOR",
    "UNITED STATES SENATOR",
}
HOUSE_OFFICES = {
    "US HOUSE",
    "U S HOUSE",
    "US REPRESENTATIVE",
    "U S REPRESENTATIVE",
    "UNITED STATES REPRESENTATIVE",
    "REPRESENTATIVE IN CONGRESS",
    "US CONGRESS",
    "CONGRESS",
    "U S CONGRESS",
    "UNITED STATES HOUSE OF REPRESENTATIVES",
    "US HOUSE OF REPRESENTATIVES",
    "U S HOUSE OF REPRESENTATIVES",
}
STATE_SENATE = {
    "STATE SENATE",
    "STATE SENATOR",
    "STATE SENATORS",
    "SENATOR IN THE GENERAL ASSEMBLY",
    "SENATOR IN STATE LEGISLATURE",
}
STATE_HOUSE = {
    "STATE HOUSE",
    "STATE HOUSE OF DELEGATES",
    "STATE REPRESENTATIVE",
    "STATE REPRESENTATIVES",
    "STATE REP",
    "STATE REPS",
    "STATE ASSEMBLY",
    "ASSEMBLY",
    "HOUSE OF DELEGATES",
    "STATE HOUSE OF REPRESENTATIVES",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
    "MEMBER OF THE STATE ASSEMBLY",
    "HOUSE OF REPRESENTATIVES",
    "STATE DELEGATE",
    "REPRESENTATIVE IN STATE LEGISLATURE",
    "MEMBER OF THE STATE LEGISLATURE",
}
# A row whose office is just "House"/"Senate" is state legislature when it carries a legislative
# district (a U.S. race would have been tagged by its own office name or dataverse).
BARE_CHAMBERS = {"HOUSE": "HD", "SENATE": "SD"}
SKIP_OFFICE = re.compile(
    r"PRESIDENT|STRAIGHT|REGISTERED|BALLOTS CAST|TURNOUT|OVER ?VOTES|UNDER ?VOTES|"
    r"VOTERS|TOTAL VOTES|BLANK|SPOILED|REJECTED",
    re.I,
)
SKIP_CANDIDATE = re.compile(
    r"^(OVER ?VOTES?|UNDER ?VOTES?|TOTAL( VOTES)?|BLANK( VOTES)?|SPOILED|"
    r"VOID|REJECTED|SCATTER(ED|ING)?|WRITE[- ]?INS?( TOTALS?)?|(TOTAL )?WRITE[- ]IN VOTES)$",
    re.I,
)
AT_LARGE_STATES = {"AK", "DE", "ND", "SD", "VT", "WY"}
PARTIES = {
    "DEMOCRAT": "DEM",
    "DEMOCRATIC": "DEM",
    "DEM": "DEM",
    "D": "DEM",
    "REPUBLICAN": "REP",
    "REP": "REP",
    "R": "REP",
    "LIBERTARIAN": "LIB",
    "LIB": "LIB",
    "GREEN": "GRN",
    "GRN": "GRN",
    "INDEPENDENT": "IND",
    "IND": "IND",
    "GOP": "REP",
    "NONPARTISAN": "NPA",
    "NON-PARTISAN": "NPA",
    "NP": "NPA",
    "NPA": "NPA",
}


def norm_office(office: str) -> tuple[str, bool]:
    """'U.S. Senate (Special)' -> ('US SENATE', True)."""
    office_text = office.upper()
    special = bool(re.search(r"\bSPECIAL\b|UNEXPIRED|PARTIAL TERM", office_text))
    office_text = re.sub(r"\(.*?\)", " ", office_text)
    office_text = re.sub(r"\bPARTIAL TERM\b.*$", " ", office_text)  # MI "… Partial Term Ending 01/01/2017"
    office_text = re.sub(r"\b(SPECIAL|UNEXPIRED TERM|UNEXPIRED)\b", " ", office_text)
    office_text = re.sub(r"[^A-Z0-9 ]+", " ", office_text)
    office_text = re.sub(r"\bDISTRICT\b.*$", "", office_text)
    office_text = re.sub(r"\bFILES? IN\b.*$", " ", office_text)  # "… Representative Files In WAYNE County"
    office_text = re.sub(r"\bPOS(?:ITION)?\.?\s*\d{0,2}\b", " ", office_text)  # "State Representative Pos. 2"
    office_text = re.sub(r"\b\d+ ?YEAR TERM\b|\bTERM\b", " ", office_text)  # "State House Unexpired 2 Year Term"
    return re.sub(r"\s+", " ", office_text).strip(), special


NO_PARTY = {"NONE", "NO PARTY", "NO PARTY LISTED", "NO PARTY AFFILIATION", "NOPARTY"}


def norm_party(party_code: str | None) -> str:
    party_code = (party_code or "").strip().upper()
    if not party_code or party_code in NO_PARTY:
        return ""
    return PARTIES.get(party_code, party_code[:12])


def slug(*parts: str) -> str:
    joined = "-".join(part for part in parts if part)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")[:80] or "unknown"


def _district_num(district_text: str | None) -> str | None:
    district_text = re.sub(r"(\d)(ST|ND|RD|TH)\b", r"\1", (district_text or "").strip().upper())
    district_text = re.sub(r"^(?:CD|CONG(?:RESSIONAL)?\.?)\s*", "", district_text)  # 'CD3' (Connecticut)
    match = re.fullmatch(r"(?:(?:DISTRICT|DIST|DIST\.|NO\.?)\s*)?0*(\d{1,3})\s*([A-Z]?)", district_text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}{match.group(2)}"


def _office_district(office: str | None) -> str | None:
    """'U.S. House District 7' -> '07' (some sources leave the district column empty)."""
    match = DISTRICT_IN.search((office or "").upper())
    return _district_num(match.group(1)) if match else None


POSITION_IN = re.compile(r"\bPOS(?:ITION)?\.?\s*(\d{1,2})\b")
DISTRICT_IN = re.compile(r"\bDISTRICT\s+(?:NO\.?\s*)?(0*\d{1,3}[A-Z]?|[A-Z])\b")
LEAD_DISTRICT = re.compile(r"^\s*(0*\d{1,3})(?:ST|ND|RD|TH)?\s+DISTRICT\s+(.+)$", re.I)
LEG_TAIL = re.compile(
    r"^(?:(?:STATE|MEMBER OF THE STATE)\s+)?(?:HOUSE|SENATE|ASSEMBLY|DELEGATES?|"
    r"REPRESENTATIVES?|SENATORS?)\s+(0*\d{1,3}[A-Z]?|[A-Z])$"
)
NAMED_DISTRICT = re.compile(r"[A-Z]{2,4}(?:-(?:[A-Z]{1,4}|\d{1,3}[A-Z]?))*")


def _district_id(text: str | None) -> str | None:
    """'001, Position 1' -> '01', 'District 30' -> '30', '4a' -> '04A', 'B' -> 'B'."""
    cleaned = re.sub(r"[^A-Z0-9 ]+", " ", (text or "").upper())
    cleaned = POSITION_IN.sub(" ", cleaned)
    cleaned = re.sub(r"\b(?:DISTRICT|DIST|NO)\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    match = re.fullmatch(r"0*(\d{1,3})\s*([A-Z]?)", cleaned)
    if match:
        return f"{int(match.group(1)):02d}{match.group(2)}"
    match = re.fullmatch(r"([A-Z])", cleaned)
    return match.group(1) if match else None


def _named_district(text: str | None) -> str | None:
    """Vermont-style district names: 'CHT-6-7', 'ADD', 'ESS-CAL-ORL'."""
    cleaned = re.sub(r"[^A-Z0-9]+", "-", (text or "").upper()).strip("-")
    if _district_id(cleaned) or not NAMED_DISTRICT.fullmatch(cleaned):
        return None
    return cleaned


def _position(text: str | None) -> int | None:
    match = POSITION_IN.search((text or "").upper())
    return int(match.group(1)) if match else None


def _leg_district(office: str, district: str) -> str | None:
    """Legislative district from the district column, else from the office name.

    Sources put the district in either place: WA "State House" + "001, Position 1", CO
    "State Representatives, District 30", AK 2020 "House District 7", RI "Senate 25".
    """
    district_id = _district_id(district)
    if district_id:
        return district_id
    up = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", (office or "").upper())).strip()
    match = DISTRICT_IN.search(up)
    if match:
        return _district_id(match.group(1))
    match = LEG_TAIL.match(up)
    return _district_id(match.group(1)) if match else None


def classify(
    state: str, office: str, district: str | None, county: str = "", dataverse: str | None = None
) -> tuple[str, str] | None:
    """Map a source row to (level, race_key string). None = not a race the history keeps.

    The source's own `dataverse` tag is only a hint: MEDSL tags state legislature rows
    "LOCAL" in some states (Michigan 2018), so an explicit office name always wins.
    """
    if SKIP_OFFICE.search(office or "") or not (office or "").strip():
        return None
    # MI writes "10th District Representative in Congress …": the district leads the office name.
    lead = LEAD_DISTRICT.match(office)
    if lead:
        office = lead.group(2)
        district = district or lead.group(1)
    office_name, special = norm_office(office)
    suffix = " S" if special else ""
    dist = (district or "").strip()
    dv = (dataverse or "").upper()
    statewide_dist = dist.upper() in ("", "STATEWIDE", "000", "0", "NA", "N/A")
    if office_name in SENATE_OFFICES or dv == "SENATE":
        return "senate", f"{state} SEN{suffix}"
    if office_name in HOUSE_OFFICES and not (
        dv == "STATE" or office_name == "HOUSE OF REPRESENTATIVES" and dv != "HOUSE"
    ):
        district_id = _district_num(dist) or _office_district(office)
        if "LARGE" in dist.upper():
            district_id = "AL"
        elif district_id is None:
            if state not in AT_LARGE_STATES:
                return None  # a multi-district state with no readable district: drop, never -AL
            district_id = "AL"
        return "house", f"{state}-{district_id}{suffix}"
    if dv == "HOUSE":
        return None  # odd US House row we could not read; never file it under local
    if office_name in STATEWIDE_OFFICES:
        # statewide races are sliced by county (CT ships one row per town) and some files even
        # carry a stray district number; neither changes the key
        return "governor", f"{state} {STATEWIDE_OFFICES[office_name]}{suffix}"
    if (
        office_name in STATE_SENATE
        or office_name in STATE_HOUSE
        or (office_name in BARE_CHAMBERS and dv not in ("HOUSE", "SENATE", "CONGRESS"))
    ):
        chamber = "SD" if office_name in STATE_SENATE or office_name == "SENATE" else "HD"
        district_id = _leg_district(office, dist) or _named_district(dist)
        if district_id:
            position = _position(dist) or _position(office)  # WA elects Position 1 and 2 apart
            if position and 1 <= position <= 26 and not re.search(r"[A-Z]$", district_id):
                district_id = f"{district_id}{chr(64 + position)}"
            race_key = f"{state} {chamber}-{district_id}{suffix}"
            try:
                return "state", str(parse(race_key))
            except RaceKeyError:
                pass
        elif office_name == "SENATE" and statewide_dist and dv not in ("STATE", "LOCAL"):
            return "senate", f"{state} SEN{suffix}"  # bare "Senate", no district: the U.S. race
        return "state", f"{state} LOCAL {slug(office_name, dist)}"
    if dv == "PRESIDENT":
        return None
    return "local", f"{state} LOCAL {slug(county, office_name, dist)}"


# ---------------------------------------------------------------------------------------------
# aggregation


class Agg:
    """(level) -> (race_key, candidate, party, county) -> votes, with MEDSL mode de-dup."""

    def __init__(self) -> None:
        self.total: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
        self.other: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))

    def add(self, level: str, key: tuple, votes: int, mode: str = "") -> None:
        (self.total if mode.upper() == "TOTAL" else self.other)[level][key] += votes

    def rows(self) -> dict[str, list[dict]]:
        by_level: dict[str, list[dict]] = {}
        for level in set(self.total) | set(self.other):
            totals, others = self.total.get(level, {}), self.other.get(level, {})
            merged: dict[tuple, int] = {}
            for candidate_key in set(totals) | set(others):
                total_votes, other_votes = totals.get(candidate_key, 0), others.get(candidate_key, 0)
                # MEDSL sometimes ships a TOTAL row next to its mode breakdown: count it once.
                if total_votes and other_votes and abs(total_votes - other_votes) <= max(1, total_votes // 100):
                    merged[candidate_key] = total_votes
                else:
                    merged[candidate_key] = total_votes + other_votes
            by_level[level] = [
                dict(zip(FIELDS[:3], candidate_key[:3]), votes=votes, county=candidate_key[3])
                for candidate_key, votes in sorted(merged.items())
                if votes > 0
            ]
        return by_level


def _int(value) -> int:
    try:
        return int(float(str(value).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def aggregate_medsl(lines, state: str, agg: Agg | None = None, stage: str = "GEN") -> Agg:
    """MEDSL precinct CSV (iterable of text lines) -> county totals."""
    agg = agg or Agg()
    for row in csv.DictReader(lines):
        if (row.get("state_po") or state).upper() != state:
            continue
        if stage and (row.get("stage") or "GEN").upper() != stage:
            continue
        candidate_name = (row.get("candidate") or "").strip()
        if not candidate_name or SKIP_CANDIDATE.match(candidate_name):
            continue
        district_text = row.get("district") or ""
        if (row.get("special") or "").upper() == "TRUE" and "SPECIAL" not in (row.get("office") or "").upper():
            office_name = (row.get("office") or "") + " (SPECIAL)"
        else:
            office_name = row.get("office") or ""
        county = (row.get("county_name") or "").strip().title()
        classified = classify(state, office_name, district_text, county, row.get("dataverse"))
        if not classified:
            continue
        level, key = classified
        agg.add(
            level,
            (
                key,
                pretty_name(candidate_name),
                norm_party(row.get("party_simplified") or row.get("party_detailed")),
                county,
            ),
            _int(row.get("votes")),
            row.get("mode") or "",
        )
    return agg


def aggregate_openelections(lines, state: str, agg: Agg | None = None) -> Agg:
    """OpenElections county/precinct CSV -> county totals. Uses `votes`, else sums *votes* columns."""
    agg = agg or Agg()
    reader = csv.DictReader(lines)
    columns = [column for column in (reader.fieldnames or []) if column]
    mode_cols = [
        column
        for column in columns
        if column != "votes"
        and re.search(
            r"votes|election_day|mail|absentee|early|"
            r"provisional|advance",
            column,
            re.I,
        )
    ]
    split_names = "candidate" not in columns and ("last" in columns or "first" in columns)
    for row in reader:
        candidate_name = (row.get("candidate") or "").strip()
        if not candidate_name and split_names:  # MI ships last/first/middle/suffix instead of candidate
            candidate_name = " ".join(
                part
                for part in (
                    (row.get("first") or "").strip(),
                    (row.get("middle") or "").strip(),
                    (row.get("last") or "").strip(),
                )
                if part
            )
            candidate_name = f"{candidate_name} {(row.get('suffix') or '').strip()}".strip()
        if not candidate_name or SKIP_CANDIDATE.match(candidate_name):
            continue
        county = (row.get("county") or row.get("parish") or "").strip().title()
        classified = classify(state, row.get("office") or "", row.get("district"), county)
        if not classified:
            continue
        raw_votes = row.get("votes")
        votes = _int(raw_votes) if raw_votes not in (None, "") else sum(_int(row.get(column)) for column in mode_cols)
        level, key = classified
        agg.add(level, (key, pretty_name(candidate_name), norm_party(row.get("party")), county), votes)
    return agg


def parse_openelections_name(name: str) -> tuple[str, str, int] | None:
    """'20141104__ct__general__avon__town.csv' -> ('20141104', 'general', 2).

    Rank 0 is a whole state in one file (…__general__county.csv, …__general__precinct.csv or
    …__general.csv) - complete on its own. A higher rank is one slice of a state, the number
    saying how the repo splits it up: district (1), town/municipal (2), county (3), precinct or
    ward (4). Slices of the same partition must be aggregated together.
    """
    low = name.lower()
    if not low.endswith(".csv") or re.search(r"runoff|recount|special|preference|presidential", low):
        return None
    parts = low[:-4].split("__")
    if len(parts) < 3 or not re.fullmatch(r"\d{8}", parts[0]):
        return None
    election_date, rest = parts[0], parts[2:]
    level = None
    if len(rest) > 1 and rest[-1] in OE_LEVELS:
        level = rest.pop()
    if not any(part in ("general", "primary") for part in rest):
        return None
    election_type = "primary" if "primary" in rest else "general"
    whole_state = not [part for part in rest if part not in ("general", "primary")]
    if whole_state:
        return election_date, election_type, min(OE_LEVELS[level], 2) if level else 1
    return election_date, election_type, OE_LEVELS[level] if level else 3


MAX_PARTITION_FILES = 500


def pick_openelections_files(names: list[str], year: str) -> list[list[str]]:
    """The partitions of a repo year directory that are worth reading, coarsest first.

    Each returned list holds every file of one partition of one election date: a per-county or
    per-town file covers only part of the state, so the files of a partition must be aggregated
    together and partitions must not be mixed (mixing partitions would count the same votes twice). Several
    partitions are offered because no filename tells whether one is complete - the caller keeps
    the one that reports the most races."""
    primary = year.endswith("primary")
    by_date: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for name in sorted(names):
        parsed = parse_openelections_name(name)
        if not parsed:
            continue
        election_date, election_type, rank = parsed
        if primary:
            if election_type != "primary" or not election_date.startswith("2024"):
                continue
        elif election_date != GENERAL_DATES.get(year) or election_type != "general":  # not a primary
            continue
        by_date[election_date][rank].append(name)
    partitions = []
    for election_date in sorted(by_date):
        by_rank = by_date[election_date]
        keep = [rank_key for rank_key in by_rank if rank_key <= 2 or len(by_rank[rank_key]) <= MAX_PARTITION_FILES]
        for rank_key in sorted(keep or [min(by_rank)]):  # a precinct partition of thousands of files
            partitions.append(by_rank[rank_key])  # is only read when it is all there is
    return partitions


# ---------------------------------------------------------------------------------------------
# network


class Net:
    def __init__(self, cache: Path, delay: float = 1.0) -> None:
        self.cache = cache
        self.delay = delay
        self._last = 0.0
        headers = {"User-Agent": UA}
        self.gh_headers = dict(headers, Accept="application/vnd.github+json")
        if os.environ.get("GITHUB_TOKEN"):
            self.gh_headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        self.client = httpx.Client(headers=headers, timeout=120, follow_redirects=True)

    def _wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    def get(self, url: str, **kw) -> httpx.Response:
        for attempt in range(4):
            self._wait()
            try:
                response = self.client.get(url, **kw)
            except httpx.TransportError as exc:
                error = exc
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                error = RuntimeError(f"HTTP {response.status_code}")
            time.sleep(2**attempt * 3)
        raise RuntimeError(f"GET {url} failed: {error}")

    def json(self, url: str, github: bool = False):
        cache_path = self.cache / "api" / (slug(url)[:60] + "-" + hashlib.sha1(url.encode()).hexdigest()[:10] + ".json")
        if cache_path.exists():
            return json.loads(cache_path.read_text())
        response = self.get(url, headers=self.gh_headers if github else None)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(response.text)
        return response.json()

    def download(self, url: str, dest: Path) -> Path | None:
        """Cached streaming download (resumable: .part files are discarded and refetched)."""
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part_path = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(3):
            self._wait()
            try:
                with self.client.stream("GET", url) as response:
                    if response.status_code in (400, 401, 403, 404):
                        body = response.read()[:200].decode(errors="replace")
                        body = body if body.lstrip().startswith("{") else ""
                        print(f"  skip {url}: HTTP {response.status_code} {body}".rstrip(), file=sys.stderr)
                        return None
                    response.raise_for_status()
                    with part_path.open("wb") as fh:
                        for chunk in response.iter_bytes(1 << 20):
                            fh.write(chunk)
                part_path.rename(dest)
                return dest
            except (httpx.HTTPError, OSError) as exc:
                print(f"  retry {url}: {exc}", file=sys.stderr)
                time.sleep(2**attempt * 3)
        return None


def _decode(raw: bytes) -> str:
    """UTF-8, else Windows-1252: several OpenElections files (IN, MA 2014) are cp1252, and
    decoding those files as UTF-8 turned "André Carson" into "Andr\ufffd Carson"."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _iter_text(path: Path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".csv") and not name.startswith("__MACOSX"):
                    yield from io.StringIO(_decode(archive.read(name)), newline="")
    else:
        yield from io.StringIO(_decode(path.read_bytes()), newline="")


# ---------------------------------------------------------------------------------------------
# GitHub file listings
#
# The contents API allows 60 unauthenticated requests an hour per IP, which is what starved the
# 2014/2016 runs (and what a shared CI box exhausts). A blobless shallow clone lists every path
# of a repo in one request instead; raw.githubusercontent.com downloads are not API-limited.
# One listing per repo is cached under <data>/_cache/git/.

_trees: dict[str, tuple[str, list[str]] | None] = {}


def raw_github(repo: str, branch: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def git_tree(net: "Net", repo: str) -> tuple[str, list[str]] | None:
    """(default branch, all file paths) of a GitHub repo, or None if the repo does not exist."""
    if repo in _trees:
        return _trees[repo]
    cache_path = net.cache / "git" / (repo.replace("/", "__") + ".json")
    tree = None
    if cache_path.exists():
        cached = json.loads(cache_path.read_text())
        tree = (cached.get("branch") or "HEAD", cached.get("files") or [])
    elif shutil.which("git"):
        clone_dir = Path(tempfile.mkdtemp(prefix="ew-gh-"))
        net._wait()  # same politeness budget as an HTTP request
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    "--depth",
                    "1",
                    "--quiet",
                    f"https://github.com/{repo}.git",
                    str(clone_dir),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )
            branch = subprocess.run(
                ["git", "-C", str(clone_dir), "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            files = subprocess.run(
                ["git", "-C", str(clone_dir), "ls-tree", "-r", "--name-only", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"  git listing failed for {repo}: {exc}", file=sys.stderr)
            tree = ("", [])  # no such repo: do not burn the rate-limited API on it
        else:
            tree = (branch or "HEAD", files)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"branch": tree[0], "files": files}))
        finally:
            shutil.rmtree(clone_dir, ignore_errors=True)
    _trees[repo] = tree
    return tree


def repo_files(net: "Net", repo: str, subdir: str = "", deep: bool = False) -> dict[str, str]:
    """{file name: download URL} for the files of `repo/subdir`.

    With deep=True files in nested directories are included too (OpenElections keeps some
    states' per-county files in <year>/counties/); the first path wins, so the copy directly
    in the year directory beats a nested duplicate."""
    prefix = f"{subdir.strip('/')}/" if subdir.strip("/") else ""
    tree = git_tree(net, repo)
    if tree is not None:
        branch, paths = tree
        urls: dict[str, str] = {}
        for path in sorted(paths):
            if not path.startswith(prefix) or path.endswith("/"):
                continue
            rest = path[len(prefix) :]
            if not rest or (not deep and "/" in rest):
                continue
            urls.setdefault(rest.rsplit("/", 1)[-1], raw_github(repo, branch, path))
        return urls
    listing = net.json(f"https://api.github.com/repos/{repo}/contents/{subdir}", github=True) or []
    return {
        entry["name"]: entry["download_url"]
        for entry in listing
        if entry.get("type") == "file" and entry.get("download_url")
    }


# ---------------------------------------------------------------------------------------------
# per-source fetchers: return (Agg | None, url | None, note)


def medsl_precinct(net: Net, year: str, state: str):
    if year == "2020":
        files = net.json(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={DV_2020_PRECINCTS}")
        for entry in (files or {}).get("data", {}).get("latestVersion", {}).get("files", []):
            data_file = entry["dataFile"]
            name = data_file.get("originalFileName") or data_file["filename"]
            if re.fullmatch(rf"2020[-_]{state.lower()}[-_]precinct[-_]general\.csv", name):
                url = f"{DATAVERSE}/api/access/datafile/{data_file['id']}"
                path = net.download(url, net.cache / "medsl" / name)
                if not path:
                    return (
                        None,
                        url,
                        "Dataverse guestbook required (fill it in a browser and drop the "
                        f"file at {net.cache / 'medsl' / name})",
                    )
                return aggregate_medsl(_iter_text(path), state), url, ""
        return None, None, "no 2020 file"
    repo = MEDSL_REPO.format(year=year)
    files = repo_files(net, repo, "individual_states")
    if not files:
        extra = (
            f"; its precinct returns are on Harvard Dataverse ({DV_2016_PRECINCTS}) behind a "
            "guestbook form, which this script does not fill in"
            if year == "2016"
            else ""
        )
        return None, None, f"no MEDSL GitHub repo for {year}{extra}"
    name_pattern = re.compile(rf"(^|[-_]){state.lower()}([-_]|24\.zip$)")
    for name, url in sorted(files.items()):
        if name.endswith((".zip", ".csv")) and (name_pattern.search(name) or name == f"{state.lower()}24.zip"):
            path = net.download(url, net.cache / "medsl" / year / name)
            if path:
                return aggregate_medsl(_iter_text(path), state), url, ""
            return None, url, "download failed"
    return None, None, "state not in MEDSL repo"


_senate_cache: dict[str, Path | None] = {}


def medsl_senate_statewide(net: Net, year: str, state: str):
    if "src" not in _senate_cache:
        metadata = net.json(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={DV_SENATE_STATE}") or {}
        _senate_cache["src"] = None
        for entry in metadata.get("data", {}).get("latestVersion", {}).get("files", []):
            data_file = entry["dataFile"]
            name = data_file.get("originalFileName") or data_file["filename"]
            if re.search(r"senate-state.*\.(csv|tab)$", name):
                url = f"{DATAVERSE}/api/access/datafile/{data_file['id']}"
                _senate_cache["url"] = url
                _senate_cache["src"] = net.download(url, net.cache / "medsl" / name)
    path = _senate_cache.get("src")
    if not path:
        return None, _senate_cache.get("url"), "senate file unavailable"
    agg = Agg()
    with path.open(encoding="utf-8", errors="replace", newline="") as entry:
        head = entry.readline()
        entry.seek(0)
        rd = csv.DictReader(entry, delimiter="\t" if "\t" in head else ",")
        for row in rd:
            if row.get("year") != year or row.get("state_po") != state or (row.get("stage") or "").lower() != "gen":
                continue
            cand = (row.get("candidate") or "").strip()
            if not cand or SKIP_CANDIDATE.match(cand):
                continue
            sp = " S" if (row.get("special") or "").lower() == "true" else ""
            agg.add(
                "senate",
                (f"{state} SEN{sp}", pretty_name(cand), norm_party(row.get("party_simplified")), ""),
                _int(row.get("candidatevotes")),
                row.get("mode") or "",
            )
    return agg, _senate_cache.get("url"), ""


def openelections(net: Net, year: str, state: str):
    repo_year = year.split("-")[0]
    repo = f"openelections/openelections-data-{state.lower()}"
    names = repo_files(net, repo, repo_year, deep=True)
    if not names:
        return None, None, "no OpenElections directory"
    picked = pick_openelections_files(list(names), year)
    if not picked:
        return None, None, "no matching OpenElections file"
    merged: dict[str, list[dict]] = {}
    urls: dict[str, str] = {}
    by_date: dict[str, list[list[str]]] = defaultdict(list)
    for group in picked:
        by_date[group[0].split("__")[0]].append(group)  # partitions of one election date
    # Every partition is aggregated on its own (a partition's files hold different parts of the
    # state), then one partition per date is kept: the one reporting the most races. Repos ship a
    # whole-state summary that is sometimes only the statewide offices and sometimes the only
    # complete file; the race count settles it without mixing partitions and double counting.
    for election_date in sorted(by_date):
        best: tuple[int, dict[str, list[dict]], str] = (0, {}, "")
        for group in by_date[election_date]:
            agg = Agg()
            used: list[str] = []
            for file_name in group:
                path = net.download(names[file_name], net.cache / "openelections" / state / file_name)
                if not path:
                    continue
                aggregate_openelections(_iter_text(path), state, agg)
                used.append(file_name)
            races = agg.rows()
            total = sum(len(level_rows) for level_rows in races.values())
            if used and total > best[0]:
                src = names[used[0]] if len(used) == 1 else f"{names[used[0]]} (+{len(used) - 1} more files)"
                best = (total, races, src)
        for level, rows in best[1].items():
            merged.setdefault(level, []).extend(rows)
            urls[level] = " ".join(filter(None, [urls.get(level), best[2]]))
    return Rows(merged), urls, ""


class Rows:
    """Already-aggregated rows with the Agg interface."""

    def __init__(self, rows: dict[str, list[dict]]) -> None:
        self._rows = rows

    def rows(self) -> dict[str, list[dict]]:
        return self._rows

    def __bool__(self) -> bool:
        return any(self._rows.values())


# ---------------------------------------------------------------------------------------------
# NYT election-night time series (real, timestamped)

NYT_2020 = "https://static01.nyt.com/elections-assets/2020/data/api/2020-11-03/race-page/{slug}/{office}.json"
NYT_2024 = "https://static01.nyt.com/elections-assets/pages/data/2024-11-05/results-{slug}-{office}.json"
NYT_OFFICES = {"2020": {"SEN": "senate", "GOV": "governor"}, "2024": {"SEN": "us-senate", "GOV": "governor"}}
NYT_PARTY = {"democrat": "DEM", "republican": "REP", "libertarian": "LIB", "green": "GRN", "independent": "IND"}


def nyt_timeline_2020(payload: dict, state: str) -> dict[str, list[dict]]:
    timelines = {}
    for race in payload.get("data", {}).get("races", []):
        if race.get("state_id") != state:
            continue
        office = "SEN" if race.get("office") == "U.S. Senate" else "GOV" if race.get("office") == "Governor" else None
        if not office:
            continue
        key = f"{state} {office}" + (" S" if "special" in (race.get("race_slug") or "") else "")
        cands = {candidate["candidate_key"]: candidate for candidate in race.get("candidates", [])}
        lines = []
        for point in sorted(race.get("timeseries", []), key=lambda point: point["timestamp"]):
            total = point.get("votes") or 0
            rows = [
                {
                    "name": cands[candidate_key]["name_display"] if candidate_key in cands else candidate_key,
                    "party": NYT_PARTY.get((cands.get(candidate_key) or {}).get("party_id", ""), None),
                    "votes": round(share * total),
                }
                for candidate_key, share in (point.get("vote_shares") or {}).items()
            ]
            if not total:
                continue
            lines.append(
                {
                    "ts": point["timestamp"],
                    "key": key,
                    "source": "nyt",
                    "synthetic": False,
                    "candidates": rows,
                    "total_votes": total,
                    "expected_vote_pct": point.get("eevp"),
                    "url": race.get("url"),
                }
            )
        if lines:
            timelines[key] = lines
    return timelines


def nyt_timeline_2024(payload: dict, state: str) -> dict[str, list[dict]]:
    timelines = {}
    for race in payload.get("races", []):
        office = {"S": "SEN", "G": "GOV"}.get(race.get("office_id"))
        if not office:
            continue
        race_key = f"{state} {office}" + (" S" if race.get("race_type_id") in ("SG", "X") else "")
        metadata = race.get("candidate_metadata", {})
        series = (payload.get("timeseries") or {}).get(race["nyt_id"], [])
        lines = []
        for point in sorted(series, key=lambda point: point["greatest_updated_at"]):
            rows = []
            for candidate in point.get("candidates", []):
                meta_entry = metadata.get(candidate["nyt_id"], {})
                name = (
                    " ".join(part for part in (meta_entry.get("first_name"), meta_entry.get("last_name")) if part)
                    or candidate["nyt_id"]
                )
                rows.append(
                    {
                        "name": name,
                        "party": norm_party((meta_entry.get("party") or {}).get("nyt_id")) or None,
                        "votes": (candidate.get("votes") or {}).get("total", 0),
                    }
                )
            if not point.get("total_votes"):
                continue
            lines.append(
                {
                    "ts": point["greatest_updated_at"],
                    "key": race_key,
                    "source": "nyt",
                    "synthetic": False,
                    "candidates": rows,
                    "total_votes": point["total_votes"],
                    "expected_vote_pct": point.get("eevp"),
                    "url": race.get("url"),
                }
            )
        if lines:
            timelines[race_key] = lines
    return timelines


def fetch_nyt_timelines(net: Net, data_dir: Path, year: str, state: str) -> list[str]:
    offices = NYT_OFFICES.get(year)
    if not offices:
        return []
    written = []
    state_slug = STATES[state].lower().replace(" ", "-")
    for office in offices.values():
        url = (NYT_2020 if year == "2020" else NYT_2024).format(slug=state_slug, office=office)
        path = net.download(url, net.cache / "nyt" / year / f"{state_slug}-{office}.json")
        if not path:
            continue  # no such race that year (404)
        payload = json.loads(path.read_text())
        series = nyt_timeline_2020(payload, state) if year == "2020" else nyt_timeline_2024(payload, state)
        for race_key, lines in series.items():
            out_path = data_dir / "timelines" / year / (race_key.replace(" ", "_") + ".jsonl")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
            written.append(f"{race_key} ({len(lines)} snapshots) <- {url}")
    return written


# ---------------------------------------------------------------------------------------------


def write_level(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".csv.part")
    with tmp_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.rename(path)


def run(
    data_dir: Path, states: list[str], years: list[str], force: bool = False, timelines: bool = True, delay: float = 1.0
) -> int:
    net = Net(data_dir / "_cache", delay)
    for year in years:
        manifest_path = data_dir / year / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"year": year, "states": {}}
        for state in states:
            if state in manifest["states"] and not force:
                print(f"{year} {state}: done already (use --force to redo)")
                continue
            print(f"{year} {state}: fetching")
            entry: dict = {"levels": {}, "notes": []}
            sources = (
                [("openelections", openelections)]
                if year.endswith("primary")
                else [
                    ("medsl_precinct", medsl_precinct),
                    ("medsl_senate_statewide", medsl_senate_statewide),
                    ("openelections", openelections),
                ]
            )
            remaining = set(LEVELS)
            for source_name, fetcher in sources:
                if not remaining:
                    break
                if source_name == "medsl_senate_statewide" and "senate" not in remaining:
                    continue
                try:
                    agg, url, note = fetcher(net, year, state)
                except Exception as exc:  # one bad source must not stop the run
                    agg, url, note = None, None, f"error: {exc}"
                if note:
                    entry["notes"].append(f"{source_name}: {note}" + (f" ({url})" if isinstance(url, str) else ""))
                if not agg:
                    continue
                for level, rows in agg.rows().items():
                    if level in remaining and rows:
                        write_level(data_dir / year / state / f"{level}.csv", rows)
                        entry["levels"][level] = {
                            "source": source_name,
                            "rows": len(rows),
                            "url": url.get(level) if isinstance(url, dict) else url,
                            "races": len({row["race_key"] for row in rows}),
                        }
                        remaining.discard(level)
            for level in sorted(remaining):
                entry["notes"].append(f"{level}: no public data found")
                (data_dir / year / state / f"{level}.csv").unlink(missing_ok=True)  # stale from a --force
            if timelines and not year.endswith("primary"):
                entry["timelines"] = fetch_nyt_timelines(net, data_dir, year, state)
            manifest["states"][state] = entry
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))  # checkpoint per state
            got = ", ".join(
                f"{level_name}={level_info['races']}" for level_name, level_info in sorted(entry["levels"].items())
            )
            print(f"{year} {state}: {got or 'nothing'}; timelines: {len(entry.get('timelines', []))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--states", default="all", help="comma-separated USPS codes (default all)")
    ap.add_argument("--years", default=",".join(YEARS), help=f"comma-separated, from {YEARS}")
    ap.add_argument("--force", action="store_true", help="redo states already in the manifest")
    ap.add_argument("--no-timelines", action="store_true", help="skip NYT election-night series")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (min 1)")
    args = ap.parse_args(argv)
    states = (
        sorted(STATES)
        if args.states == "all"
        else [state_code.strip().upper() for state_code in args.states.split(",")]
    )
    bad = [state_code for state_code in states if state_code not in STATES]
    years = [year.strip() for year in args.years.split(",")]
    if bad or any(year not in YEARS for year in years):
        ap.error(f"bad state/year: {bad or years}")
    return run(Path(args.data_dir).expanduser(), states, years, args.force, not args.no_timelines, max(1.0, args.delay))


if __name__ == "__main__":
    sys.exit(main())
