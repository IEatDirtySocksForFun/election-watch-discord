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
GENERAL_DATES = {"2014": "20141104", "2016": "20161108", "2018": "20181106", "2020": "20201103", "2022": "20221108", "2024": "20241105"}
YEARS = ["2014", "2016", "2018", "2020", "2022", "2024", "2024-primary"]
LEVELS = ["senate", "house", "governor", "state", "local"]
FIELDS = ["race_key", "candidate", "party", "votes", "county"]

MEDSL_REPO = "MEDSL/{year}-elections-official"
DATAVERSE = "https://dataverse.harvard.edu"
DV_2020_PRECINCTS = "doi:10.7910/DVN/NT66Z3"      # Precinct-Level Returns 2020 by Individual State
DV_SENATE_STATE = "doi:10.7910/DVN/PEJ5QU"        # U.S. Senate statewide 1976-2024
DV_2016_PRECINCTS = "doi:10.7910/DVN/NH5S2I"      # 2016 Precinct-Level Election Results (guestbook)

# OpenElections trailing filename word -> how the repo splits a state up. Ranks 0-2 are a whole
# state in one file (county summary, no geo word, precinct dump): complete on their own, so only
# one is used. Ranks 3+ are one slice of the state (district, town, county, precinct) and every
# file of the chosen slice is aggregated, because each covers only part of the state.
OE_LEVELS = {"county": 0, "county-level": 0, "counties": 0,
             "district": 3, "districts": 3, "congressional": 3,
             "town": 4, "towns": 4, "municipal": 4, "municipalities": 4, "city": 4, "cities": 4,
             "county-ward": 5, "county-precinct": 5,
             "precinct": 6, "precincts": 6, "precinct-level": 6, "county-level-precinct": 6,
             "ward": 6, "wards": 6, "township": 6, "townships": 6}

# ---------------------------------------------------------------------------------------------
# normalisation (pure functions, unit-tested)

STATEWIDE_OFFICES = {
    "GOVERNOR": "GOV", "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "GOVERNOR LIEUTENANT GOVERNOR": "GOV", "GOVERNOR AND LT GOVERNOR": "GOV",
    "GOVERNOR LT GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV", "LT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG", "SECRETARY OF STATE": "SOS", "SECRETARY OF THE STATE": "SOS",
    "STATE TREASURER": "TREAS", "TREASURER": "TREAS", "TREASURER OF STATE": "TREAS",
    "STATE AUDITOR": "AUD", "AUDITOR": "AUD", "AUDITOR OF STATE": "AUD", "AUDITOR GENERAL": "AUD",
    "STATE COMPTROLLER": "COMP", "COMPTROLLER": "COMP", "COMPTROLLER OF PUBLIC ACCOUNTS": "COMP",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT", "STATE SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "STATE SCHOOL SUPERINTENDENT": "SUPT", "SUPERINTENDENT OF EDUCATION": "SUPT",
    "COMMISSIONER OF AGRICULTURE": "AGCOM", "AGRICULTURE COMMISSIONER": "AGCOM",
    "COMMISSIONER OF AGRICULTURE AND INDUSTRIES": "AGCOM", "SECRETARY OF AGRICULTURE": "AGCOM",
    "INSURANCE COMMISSIONER": "INSCOM", "COMMISSIONER OF INSURANCE": "INSCOM",
    "COMMISSIONER OF THE GENERAL LAND OFFICE": "LANDCOM", "LAND COMMISSIONER": "LANDCOM",
    "COMMISSIONER OF PUBLIC LANDS": "LANDCOM",
    "RAILROAD COMMISSIONER": "RRC",
}
SENATE_OFFICES = {"US SENATE", "U S SENATE", "UNITED STATES SENATE", "US SENATOR", "U S SENATOR",
                  "UNITED STATES SENATOR"}
HOUSE_OFFICES = {"US HOUSE", "U S HOUSE", "US REPRESENTATIVE", "U S REPRESENTATIVE",
                 "UNITED STATES REPRESENTATIVE", "REPRESENTATIVE IN CONGRESS", "US CONGRESS",
                 "U S CONGRESS", "UNITED STATES HOUSE OF REPRESENTATIVES", "US HOUSE OF REPRESENTATIVES",
                 "U S HOUSE OF REPRESENTATIVES"}
STATE_SENATE = {"STATE SENATE", "STATE SENATOR", "STATE SENATORS", "SENATOR IN THE GENERAL ASSEMBLY",
                "SENATOR IN STATE LEGISLATURE"}
STATE_HOUSE = {"STATE HOUSE", "STATE HOUSE OF DELEGATES", "STATE REPRESENTATIVE", "STATE REPRESENTATIVES",
               "STATE REP", "STATE REPS", "STATE ASSEMBLY", "ASSEMBLY", "HOUSE OF DELEGATES",
               "STATE HOUSE OF REPRESENTATIVES", "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
               "MEMBER OF THE STATE ASSEMBLY", "HOUSE OF REPRESENTATIVES", "STATE DELEGATE",
               "REPRESENTATIVE IN STATE LEGISLATURE", "MEMBER OF THE STATE LEGISLATURE"}
# A row whose office is just "House"/"Senate" is state legislature when it carries a legislative
# district (a U.S. race would have been tagged by its own office name or dataverse).
BARE_CHAMBERS = {"HOUSE": "HD", "SENATE": "SD"}
SKIP_OFFICE = re.compile(r"PRESIDENT|STRAIGHT|REGISTERED|BALLOTS CAST|TURNOUT|OVER ?VOTES|UNDER ?VOTES|"
                         r"VOTERS|TOTAL VOTES|BLANK|SPOILED|REJECTED", re.I)
SKIP_CANDIDATE = re.compile(r"^(OVER ?VOTES?|UNDER ?VOTES?|TOTAL( VOTES)?|BLANK( VOTES)?|SPOILED|"
                            r"VOID|REJECTED|SCATTER(ED|ING)?|WRITE[- ]?INS?( TOTALS?)?|(TOTAL )?WRITE[- ]IN VOTES)$", re.I)
AT_LARGE_STATES = {"AK", "DE", "ND", "SD", "VT", "WY"}
PARTIES = {"DEMOCRAT": "DEM", "DEMOCRATIC": "DEM", "DEM": "DEM", "D": "DEM",
           "REPUBLICAN": "REP", "REP": "REP", "R": "REP", "LIBERTARIAN": "LIB", "LIB": "LIB",
           "GREEN": "GRN", "GRN": "GRN", "INDEPENDENT": "IND", "IND": "IND",
           "GOP": "REP", "NONPARTISAN": "NPA", "NON-PARTISAN": "NPA", "NP": "NPA", "NPA": "NPA"}


def norm_office(office: str) -> tuple[str, bool]:
    """'U.S. Senate (Special)' -> ('US SENATE', True)."""
    o = office.upper()
    special = bool(re.search(r"\bSPECIAL\b|UNEXPIRED|PARTIAL TERM", o))
    o = re.sub(r"\(.*?\)", " ", o)
    o = re.sub(r"\bPARTIAL TERM\b.*$", " ", o)   # MI "… Partial Term Ending 01/01/2017"
    o = re.sub(r"\b(SPECIAL|UNEXPIRED TERM|UNEXPIRED)\b", " ", o)
    o = re.sub(r"[^A-Z0-9 ]+", " ", o)
    o = re.sub(r"\bDISTRICT\b.*$", "", o)
    o = re.sub(r"\bFILES? IN\b.*$", " ", o)                # "… Representative Files In WAYNE County"
    o = re.sub(r"\bPOS(?:ITION)?\.?\s*\d{0,2}\b", " ", o)  # "State Representative Pos. 2"
    o = re.sub(r"\b\d+ ?YEAR TERM\b|\bTERM\b", " ", o)     # "State House Unexpired 2 Year Term"
    return re.sub(r"\s+", " ", o).strip(), special


NO_PARTY = {"NONE", "NO PARTY", "NO PARTY LISTED", "NO PARTY AFFILIATION", "NOPARTY"}


def norm_party(p: str | None) -> str:
    p = (p or "").strip().upper()
    if not p or p in NO_PARTY:
        return ""
    return PARTIES.get(p, p[:12])


def slug(*parts: str) -> str:
    s = "-".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "unknown"


def _district_num(d: str | None) -> str | None:
    d = re.sub(r"(\d)(ST|ND|RD|TH)\b", r"\1", (d or "").strip().upper())
    m = re.fullmatch(r"(?:(?:DISTRICT|DIST|DIST\.|NO\.?)\s*)?0*(\d{1,3})\s*([A-Z]?)", d)
    if not m:
        return None
    return f"{int(m.group(1)):02d}{m.group(2)}"


def _office_district(office: str | None) -> str | None:
    """'U.S. House District 7' -> '07' (some sources leave the district column empty)."""
    m = DISTRICT_IN.search((office or "").upper())
    return _district_num(m.group(1)) if m else None


POSITION_IN = re.compile(r"\bPOS(?:ITION)?\.?\s*(\d{1,2})\b")
DISTRICT_IN = re.compile(r"\bDISTRICT\s+(?:NO\.?\s*)?(0*\d{1,3}[A-Z]?|[A-Z])\b")
LEAD_DISTRICT = re.compile(r"^\s*(0*\d{1,3})(?:ST|ND|RD|TH)?\s+DISTRICT\s+(.+)$", re.I)
LEG_TAIL = re.compile(r"^(?:(?:STATE|MEMBER OF THE STATE)\s+)?(?:HOUSE|SENATE|ASSEMBLY|DELEGATES?|"
                      r"REPRESENTATIVES?|SENATORS?)\s+(0*\d{1,3}[A-Z]?|[A-Z])$")
NAMED_DISTRICT = re.compile(r"[A-Z]{2,4}(?:-(?:[A-Z]{1,4}|\d{1,3}[A-Z]?))*")


def _district_id(text: str | None) -> str | None:
    """'001, Position 1' -> '01', 'District 30' -> '30', '4a' -> '04A', 'B' -> 'B'."""
    t = re.sub(r"[^A-Z0-9 ]+", " ", (text or "").upper())
    t = POSITION_IN.sub(" ", t)
    t = re.sub(r"\b(?:DISTRICT|DIST|NO)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = re.fullmatch(r"0*(\d{1,3})\s*([A-Z]?)", t)
    if m:
        return f"{int(m.group(1)):02d}{m.group(2)}"
    m = re.fullmatch(r"([A-Z])", t)
    return m.group(1) if m else None


def _named_district(text: str | None) -> str | None:
    """Vermont-style district names: 'CHT-6-7', 'ADD', 'ESS-CAL-ORL'."""
    t = re.sub(r"[^A-Z0-9]+", "-", (text or "").upper()).strip("-")
    if _district_id(t) or not NAMED_DISTRICT.fullmatch(t):
        return None
    return t


def _position(text: str | None) -> int | None:
    m = POSITION_IN.search((text or "").upper())
    return int(m.group(1)) if m else None


def _leg_district(office: str, district: str) -> str | None:
    """Legislative district from the district column, else from the office name.

    Sources put the district in either place: WA "State House" + "001, Position 1", CO
    "State Representatives, District 30", AK 2020 "House District 7", RI "Senate 25".
    """
    n = _district_id(district)
    if n:
        return n
    up = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", (office or "").upper())).strip()
    m = DISTRICT_IN.search(up)
    if m:
        return _district_id(m.group(1))
    m = LEG_TAIL.match(up)
    return _district_id(m.group(1)) if m else None


def classify(state: str, office: str, district: str | None, county: str = "",
             dataverse: str | None = None) -> tuple[str, str] | None:
    """Map a source row to (level, race_key string). None = not a race we keep.

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
    o, special = norm_office(office)
    sp = " S" if special else ""
    dist = (district or "").strip()
    dv = (dataverse or "").upper()
    statewide_dist = dist.upper() in ("", "STATEWIDE", "000", "0", "NA", "N/A")
    if o in SENATE_OFFICES or dv == "SENATE":
        return "senate", f"{state} SEN{sp}"
    if o in HOUSE_OFFICES and not (dv == "STATE" or o == "HOUSE OF REPRESENTATIVES" and dv != "HOUSE"):
        n = _district_num(dist) or _office_district(office)
        if "LARGE" in dist.upper():
            n = "AL"
        elif n is None:
            if state not in AT_LARGE_STATES:
                return None   # a multi-district state with no readable district: drop, never -AL
            n = "AL"
        return "house", f"{state}-{n}{sp}"
    if dv == "HOUSE":
        return None   # odd US House row we could not read; never file it under local
    if o in STATEWIDE_OFFICES and statewide_dist:
        return "governor", f"{state} {STATEWIDE_OFFICES[o]}{sp}"
    if o in STATE_SENATE or o in STATE_HOUSE or (o in BARE_CHAMBERS and \
                                                 dv not in ("HOUSE", "SENATE", "CONGRESS")):
        chamber = "SD" if o in STATE_SENATE or o == "SENATE" else "HD"
        n = _leg_district(office, dist) or _named_district(dist)
        if n:
            pos = _position(dist) or _position(office)   # WA elects Position 1 and 2 apart
            if pos and 1 <= pos <= 26 and not re.search(r"[A-Z]$", n):
                n = f"{n}{chr(64 + pos)}"
            key = f"{state} {chamber}-{n}{sp}"
            try:
                return "state", str(parse(key))
            except RaceKeyError:
                pass
        elif o == "SENATE" and statewide_dist and dv not in ("STATE", "LOCAL"):
            return "senate", f"{state} SEN{sp}"   # bare "Senate", no district: the U.S. race
        return "state", f"{state} LOCAL {slug(o, dist)}"
    if dv == "PRESIDENT":
        return None
    return "local", f"{state} LOCAL {slug(county, o, dist)}"


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
        out: dict[str, list[dict]] = {}
        for level in set(self.total) | set(self.other):
            t, o = self.total.get(level, {}), self.other.get(level, {})
            merged: dict[tuple, int] = {}
            for k in set(t) | set(o):
                tv, ov = t.get(k, 0), o.get(k, 0)
                # MEDSL sometimes ships a TOTAL row next to its mode breakdown: count it once.
                if tv and ov and abs(tv - ov) <= max(1, tv // 100):
                    merged[k] = tv
                else:
                    merged[k] = tv + ov
            out[level] = [dict(zip(FIELDS[:3], k[:3]), votes=v, county=k[3])
                          for k, v in sorted(merged.items()) if v > 0]
        return out


def _int(v) -> int:
    try:
        return int(float(str(v).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def aggregate_medsl(lines, state: str, agg: Agg | None = None, stage: str = "GEN") -> Agg:
    """MEDSL precinct CSV (iterable of text lines) -> county totals."""
    agg = agg or Agg()
    for r in csv.DictReader(lines):
        if (r.get("state_po") or state).upper() != state:
            continue
        if stage and (r.get("stage") or "GEN").upper() != stage:
            continue
        cand = (r.get("candidate") or "").strip()
        if not cand or SKIP_CANDIDATE.match(cand):
            continue
        dist = r.get("district") or ""
        if (r.get("special") or "").upper() == "TRUE" and "SPECIAL" not in (r.get("office") or "").upper():
            office = (r.get("office") or "") + " (SPECIAL)"
        else:
            office = r.get("office") or ""
        county = (r.get("county_name") or "").strip().title()
        got = classify(state, office, dist, county, r.get("dataverse"))
        if not got:
            continue
        level, key = got
        agg.add(level, (key, pretty_name(cand), norm_party(r.get("party_simplified") or
                                                           r.get("party_detailed")),
                        county), _int(r.get("votes")), r.get("mode") or "")
    return agg


def aggregate_openelections(lines, state: str, agg: Agg | None = None) -> Agg:
    """OpenElections county/precinct CSV -> county totals. Uses `votes`, else sums *votes* columns."""
    agg = agg or Agg()
    reader = csv.DictReader(lines)
    cols = [c for c in (reader.fieldnames or []) if c]
    mode_cols = [c for c in cols if c != "votes" and re.search(r"votes|election_day|mail|absentee|early|"
                                                                r"provisional|advance", c, re.I)]
    split_names = "candidate" not in cols and ("last" in cols or "first" in cols)
    for r in reader:
        cand = (r.get("candidate") or "").strip()
        if not cand and split_names:      # MI ships last/first/middle/suffix instead of candidate
            cand = " ".join(p for p in ((r.get("first") or "").strip(), (r.get("middle") or "").strip(),
                                        (r.get("last") or "").strip()) if p)
            cand = f"{cand} {(r.get('suffix') or '').strip()}".strip()
        if not cand or SKIP_CANDIDATE.match(cand):
            continue
        county = (r.get("county") or r.get("parish") or "").strip().title()
        got = classify(state, r.get("office") or "", r.get("district"), county)
        if not got:
            continue
        v = r.get("votes")
        votes = _int(v) if v not in (None, "") else sum(_int(r.get(c)) for c in mode_cols)
        level, key = got
        agg.add(level, (key, pretty_name(cand), norm_party(r.get("party")), county), votes)
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
    date, rest = parts[0], parts[2:]
    level = None
    if len(rest) > 1 and rest[-1] in OE_LEVELS:
        level = rest.pop()
    if not any(p in ("general", "primary") for p in rest):
        return None
    etype = "primary" if "primary" in rest else "general"
    whole_state = not [p for p in rest if p not in ("general", "primary")]
    if whole_state:
        return date, etype, min(OE_LEVELS[level], 2) if level else 1
    return date, etype, OE_LEVELS[level] if level else 3


MAX_PARTITION_FILES = 500


def pick_openelections_files(names: list[str], year: str) -> list[list[str]]:
    """The partitions of a repo year directory that are worth reading, coarsest first.

    Each returned list holds every file of one partition of one election date: a per-county or
    per-town file covers only part of the state, so the files of a partition must be aggregated
    together and partitions must not be mixed (they would count the same votes twice). Several
    partitions are offered because no filename tells whether one is complete - the caller keeps
    the one that reports the most races."""
    primary = year.endswith("primary")
    by_date: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for n in sorted(names):
        parsed = parse_openelections_name(n)
        if not parsed:
            continue
        date, etype, rank = parsed
        if primary:
            if etype != "primary" or not date.startswith("2024"):
                continue
        elif date != GENERAL_DATES.get(year) or etype != "general":   # not a primary
            continue
        by_date[date][rank].append(n)
    out = []
    for date in sorted(by_date):
        parts = by_date[date]
        keep = [r for r in parts if r <= 2 or len(parts[r]) <= MAX_PARTITION_FILES]
        for r in sorted(keep or [min(parts)]):     # a precinct partition of thousands of files
            out.append(parts[r])                  # is only read when it is all there is
    return out


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
                r = self.client.get(url, **kw)
            except httpx.TransportError as e:
                err = e
            else:
                if r.status_code < 500 and r.status_code != 429:
                    return r
                err = RuntimeError(f"HTTP {r.status_code}")
            time.sleep(2 ** attempt * 3)
        raise RuntimeError(f"GET {url} failed: {err}")

    def json(self, url: str, github: bool = False):
        cpath = self.cache / "api" / (slug(url)[:60] + "-" + hashlib.sha1(url.encode()).hexdigest()[:10] + ".json")
        if cpath.exists():
            return json.loads(cpath.read_text())
        r = self.get(url, headers=self.gh_headers if github else None)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(r.text)
        return r.json()

    def download(self, url: str, dest: Path) -> Path | None:
        """Cached streaming download (resumable: .part files are discarded and refetched)."""
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(3):
            self._wait()
            try:
                with self.client.stream("GET", url) as r:
                    if r.status_code in (400, 401, 403, 404):
                        body = r.read()[:200].decode(errors="replace")
                        body = body if body.lstrip().startswith("{") else ""
                        print(f"  skip {url}: HTTP {r.status_code} {body}".rstrip(), file=sys.stderr)
                        return None
                    r.raise_for_status()
                    with part.open("wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                part.rename(dest)
                return dest
            except (httpx.HTTPError, OSError) as e:
                print(f"  retry {url}: {e}", file=sys.stderr)
                time.sleep(2 ** attempt * 3)
        return None


def _iter_text(path: Path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith(".csv") and not name.startswith("__MACOSX"):
                    with z.open(name) as f:
                        yield from io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
    else:
        with path.open(encoding="utf-8", errors="replace", newline="") as f:
            yield from f


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
    cpath = net.cache / "git" / (repo.replace("/", "__") + ".json")
    tree = None
    if cpath.exists():
        doc = json.loads(cpath.read_text())
        tree = (doc.get("branch") or "HEAD", doc.get("files") or [])
    elif shutil.which("git"):
        tmp = Path(tempfile.mkdtemp(prefix="ew-gh-"))
        net._wait()                       # same politeness budget as an HTTP request
        try:
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
                            "--quiet", f"https://github.com/{repo}.git", str(tmp)],
                           check=True, capture_output=True, timeout=600)
            branch = subprocess.run(["git", "-C", str(tmp), "rev-parse", "--abbrev-ref", "HEAD"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            files = subprocess.run(["git", "-C", str(tmp), "ls-tree", "-r", "--name-only", "HEAD"],
                                   check=True, capture_output=True, text=True).stdout.splitlines()
        except (subprocess.SubprocessError, OSError) as e:
            print(f"  git listing failed for {repo}: {e}", file=sys.stderr)
            tree = ("", [])          # no such repo: do not burn the rate-limited API on it
        else:
            tree = (branch or "HEAD", files)
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps({"branch": tree[0], "files": files}))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
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
        out: dict[str, str] = {}
        for p in sorted(paths):
            if not p.startswith(prefix) or p.endswith("/"):
                continue
            rest = p[len(prefix):]
            if not rest or (not deep and "/" in rest):
                continue
            out.setdefault(rest.rsplit("/", 1)[-1], raw_github(repo, branch, p))
        return out
    listing = net.json(f"https://api.github.com/repos/{repo}/contents/{subdir}", github=True) or []
    return {f["name"]: f["download_url"] for f in listing
            if f.get("type") == "file" and f.get("download_url")}


# ---------------------------------------------------------------------------------------------
# per-source fetchers: return (Agg | None, url | None, note)

def medsl_precinct(net: Net, year: str, st: str):
    if year == "2020":
        files = net.json(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={DV_2020_PRECINCTS}")
        for f in (files or {}).get("data", {}).get("latestVersion", {}).get("files", []):
            df = f["dataFile"]
            name = df.get("originalFileName") or df["filename"]
            if re.fullmatch(rf"2020[-_]{st.lower()}[-_]precinct[-_]general\.csv", name):
                url = f"{DATAVERSE}/api/access/datafile/{df['id']}"
                path = net.download(url, net.cache / "medsl" / name)
                if not path:
                    return None, url, "Dataverse guestbook required (fill it in a browser and drop the "\
                                      f"file at {net.cache / 'medsl' / name})"
                return aggregate_medsl(_iter_text(path), st), url, ""
        return None, None, "no 2020 file"
    repo = MEDSL_REPO.format(year=year)
    files = repo_files(net, repo, "individual_states")
    if not files:
        extra = (f"; its precinct returns are on Harvard Dataverse ({DV_2016_PRECINCTS}) behind a "
                 "guestbook form, which this script does not fill in" if year == "2016" else "")
        return None, None, f"no MEDSL GitHub repo for {year}{extra}"
    pat = re.compile(rf"(^|[-_]){st.lower()}([-_]|24\.zip$)")
    for name, url in sorted(files.items()):
        if name.endswith((".zip", ".csv")) and (pat.search(name) or name == f"{st.lower()}24.zip"):
            path = net.download(url, net.cache / "medsl" / year / name)
            if path:
                return aggregate_medsl(_iter_text(path), st), url, ""
            return None, url, "download failed"
    return None, None, "state not in MEDSL repo"


_senate_cache: dict[str, Path | None] = {}


def medsl_senate_statewide(net: Net, year: str, st: str):
    if "src" not in _senate_cache:
        meta = net.json(f"{DATAVERSE}/api/datasets/:persistentId/?persistentId={DV_SENATE_STATE}") or {}
        _senate_cache["src"] = None
        for f in meta.get("data", {}).get("latestVersion", {}).get("files", []):
            df = f["dataFile"]
            name = df.get("originalFileName") or df["filename"]
            if re.search(r"senate-state.*\.(csv|tab)$", name):
                url = f"{DATAVERSE}/api/access/datafile/{df['id']}"
                _senate_cache["url"] = url
                _senate_cache["src"] = net.download(url, net.cache / "medsl" / name)
    path = _senate_cache.get("src")
    if not path:
        return None, _senate_cache.get("url"), "senate file unavailable"
    agg = Agg()
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        head = f.readline()
        f.seek(0)
        rd = csv.DictReader(f, delimiter="\t" if "\t" in head else ",")
        for r in rd:
            if r.get("year") != year or r.get("state_po") != st or (r.get("stage") or "").lower() != "gen":
                continue
            cand = (r.get("candidate") or "").strip()
            if not cand or SKIP_CANDIDATE.match(cand):
                continue
            sp = " S" if (r.get("special") or "").lower() == "true" else ""
            agg.add("senate", (f"{st} SEN{sp}", pretty_name(cand),
                               norm_party(r.get("party_simplified")), ""),
                    _int(r.get("candidatevotes")), r.get("mode") or "")
    return agg, _senate_cache.get("url"), ""


def openelections(net: Net, year: str, st: str):
    y = year.split("-")[0]
    repo = f"openelections/openelections-data-{st.lower()}"
    names = repo_files(net, repo, y, deep=True)
    if not names:
        return None, None, "no OpenElections directory"
    picked = pick_openelections_files(list(names), year)
    if not picked:
        return None, None, "no matching OpenElections file"
    merged: dict[str, list[dict]] = {}
    urls: dict[str, str] = {}
    by_date: dict[str, list[list[str]]] = defaultdict(list)
    for group in picked:
        by_date[group[0].split("__")[0]].append(group)     # partitions of one election date
    # Every partition is aggregated on its own (a partition's files hold different parts of the
    # state), then one partition per date is kept: the one reporting the most races. Repos ship a
    # whole-state summary that is sometimes only the statewide offices and sometimes the only
    # complete file; the race count settles it without mixing partitions and double counting.
    for date in sorted(by_date):
        best: tuple[int, dict[str, list[dict]], str] = (0, {}, "")
        for group in by_date[date]:
            agg = Agg()
            used: list[str] = []
            for n in group:
                path = net.download(names[n], net.cache / "openelections" / st / n)
                if not path:
                    continue
                aggregate_openelections(_iter_text(path), st, agg)
                used.append(n)
            races = agg.rows()
            total = sum(len(v) for v in races.values())
            if used and total > best[0]:
                src = names[used[0]] if len(used) == 1 else \
                    f"{names[used[0]]} (+{len(used) - 1} more files)"
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
NYT_PARTY = {"democrat": "DEM", "republican": "REP", "libertarian": "LIB", "green": "GRN",
             "independent": "IND"}


def nyt_timeline_2020(doc: dict, st: str) -> dict[str, list[dict]]:
    out = {}
    for race in doc.get("data", {}).get("races", []):
        if race.get("state_id") != st:
            continue
        office = "SEN" if race.get("office") == "U.S. Senate" else "GOV" if race.get("office") == "Governor" else None
        if not office:
            continue
        key = f"{st} {office}" + (" S" if "special" in (race.get("race_slug") or "") else "")
        cands = {c["candidate_key"]: c for c in race.get("candidates", [])}
        lines = []
        for p in sorted(race.get("timeseries", []), key=lambda p: p["timestamp"]):
            total = p.get("votes") or 0
            cl = [{"name": cands[k]["name_display"] if k in cands else k,
                   "party": NYT_PARTY.get((cands.get(k) or {}).get("party_id", ""), None),
                   "votes": round(share * total)} for k, share in (p.get("vote_shares") or {}).items()]
            if not total:
                continue
            lines.append({"ts": p["timestamp"], "key": key, "source": "nyt", "synthetic": False,
                          "candidates": cl, "total_votes": total, "expected_vote_pct": p.get("eevp"),
                          "url": race.get("url")})
        if lines:
            out[key] = lines
    return out


def nyt_timeline_2024(doc: dict, st: str) -> dict[str, list[dict]]:
    out = {}
    for race in doc.get("races", []):
        office = {"S": "SEN", "G": "GOV"}.get(race.get("office_id"))
        if not office:
            continue
        key = f"{st} {office}" + (" S" if race.get("race_type_id") in ("SG", "X") else "")
        meta = race.get("candidate_metadata", {})
        series = (doc.get("timeseries") or {}).get(race["nyt_id"], [])
        lines = []
        for p in sorted(series, key=lambda p: p["greatest_updated_at"]):
            cl = []
            for c in p.get("candidates", []):
                m = meta.get(c["nyt_id"], {})
                name = " ".join(x for x in (m.get("first_name"), m.get("last_name")) if x) or c["nyt_id"]
                cl.append({"name": name, "party": norm_party((m.get("party") or {}).get("nyt_id")) or None,
                           "votes": (c.get("votes") or {}).get("total", 0)})
            if not p.get("total_votes"):
                continue
            lines.append({"ts": p["greatest_updated_at"], "key": key, "source": "nyt", "synthetic": False,
                          "candidates": cl, "total_votes": p["total_votes"],
                          "expected_vote_pct": p.get("eevp"), "url": race.get("url")})
        if lines:
            out[key] = lines
    return out


def fetch_nyt_timelines(net: Net, data: Path, year: str, st: str) -> list[str]:
    offices = NYT_OFFICES.get(year)
    if not offices:
        return []
    written = []
    st_slug = STATES[st].lower().replace(" ", "-")
    for office in offices.values():
        url = (NYT_2020 if year == "2020" else NYT_2024).format(slug=st_slug, office=office)
        path = net.download(url, net.cache / "nyt" / year / f"{st_slug}-{office}.json")
        if not path:
            continue   # no such race that year (404)
        doc = json.loads(path.read_text())
        series = nyt_timeline_2020(doc, st) if year == "2020" else nyt_timeline_2024(doc, st)
        for key, lines in series.items():
            out = data / "timelines" / year / (key.replace(" ", "_") + ".jsonl")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("".join(json.dumps(x) + "\n" for x in lines))
            written.append(f"{key} ({len(lines)} snapshots) <- {url}")
    return written


# ---------------------------------------------------------------------------------------------

def write_level(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.part")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.rename(path)


def run(data: Path, states: list[str], years: list[str], force: bool = False,
        timelines: bool = True, delay: float = 1.0) -> int:
    net = Net(data / "_cache", delay)
    for year in years:
        mpath = data / year / "manifest.json"
        manifest = json.loads(mpath.read_text()) if mpath.exists() else {"year": year, "states": {}}
        for st in states:
            if st in manifest["states"] and not force:
                print(f"{year} {st}: done already (use --force to redo)")
                continue
            print(f"{year} {st}: fetching")
            entry: dict = {"levels": {}, "notes": []}
            sources = ([("openelections", openelections)] if year.endswith("primary") else
                       [("medsl_precinct", medsl_precinct), ("medsl_senate_statewide", medsl_senate_statewide),
                        ("openelections", openelections)])
            remaining = set(LEVELS)
            for name, fn in sources:
                if not remaining:
                    break
                if name == "medsl_senate_statewide" and "senate" not in remaining:
                    continue
                try:
                    agg, url, note = fn(net, year, st)
                except Exception as e:  # one bad source must not stop the run
                    agg, url, note = None, None, f"error: {e}"
                if note:
                    entry["notes"].append(f"{name}: {note}" + (f" ({url})" if isinstance(url, str) else ""))
                if not agg:
                    continue
                for level, rows in agg.rows().items():
                    if level in remaining and rows:
                        write_level(data / year / st / f"{level}.csv", rows)
                        entry["levels"][level] = {"source": name, "rows": len(rows),
                                                  "url": url.get(level) if isinstance(url, dict) else url,
                                                  "races": len({r["race_key"] for r in rows})}
                        remaining.discard(level)
            for level in sorted(remaining):
                entry["notes"].append(f"{level}: no public data found")
                (data / year / st / f"{level}.csv").unlink(missing_ok=True)   # stale from a --force
            if timelines and not year.endswith("primary"):
                entry["timelines"] = fetch_nyt_timelines(net, data, year, st)
            manifest["states"][st] = entry
            mpath.parent.mkdir(parents=True, exist_ok=True)
            mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True))   # checkpoint per state
            got = ", ".join(f"{k}={v['races']}" for k, v in sorted(entry["levels"].items()))
            print(f"{year} {st}: {got or 'nothing'}; timelines: {len(entry.get('timelines', []))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--states", default="all", help="comma-separated USPS codes (default all)")
    ap.add_argument("--years", default=",".join(YEARS), help=f"comma-separated, from {YEARS}")
    ap.add_argument("--force", action="store_true", help="redo states already in the manifest")
    ap.add_argument("--no-timelines", action="store_true", help="skip NYT election-night series")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests (min 1)")
    a = ap.parse_args(argv)
    states = sorted(STATES) if a.states == "all" else [s.strip().upper() for s in a.states.split(",")]
    bad = [s for s in states if s not in STATES]
    years = [y.strip() for y in a.years.split(",")]
    if bad or any(y not in YEARS for y in years):
        ap.error(f"bad state/year: {bad or years}")
    return run(Path(a.data_dir).expanduser(), states, years, a.force, not a.no_timelines, max(1.0, a.delay))


if __name__ == "__main__":
    sys.exit(main())
