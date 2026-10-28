"""Minnesota — Secretary of State results file host (electionresultsfiles.sos.mn.gov).

The SOS results site (electionresults.sos.mn.gov) is behind a Radware bot manager:
every request answers a ``__uzdbm_*`` challenge instead of markup. The numbers the
site renders come from a plain text host that is *not* gated, one file per office
group:

    https://electionresultsfiles.sos.mn.gov/<YYYYMMDD>/ussenate.txt
    .../ushouse.txt                U.S. Representative
    .../stsenate.txt               State Senator
    .../LegislativeByDistrict.txt  State Representative
    .../governor.txt               Governor & Lt Governor
    .../auditor.txt                State Auditor
    .../judicial.txt               Supreme Court / Court of Appeals / District Court
    .../cntyRaceQuestions.txt      county offices
    .../local.txt                  municipal races and city questions
    .../SDRaceQuestions.txt        school board races and school questions
    .../PartyTbl.txt               party code -> name
    .../Cntytbl.txt                county code -> name
    .../BallotQuestions.txt        ballot question text (no votes)

``governor.txt`` and ``auditor.txt`` are the only statewide-executive files the SOS
publishes; there is no Attorney General or Secretary of State export, so those two
races are not available from this host (see docs/results/alt-a.md).

Rows are semicolon-delimited, one per candidate per contest, no header:

    MN;;;0104;U.S. Representative District 1;1;0201;Rachel Bohman;;;DFL;749;749;156375;41.41;377601
     0 1 2     3    4                          5  6    7            8 9  10  11  12 13      14    15

    0 state ("MN")          8-9 unused
    1 county code           10 party code ("DFL", "R", "NP", "WI", ...)
    2 unused                11 precincts reporting for the contest
    3 contest code          12 precincts total for the contest
    4 contest name          13 votes for this candidate
    5 district / seat       14 percent (0-100)
    6 candidate code        15 total votes cast in the contest
    7 candidate name

Precinct counts and the contest total repeat on every row, so they are read once per
contest. Percentages are recomputed by ``RaceResult.finalize()`` when absent.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://electionresultsfiles.sos.mn.gov"
HUMAN = "https://electionresults.sos.mn.gov/"

#: Office-group files fetched by default. ``local.txt``/``SDRaceQuestions.txt`` hold the
#: municipal and school district contests; add them to ``FILES`` if the deployment wants
#: every township race (thousands of keys).
FILES = (
    "ussenate.txt",
    "ushouse.txt",
    "stsenate.txt",
    "LegislativeByDistrict.txt",
    "governor.txt",
    "auditor.txt",
    "judicial.txt",
    "cntyRaceQuestions.txt",
)

_STATEWIDE = {
    "GOVERNOR & LT GOVERNOR": "GOV",
    "GOVERNOR AND LT GOVERNOR": "GOV",
    "ATTORNEY GENERAL": "AG",
    "SECRETARY OF STATE": "SOS",
    "STATE AUDITOR": "AUD",
}

_US_SENATE = ("U.S. SENATOR", "U.S. SENATE", "UNITED STATES SENATOR")
_US_HOUSE = re.compile(r"^U\.S\. REPRESENTATIVE DISTRICT\s+(\d+)$")
_STATE_SENATE = re.compile(r"^STATE SENATOR DISTRICT\s+(\d+)$")
_STATE_HOUSE = re.compile(r"^STATE REPRESENTATIVE DISTRICT\s+(\d+[A-Z]?)$")

_WRITE_IN = "WRITE-IN"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(name: str, district: str | None = None, county: str | None = None) -> RaceKey:
    """Map an MN contest name (+ optional county) to a canonical key."""
    contest_name = _norm(name)
    special = c.is_special(contest_name)
    if contest_name in _US_SENATE:
        return RaceKey("MN", Level.SENATE, "SEN", None, special)
    if contest_name in _STATEWIDE:
        return RaceKey("MN", Level.GOVERNOR, _STATEWIDE[contest_name], None, special)
    match = _US_HOUSE.match(contest_name)
    if match:
        return RaceKey("MN", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}", special)
    match = _STATE_SENATE.match(contest_name)
    if match:
        return RaceKey("MN", Level.STATE, "SD", f"{int(match.group(1)):02d}", special)
    match = _STATE_HOUSE.match(contest_name)
    if match:
        return RaceKey("MN", Level.STATE, "HD", match.group(1), special)
    return c.local_key("MN", f"{county} {name}" if county else name)


def parse_counties(text: str) -> dict[str, str]:
    """Cntytbl.txt -> {"01": "Aitkin", ...}."""
    county_codes: dict[str, str] = {}
    for line in (text or "").splitlines():
        parts = [piece.strip() for piece in line.split(";")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            county_codes[parts[0]] = parts[1]
    return county_codes


def race_title(name: str, county: str | None = None) -> str:
    """Human title, prefixed with the county for county offices."""
    title = re.sub(r"\s+", " ", (name or "").strip())
    return f"{county} {title}" if county else title


def parse_mn(
    files: dict[str, str],
    *,
    fetched_at: datetime,
    counties: dict[str, str] | None = None,
) -> list[RaceResult]:
    """Pure parse of the MnDVS text exports (file name -> file body)."""
    counties = counties or {}
    results: list[RaceResult] = []
    seen: set[str] = set()
    for body in files.values():
        # Rows of one contest always sit together: group in encounter order.
        order: list[tuple[str, str, str]] = []  # (name, district, county code)
        rows: dict[tuple[str, str, str], list[list[str]]] = {}
        for line in (body or "").splitlines():
            fields = [raw_field.strip() for raw_field in line.rstrip("\r").split(";")]
            if len(fields) < 16 or fields[0] != "MN":
                continue
            name, district, county_code = fields[4], fields[5], fields[1]
            group = (name, district, county_code)
            if group not in rows:
                order.append(group)
                rows[group] = []
            rows[group].append(fields)

        for group in order:
            name, district, county_code = group
            county = counties.get(county_code) if county_code else None
            key = race_key(name, district, county)
            if str(key) in seen:
                continue
            cands: list[CandidateResult] = []
            for fields in rows[group]:
                cand_name, incumbent = c.strip_incumbent(fields[7])
                if not cand_name or cand_name.upper() == _WRITE_IN:
                    continue
                cands.append(
                    CandidateResult(
                        name=cand_name,
                        party=c.norm_party(fields[10]),
                        votes=c.to_int(fields[13]),
                        pct=c.to_pct(fields[14]),
                        incumbent=incumbent,
                        winner=False,  # the export carries no winner flag
                    )
                )
            if not cands:
                continue
            seen.add(str(key))
            first = rows[group][0]
            results.append(
                c.make_result(
                    key,
                    race_title(name, county),
                    cands,
                    fetched_at,
                    source=MnSos.name,
                    url=HUMAN,
                    total_votes=c.to_int(first[15]) or None,
                    precincts_reporting=c.to_int(first[11]) or None,
                    precincts_total=c.to_int(first[12]) or None,
                )
            )
    return results


def folder(date: str) -> str:
    """'2026-11-03' -> '20261103' (the SOS names the folder after the election day)."""
    return date.replace("-", "")


def decode_bytes(raw: bytes) -> str:
    """The exports are Windows-1252 with a stray non-UTF-8 byte in a few candidate names."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


async def _optional_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, headers={"Accept": "text/plain, */*"})
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"GET {url} failed: {exc}") from exc
    if resp.status_code >= 400:
        return None  # that office-group file is simply not published
    return decode_bytes(resp.content)


async def _load(client: httpx.AsyncClient, date: str) -> dict[str, str]:
    """Everything the adapter needs for one election date (missing files are skipped)."""
    base = f"{BASE}/{folder(date)}"
    bodies: dict[str, str] = {}
    for name in FILES:
        body = await _optional_text(client, f"{base}/{name}")
        if body:
            bodies[name] = body
    if not bodies:
        raise SourceUnavailable(f"MN: none of the {len(FILES)} results files under {base} are published")
    counties = parse_counties(await _optional_text(client, f"{base}/Cntytbl.txt") or "")
    return {"__counties__": counties} | bodies


@register
class MnSos(ResultsSource):
    name = "mn_sos"
    states = frozenset({"MN"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "MN" for key in keys):
            return []
        payload = await _load(client, c.election_date())
        counties = payload.pop("__counties__", {})
        parsed = parse_mn(payload, fetched_at=c.utcnow(), counties=counties)
        return c.select(parsed, [key for key in keys if key.state == "MN"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MN":
            return []
        payload = await _load(client, c.election_date())
        counties = payload.pop("__counties__", {})
        return [
            (result.key, result.title or str(result.key))
            for result in parse_mn(payload, fetched_at=c.utcnow(), counties=counties)
        ]
