"""Washington — Secretary of State statewide results export (results.vote.wa.gov).

The results site serves one folder per election and a plain-XML export of every
contest, no auth and no JavaScript:

    https://results.vote.wa.gov/results/<YYYYMMDD>/
    https://results.vote.wa.gov/results/<YYYYMMDD>/export/<YYYYMMDD>_AllState.xml

The AllState export contains statewide executive offices, federal offices,
legislative districts, judicial positions and ballot measures. Rows are one
candidate per contest:

    <Result>
      <RaceName>LEGISLATIVE DISTRICT 1 - State Senator</RaceName>
      <Candidate>John Smith</Candidate>
      <Party>REP</Party>
      <Votes>12345</Votes>
      <PercentageOfTotalVote>52.31</PercentageOfTotalVote>
      <JurisdictionName>Legislative</JurisdictionName>
    </Result>

`Party` is blank for measures (choices are `Yes` / `No`). The export carries no
precinct counts and no winner flags, so those fields stay None/False.

Washington elects two state representatives per legislative district, one per
"position". RaceKey has no position field, so position 1 keeps the bare
district key (WA HD-01) and position 2 gets the suffix P2 (WA HD-01P2); both
keys are stable across elections.

Discovery: the folder is the election date as YYYYMMDD, so a new election needs
no id mapping. Until the state publishes the folder for a date, the adapter
reports itself unavailable (2026 general was a 404 on 2026-10-27).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://results.vote.wa.gov/results"
HUMAN = BASE + "/"

ELECTIONS: dict[str, str | None] = {
    # date -> folder. None = derive from the tracked date (YYYYMMDD).
    "2024-11-05": "20241105",
    "2026-11-03": None,  # not published yet on 2026-10-27 (folder 404)
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LT. GOVERNOR": "LTGOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
    "STATE TREASURER": "TREAS",
    "INSURANCE COMMISSIONER": "INSCOM",
    "SUPERINTENDENT OF PUBLIC INSTRUCTION": "SUPT",
    "COMMISSIONER OF PUBLIC LANDS": "LANDCOM",
}

_CONGRESS = re.compile(r"^CONGRESSIONAL DISTRICT\s+(\d+)\s*-\s*U\.S\. REPRESENTATIVE$", re.I)
_LEG_SEN = re.compile(r"^LEGISLATIVE DISTRICT\s+(\d+)\s*-\s*STATE SENATOR$", re.I)
_LEG_REP = re.compile(
    r"^LEGISLATIVE DISTRICT\s+(\d+)\s*-\s*STATE REPRESENTATIVE"
    r"(?:\s+POS\.?\s*(\d+))?$",
    re.I,
)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def _party(value: str | None) -> str | None:
    """WA prints the ballot label: '(Prefers Democratic Party)', '(States No Party Preference)'."""
    raw = (value or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if "democratic" in low:
        return "DEM"
    if "republican" in low or "gop" in low:
        return "REP"
    if "libertarian" in low:
        return "LIB"
    if "green" in low:
        return "GRN"
    if "no party preference" in low:
        return "NPA"
    if "independent" in low:
        return "IND"
    return c.norm_party(re.sub(r"^\(prefers\s+|\)$|party nominees$", "", raw, flags=re.I))


def _folder(date: str) -> str:
    pinned = ELECTIONS.get(date)
    if pinned:
        return pinned
    y, m, d = (int(x) for x in date.split("-"))
    return f"{y:04d}{m:02d}{d:02d}"


def race_key(name: str) -> RaceKey:
    """Map a WA export RaceName to a RaceKey."""
    n = _norm(name)
    if n in ("U.S. SENATOR", "UNITED STATES SENATOR"):
        return RaceKey("WA", Level.SENATE, "SEN")
    if n in ("PRESIDENT/VICE PRESIDENT", "PRESIDENT AND VICE PRESIDENT"):
        return c.local_key("WA", n)
    if n in _STATEWIDE:
        return RaceKey("WA", Level.GOVERNOR, _STATEWIDE[n])
    m = _CONGRESS.match(n)
    if m:
        return RaceKey("WA", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    m = _LEG_SEN.match(n)
    if m:
        return RaceKey("WA", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _LEG_REP.match(n)
    if m:
        district = f"{int(m.group(1)):02d}"
        pos = int(m.group(2) or 1)
        # Position 1 is the plain district; position 2 needs its own stable key.
        return RaceKey("WA", Level.STATE, "HD", district if pos == 1 else f"{district}P{pos}")
    return c.local_key("WA", n)


def _rows(root: ET.Element) -> list[dict]:
    out = []
    for row in root.iter("Result"):
        out.append(
            {
                "race": (row.findtext("RaceName") or "").strip(),
                "candidate": (row.findtext("Candidate") or "").strip(),
                "party": (row.findtext("Party") or "").strip() or None,
                "votes": row.findtext("Votes"),
                "pct": row.findtext("PercentageOfTotalVote"),
            }
        )
    return out


def parse_wa(xml_text: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the AllState XML export (one row per candidate)."""
    raw = (xml_text or "").strip()
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SourceUnavailable("WA: results export is not well-formed XML") from exc

    grouped: dict[str, dict] = {}
    for row in _rows(root):
        name = row["race"]
        if not name:
            continue
        bucket = grouped.setdefault(name, {"key": race_key(name), "cands": []})
        cand_name, incumbent = c.strip_incumbent(row["candidate"])
        if not cand_name:
            continue
        bucket["cands"].append(
            CandidateResult(
                name=cand_name,
                party=_party(row["party"]),
                votes=c.to_int(row["votes"]),
                pct=c.to_pct(row["pct"]),
                incumbent=incumbent,
                winner=False,  # the export publishes no winner flags
            )
        )

    out: list[RaceResult] = []
    seen: set[str] = set()
    for name, bucket in grouped.items():
        if not bucket["cands"] or str(bucket["key"]) in seen:
            continue
        seen.add(str(bucket["key"]))
        out.append(
            c.make_result(
                bucket["key"],
                name,
                bucket["cands"],
                fetched_at,
                source=WaSos.name,
                url=HUMAN,
                total_votes=sum(x.votes for x in bucket["cands"]),
            )
        )
    return out


@register
class WaSos(ResultsSource):
    name = "wa_sos"
    states = frozenset({"WA"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "WA" for k in keys):
            return []
        folder = _folder(c.election_date())
        url = f"{BASE}/{folder}/export/{folder}_AllState.xml"
        try:
            text = await c.get_text(client, url)
        except SourceUnavailable as exc:
            raise SourceUnavailable(
                f"wa_sos: {url} is not published yet ({exc}); Washington posts the "
                f"export under the election date folder on election night"
            ) from exc
        parsed = parse_wa(text, fetched_at=c.utcnow())
        return c.select(parsed, [k for k in keys if k.state == "WA"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "WA":
            return []
        folder = _folder(c.election_date())
        url = f"{BASE}/{folder}/export/{folder}_AllState.xml"
        try:
            text = await c.get_text(client, url)
        except SourceUnavailable:
            return []
        return [(r.key, r.title or str(r.key)) for r in parse_wa(text, fetched_at=c.utcnow())]
