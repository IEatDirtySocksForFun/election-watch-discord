"""Missouri — St. Louis County election results export (official county feed).

The Missouri Secretary of State's statewide ENR (`https://enr.sos.mo.gov/`) answers a
Cloudflare challenge to any plain request, so county feeds are the reachable official
alternate. St. Louis County publishes a complete per-precinct CSV export, one directory per
election named `el{YYMMDD}`:

    https://extcontent.stlouisco.com/BOE/eResults/el241105/CSV.csv   (9.3 MB, cp1252)

Columns: precinct, registered voters, ballots cast, contest title/id/party, choice name/id/
party, total votes. One row per (precinct, contest, choice), so votes are summed per choice
and the precincts reporting a contest are counted from the same file.

The CSV uses the county's own contest names, including the odd ones: judicial retention
contests are titled with the judge's name and have YES/NO choices. Statewide offices and
districts map to shared keys; everything else is namespaced `st-louis-county-...`. The
adapter is one county (~13% of Missouri's 2024 vote), so every result carries the
" (partial: N counties)" suffix via the shared county merger.

St. Louis County is one of the state's larger counties but this is NOT statewide coverage:
Jackson, St. Charles, Greene, Clay and Boone run other vendors (livevoterturnout.com HTML).
"""
from __future__ import annotations

import csv
import gzip
import io
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _county

BASE = "https://extcontent.stlouisco.com/BOE/eResults"
HUMAN = "https://enr.sos.mo.gov/"
COUNTY = "St_Louis_County"
SLUG = "st-louis-county"

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "SECRETARY OF STATE": "SOS",
    "STATE TREASURER": "TREAS",
    "ATTORNEY GENERAL": "AG",
    "STATE AUDITOR": "AUD",
}
_SENATE = {"U.S. SENATOR", "US SENATOR", "UNITED STATES SENATOR"}
_HOUSE = re.compile(r"^(?:U\.S\.|US|UNITED STATES) REPRESENTATIVE\b.*?(?:DISTRICT|DIST\.?)\s*(\d+)")
_STATE_SENATE = re.compile(r"^STATE SENATOR\b.*?(?:DISTRICT|DIST\.?)\s*(\d+)")
_STATE_HOUSE = re.compile(r"^STATE REPRESENTATIVE\b.*?(?:DISTRICT|DIST\.?)\s*(\d+)")
#: Statewide ballot measures: "STATE - CONSTITUTIONAL AMENDMENT NO. 2", "STATE - PROPOSITION A".
_STATE_MEASURE = re.compile(r"^STATE - (CONSTITUTIONAL AMENDMENT NO\.? ?\w+|PROPOSITION ?\w+)$")


def election_dir(date: str) -> str:
    """'2024-11-05' -> 'el241105' (the county's directory naming)."""
    return "el" + date[2:4] + date[5:7] + date[8:10]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def shared_key(name: str) -> RaceKey | None:
    """Key for a statewide race/measure, or None for a county-local contest."""
    n = _norm(name)
    if n in _SENATE:
        return RaceKey("MO", Level.SENATE, "SEN")
    m = _HOUSE.match(n)
    if m:
        return RaceKey("MO", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    if n in _STATEWIDE:
        return RaceKey("MO", Level.GOVERNOR, _STATEWIDE[n])
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey("MO", Level.STATE, "SD", f"{int(m.group(1)):02d}")
    m = _STATE_HOUSE.match(n)
    if m:
        return RaceKey("MO", Level.STATE, "HD", f"{int(m.group(1)):02d}")
    m = _STATE_MEASURE.match(n)
    if m:
        return c.local_key("MO", m.group(1).lower().replace(".", ""))
    return None


def race_key(name: str) -> RaceKey:
    """Every contest gets a key; unrecognised ones become MO LOCAL st-louis-county-<race>."""
    shared = shared_key(name)
    if shared is not None:
        return shared
    # "ST. LOUIS COUNTY - PROPOSITION A" is already qualified; don't repeat the county.
    local = re.sub(r"^(?:ST\.? LOUIS COUNTY|ST LOUIS CO)\s*-\s*", "", _norm(name))
    return c.local_key("MO", f"{SLUG}-{local}")


def _votes(value) -> int:
    return c.to_int((value or "").replace(",", "")) or 0


def parse_csv(text: str, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the St. Louis County per-precinct CSV export.

    Rows are (precinct, contest, choice, votes); votes are summed per choice and the
    precincts that reported the contest are counted. Unfiled write-ins (empty party, zero
    votes, choice id 0) are dropped, as they are noise from the export.
    """
    tally: dict[str, dict[str, list]] = {}
    precincts: dict[str, set[str]] = {}
    titles: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        title = (row.get("Contest Title") or "").strip()
        name = (row.get("Choice Name") or "").strip()
        if not title or not name:
            continue
        votes = _votes(row.get("Total Votes"))
        party = c.norm_party((row.get("Choice Party") or "").strip() or None)
        choice_id = (row.get("Choice ID") or "").strip()
        if votes == 0 and party is None and choice_id in ("0", ""):
            continue
        key = race_key(title)
        ident = str(key)
        titles.setdefault(ident, title)
        cand_name, incumbent = c.strip_incumbent(name)
        entry = tally.setdefault(ident, {})
        slot = entry.get(cand_name)
        if slot is None:
            entry[cand_name] = [party, votes, incumbent]
        else:
            slot[1] += votes
            slot[0] = slot[0] or party
            slot[2] = slot[2] or incumbent
        precinct = (row.get("#Precinct") or "").strip()
        if precinct:
            precincts.setdefault(ident, set()).add(precinct)

    out: list[RaceResult] = []
    for ident, cands in tally.items():
        results = [
            CandidateResult(name=name, party=party, votes=votes, pct=None, incumbent=inc)
            for name, (party, votes, inc) in cands.items()
        ]
        out.append(c.make_result(
            race_key(titles[ident]), titles[ident], results, fetched_at,
            source=MoStLouis.name,
            url=HUMAN,
            total_votes=sum(x.votes for x in results),
            precincts_reporting=len(precincts.get(ident, ())) or None,
            precincts_total=None,
        ))
    return out


async def _download(client: httpx.AsyncClient, date: str) -> str:
    url = f"{BASE}/{election_dir(date)}/CSV.csv"
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"mo_stlouis: {url}: {exc}") from exc
    if resp.status_code == 404:
        raise SourceUnavailable(f"mo_stlouis: no county export at {url}")
    if resp.status_code != 200:
        raise SourceUnavailable(f"mo_stlouis: {url}: HTTP {resp.status_code}")
    raw = resp.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("cp1252", errors="replace")


@register
class MoStLouis(ResultsSource):
    name = "mo_stlouis"
    states = frozenset({"MO"})
    scrape = False
    enabled_by_default = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "MO" for k in keys):
            return []
        text = await _download(client, c.election_date())
        results = parse_csv(text, fetched_at=c.utcnow())
        return c.select(_county.merge(results, source=self.name, url=HUMAN), keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "MO":
            return []
        text = await _download(client, c.election_date())
        out: list[tuple[RaceKey, str]] = []
        seen: set[str] = set()
        for r in parse_csv(text, fetched_at=c.utcnow()):
            if str(r.key) not in seen:
                seen.add(str(r.key))
                out.append((r.key, r.title or str(r.key)))
        return out
