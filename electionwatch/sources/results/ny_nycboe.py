"""New York — New York City Board of Elections recap files (official city feed).

`https://nyenr.elections.ny.gov/` (the state ENR front end) and `results.elections.ny.gov`
answer 403 to a plain request, and the state's data host (`ny.elstats.civera.com`) only
serves per-contest ids with no index, so the state feed cannot be discovered without the
blocked site. New York City's Board of Elections publishes the raw "Statement and Return
Report" recap files for every contest as CSV on its own host:

    https://www.vote.nyc/page/election-results-summary            current year, HTML index
    https://www.vote.nyc/page/election-results-summary-<year>    archive index
    https://www.vote.nyc/sites/default/files/pdf/election_results/
        <YYYY>/<YYYYMMDD><Stage>/<11-digit code><County> <Contest>[ Citywide] Recap.csv

Each file is one contest in one county (New York, Bronx, Kings, Queens, Richmond) or the
citywide aggregate ("Citywide"). Votes are read from the "Total for <contest> - <County>"
block, which holds one row per candidate ("Kirsten E. Gillibrand (Democratic)") plus
machine categories (Public Counter, Absentee / Military, ...) that are skipped.

New York City is the whole of five counties (~35% of New York's 2024 vote) but nowhere near
the rest of the state, so every result carries the " (partial: N counties)" suffix. Only
general elections have single-vote races, so other stages are refused.

Fetching is filtered: the bot's subscription is matched against the contest names in the
index (`keys`), so tracking one congressional district downloads only its two files.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from datetime import datetime
from urllib.parse import quote

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _county

INDEX = "https://www.vote.nyc/page/election-results-summary"
ARCHIVE = INDEX + "-{year}"
HOST = "https://www.vote.nyc"
HUMAN = INDEX

COUNTIES = ("Citywide", "New York", "Bronx", "Kings", "Queens", "Richmond")

#: Non-candidate rows in the "Total for ..." block of the recap files.
_MACHINE = {
    "PUBLIC COUNTER",
    "MANUALLY COUNTED EMERGENCY",
    "ABSENTEE / MILITARY",
    "ABSENTEE",
    "AFFIDAVIT",
    "FEDERAL",
    "EMERGENCY",
    "PROVISIONAL",
    "UNRECORDED",
    "SCANNER",
    "TOTAL VOTES",
}

_LINK = re.compile(r"/sites/default/files/pdf/election_results/(\d{4})/(\d{8})([^/\"]*)/([^\"]+)Recap\.csv")
_STEM = re.compile(r"^\d{11}(" + "|".join(COUNTIES) + r") (.+?)(?: Citywide)?$")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def _district(name: str, kind: str) -> str | None:
    """'26th Senatorial District' -> '26' (district_from only knows 'Nth District' forms)."""
    match = re.search(r"(\d{1,3})(?:ST|ND|RD|TH)\s+" + kind, name)
    if match:
        return f"{int(match.group(1)):02d}"
    return c.district_from(name)


def _clean_contest(name: str) -> str:
    return re.sub(r"^Citywide\s+|\s+Citywide$", "", name.strip()) or name.strip()


def race_key(name: str) -> RaceKey:
    """Every contest gets a key; NYC city offices become NY LOCAL nyc-<race>."""
    office_name = _norm(_clean_contest(name))
    if "UNITED STATES SENATOR" in office_name:
        return RaceKey("NY", Level.SENATE, "SEN")
    if "REPRESENTATIVE IN CONGRESS" in office_name or "CONGRESSIONAL DISTRICT" in office_name:
        district = _district(office_name, "CONGRESSIONAL")
        if district:
            return RaceKey("NY", Level.HOUSE, "HOUSE", district)
    # NY elects governor and lieutenant governor on one ticket: "Governor/Lieutenant Governor".
    if office_name.startswith("GOVERNOR") or (office_name.endswith("GOVERNOR") and "LIEUTENANT" not in office_name):
        return RaceKey("NY", Level.GOVERNOR, "GOV")
    if "LIEUTENANT GOVERNOR" in office_name:
        return RaceKey("NY", Level.GOVERNOR, "LTGOV")
    if "STATE SENATOR" in office_name or "SENATORIAL DISTRICT" in office_name:
        district = _district(office_name, "SENATORIAL")
        if district:
            return RaceKey("NY", Level.STATE, "SD", district)
    if "ASSEMBLY" in office_name:
        district = _district(office_name, "ASSEMBLY")
        if district:
            return RaceKey("NY", Level.STATE, "HD", district)
    if "PRESIDENT" in office_name:
        return c.local_key("NY", "president-vice-president")
    return c.local_key("NY", f"nyc-{office_name}")


def parse_index(html: str, date: str) -> list[dict]:
    """Pure parse of a vote.nyc results index -> [{county, contest, path}] for `date`."""
    stamp = date.replace("-", "")
    entries: list[dict] = []
    seen: set[str] = set()
    for match in _LINK.finditer(html or ""):
        _year, link_date, stage, name = match.groups()
        if link_date != stamp:
            continue
        if stage and "general" not in stage.lower():
            continue  # primary/crossover files are per party and would collide on keys
        stem = _STEM.match(name.strip())
        if not stem:
            continue
        path = match.group(0)
        if path in seen:
            continue
        seen.add(path)
        entries.append({"county": stem.group(1), "contest": _clean_contest(stem.group(2)), "path": path})
    return entries


def _split_party(label: str) -> tuple[str, str | None]:
    """'Kirsten E. Gillibrand (Democratic)' -> ('Kirsten E. Gillibrand', 'DEM')"""
    match = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", label)
    if not match:
        return label.strip(), None
    name, party = match.group(1).strip(), match.group(2).strip()
    if party.lower().replace("-", "") in ("writein", "writein vote"):
        return f"{name} (Write-In)", "WRI"
    return name, c.norm_party(party)


def parse_recap(text: str, *, county: str, contest: str, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of one recap CSV: the candidate rows of its "Total for ..." block."""
    cands: dict[str, list] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or not (row[3] or "").startswith("Total for"):
            continue
        label = (row[4] or "").strip()
        if not label or label.upper() in _MACHINE:
            continue
        votes = c.to_int(row[5])
        name, party = _split_party(label)
        slot = cands.get(name)
        if slot is None:
            cands[name] = [party, votes]
        else:
            slot[1] += votes
            slot[0] = slot[0] or party
    if not cands:
        return []
    key = race_key(contest)
    results = [CandidateResult(name=name, party=party, votes=votes, pct=None) for name, (party, votes) in cands.items()]
    return [
        c.make_result(
            key,
            _clean_contest(contest),
            results,
            fetched_at,
            source=NyNycBoe.name,
            url=HUMAN,
            total_votes=sum(candidate.votes for candidate in results),
        )
    ]


def wanted(entry: dict, keys: list[RaceKey]) -> bool:
    """Does the index entry's contest match one of the subscribed race keys?"""
    key = race_key(entry["contest"])
    return any(str(wanted_key) == str(key) for wanted_key in keys)


def select_entries(entries: list[dict], keys: list[RaceKey]) -> list[dict]:
    """Keep matching contests; when a citywide aggregate exists, use it instead of boroughs."""
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        if not wanted(entry, keys):
            continue
        grouped.setdefault(str(race_key(entry["contest"])), []).append(entry)
    picked: list[dict] = []
    for group in grouped.values():
        citywide = [city_entry for city_entry in group if city_entry["county"] == "Citywide"]
        picked.extend(citywide or group)
    return picked


async def _index_html(client: httpx.AsyncClient, year: str, current_year: str) -> str:
    urls = [INDEX] if year == current_year else [ARCHIVE.format(year=year), INDEX]
    last = ""
    for url in urls:
        try:
            html = await c.get_text(client, url)
        except SourceUnavailable as exc:
            last = str(exc)
            continue
        if "election_results" in html:
            return html
    raise SourceUnavailable(f"ny_nycboe: no results index for {year} ({last})")


async def _one(client: httpx.AsyncClient, entry: dict, fetched_at: datetime) -> list[RaceResult]:
    url = HOST + quote(entry["path"])
    text = await c.get_text(client, url)
    return parse_recap(text, county=entry["county"], contest=entry["contest"], fetched_at=fetched_at)


@register
class NyNycBoe(ResultsSource):
    name = "ny_nycboe"
    states = frozenset({"NY"})
    # The votes come from CSV files, but finding the files means reading the vote.nyc HTML index,
    # so this is treated as a scrape: off unless an operator enables it.
    scrape = True
    enabled_by_default = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "NY" for key in keys):
            return []
        date = c.election_date()
        fetched_at = c.utcnow()
        html = await _index_html(client, date[:4], str(c.utcnow().year))
        entries = select_entries(parse_index(html, date), keys)
        if not entries:
            raise SourceUnavailable(
                f"ny_nycboe: no New York City recap files for {date} covering {', '.join(str(key) for key in keys)}"
            )
        gate = asyncio.Semaphore(4)
        results: list[RaceResult] = []

        async def run(entry: dict) -> None:
            async with gate:
                try:
                    results.extend(await _one(client, entry, fetched_at))
                except SourceUnavailable:
                    return

        await asyncio.gather(*(run(entry) for entry in entries))
        if not results:
            raise SourceUnavailable("ny_nycboe: every matched recap file failed to download")
        return c.select(_county.merge(results, source=self.name, url=HUMAN), keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "NY":
            return []
        date = c.election_date()
        html = await _index_html(client, date[:4], str(c.utcnow().year))
        found: list[tuple[RaceKey, str]] = []
        seen: set[str] = set()
        for entry in parse_index(html, date):
            key = race_key(entry["contest"])
            if str(key) not in seen:
                seen.add(str(key))
                found.append((key, _clean_contest(entry["contest"])))
        return found
