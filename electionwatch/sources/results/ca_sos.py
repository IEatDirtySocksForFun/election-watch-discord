"""California — Secretary of State media JSON bulk files.

California publishes bulk JSON files for each contest family on election night:

    https://sos.ca.gov/media                     media guide / file list
    https://media.sos.ca.gov/media/json-endpoints.csv   file -> URL mapping
    https://media.sos.ca.gov/media/<office>.json        per-office JSON

Each JSON file is a list of race objects. Statewide races have
``raceTitle`` ending in " - Statewide Results"; district races end in
" - Districtwide Results". ``ballot-measures.json`` uses a
``ballot-measures`` key with ``yesVotes``/``noVotes`` instead of
``candidates``.

``ELECTIONS`` maps an ISO election date to the media base URL.  The 2024
general files are no longer served from this location (only Statement of
Vote PDF/XLSX remains), so the adapter is configured for the live 2026
media files and discovers the json-endpoints.csv for future elections.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://media.sos.ca.gov/media"
HUMAN = "https://sos.ca.gov/elections"

#: date -> base URL for media files (None = discover from json-endpoints.csv).
ELECTIONS: dict[str, str | None] = {
    "2026-11-03": BASE,
    "2024-11-05": None,
}

#: Office families to poll. The JSON files are stable paths under the base URL.
FILES: tuple[str, ...] = (
    "governor.json",
    "lieutenant-governor.json",
    "secretary-of-state.json",
    "controller.json",
    "treasurer.json",
    "attorney-general.json",
    "insurance-commissioner.json",
    "superintendent-of-public-instruction.json",
    "ballot-measures.json",
    "us-rep.json",
    "state-senate.json",
    "state-assembly.json",
    "supreme-court.json",
    "court-of-appeal.json",
    "board-of-equalization.json",
)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u2013", "-").strip()).upper()


def _parse_reporting(text: str | None) -> tuple[int | None, int | None]:
    """'100% (23,863 of 23,863) precincts reporting' -> (23863, 23863)."""
    if not text:
        return None, None
    m = re.search(r"\(\s*([\d,]+)\s+of\s+([\d,]+)\s*\)", str(text))
    if not m:
        return None, None
    return c.to_int(m.group(1)), c.to_int(m.group(2))


def _title_office(title: str) -> tuple[str, str | None]:
    """'Governor - Statewide Results' -> ('Governor', 'statewide');
    'United States Representative District 1 - Districtwide Results' ->
    ('United States Representative District 1', 'districtwide')."""
    t = (title or "").strip()
    m = re.match(r"^(.+?)\s+-\s+(Statewide|Districtwide)\s+Results\s*$", t)
    if m:
        return m.group(1).strip(), m.group(2).lower()
    return t, None


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    if n == "GOVERNOR":
        return RaceKey("CA", Level.GOVERNOR, "GOV", None, special)
    if n in ("LIEUTENANT GOVERNOR", "LT. GOVERNOR"):
        return RaceKey("CA", Level.GOVERNOR, "LTGOV", None, special)
    if n == "SECRETARY OF STATE":
        return RaceKey("CA", Level.GOVERNOR, "SOS", None, special)
    if n == "CONTROLLER":
        return RaceKey("CA", Level.GOVERNOR, "COMP", None, special)
    if n in ("TREASURER", "STATE TREASURER"):
        return RaceKey("CA", Level.GOVERNOR, "TREAS", None, special)
    if n == "ATTORNEY GENERAL":
        return RaceKey("CA", Level.GOVERNOR, "AG", None, special)
    if n == "INSURANCE COMMISSIONER":
        return RaceKey("CA", Level.GOVERNOR, "INSCOM", None, special)
    if n in ("SUPERINTENDENT OF PUBLIC INSTRUCTION", "SUPT"):
        return RaceKey("CA", Level.GOVERNOR, "SUPT", None, special)
    m = re.match(r"^UNITED STATES REPRESENTATIVE DISTRICT\s+(\d+)", n)
    if m:
        return RaceKey("CA", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^STATE SENATE DISTRICT\s+(\d+)", n)
    if m:
        return RaceKey("CA", Level.STATE, "SD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^STATE ASSEMBLY MEMBER DISTRICT\s+(\d+)", n)
    if m:
        return RaceKey("CA", Level.STATE, "HD", f"{int(m.group(1)):02d}", special)
    m = re.match(r"^BOARD OF EQUALIZATION MEMBER DISTRICT\s+(\d+)", n)
    if m:
        return c.local_key("CA", f"board-of-equalization-district-{int(m.group(1))}")
    return c.local_key("CA", name)


def _candidates(race: dict) -> list[CandidateResult]:
    out: list[CandidateResult] = []
    if "ballot-measures" in race:
        for item in race["ballot-measures"]:
            name = (item.get("Name") or "").strip()
            number = (item.get("Number") or "").strip()
            label = f"Proposition {number}" if number else name
            yes = c.to_int(item.get("yesVotes"))
            no = c.to_int(item.get("noVotes"))
            out.append(CandidateResult(name=f"{label} - YES", party=None, votes=yes))
            out.append(CandidateResult(name=f"{label} - NO", party=None, votes=no))
        return out
    for item in race.get("candidates") or []:
        name = (item.get("Name") or "").strip()
        if not name:
            continue
        out.append(CandidateResult(
            name=name,
            party=c.norm_party(item.get("Party")),
            votes=c.to_int(item.get("Votes")),
            pct=c.to_pct(item.get("Percent")),
            incumbent=bool(item.get("incumbent")),
        ))
    return out


def parse_ca(
    payloads: dict[str, list[dict]],
    *,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of one or more California media JSON files.

    ``payloads`` maps filename -> list of race objects (the JSON file contents).
    """
    out: list[RaceResult] = []
    seen: set[str] = set()
    for filename, races in payloads.items():
        for race in races:
            title = race.get("raceTitle", "")
            office, scope = _title_office(title)
            if scope not in ("statewide", "districtwide"):
                continue
            cands = _candidates(race)
            if not cands:
                continue
            title = office
            if office == "Ballot Measures":
                title = cands[0].name.split(" - ")[0]
            key = race_key(title)
            if str(key) in seen:
                continue
            seen.add(str(key))
            reporting, total = _parse_reporting(race.get("Reporting"))
            out.append(c.make_result(
                key, title, cands, fetched_at,
                source=CaSos.name,
                url=HUMAN,
                total_votes=sum(x.votes for x in cands),
                precincts_reporting=reporting,
                precincts_total=total,
                source_updated_at=c.parse_updated_at(race.get("ReportingTime")),
            ))
    return out


async def _media_base(client: httpx.AsyncClient, date: str) -> str:
    base = ELECTIONS.get(date)
    if base:
        return base
    raise SourceUnavailable(
        f"CA: no media base URL configured for {date}; "
        f"California media JSON files are election-specific and must be "
        f"configured in ELECTIONS")


async def _fetch_file(
    client: httpx.AsyncClient,
    base: str,
    filename: str,
) -> list[dict]:
    try:
        payload = await c.get_json(client, f"{base}/{filename}")
    except SourceUnavailable as exc:
        raise SourceUnavailable(f"CA: {filename} unavailable ({exc})") from exc
    if not isinstance(payload, list):
        raise SourceUnavailable(f"CA: {filename} is not a JSON list")
    return payload


@register
class CaSos(ResultsSource):
    name = "ca_sos"
    states = frozenset({"CA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        ca_keys = [k for k in keys if k.state == "CA"]
        if not ca_keys:
            return []
        base = await _media_base(client, c.election_date())
        payloads: dict[str, list[dict]] = {}
        for filename in FILES:
            payloads[filename] = await _fetch_file(client, base, filename)
        return c.select(parse_ca(payloads, fetched_at=c.utcnow()), ca_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "CA":
            return []
        base = await _media_base(client, c.election_date())
        payloads: dict[str, list[dict]] = {}
        for filename in FILES:
            payloads[filename] = await _fetch_file(client, base, filename)
        return [
            (r.key, r.title or str(r.key))
            for r in parse_ca(payloads, fetched_at=c.utcnow())
        ]
