"""Texas — Secretary of State election-night results (results.texas-election.com).

The site is a static-JSON SPA. Everything hangs off
``https://results.texas-election.com/static/data/``:

    version.json                              {"___versionNo": 404}
    ElectionConstants_<ver>.json              every election the SOS has published
    election/<election_id>/version.json       {"___versionNo": 1012, "elecDate": "11052024"}
    election/<election_id>/<ver>/Home.json    statewide precinct/county reporting
    election/<election_id>/<ver>/Race.json    office list (for list_races)
    election/<election_id>/<ver>/Federal.json, StateWide.json,
                                  Districted.json, StateWideQ.json

Federal.json covers president/Senate/US House, StateWide.json the statewide executives and
the two high courts, Districted.json the legislature, SBOE, courts of appeals and district
courts, StateWideQ.json constitutional amendments (Races is ``null`` if there are none).

The 2026 general election is not in ElectionConstants yet (checked 2026-10-27), so the ID is
discovered at runtime; 2024 is pinned so the fixture never depends on discovery.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://results.texas-election.com/static/data"
HUMAN = "https://results.texas-election.com/races"

#: Human-facing general-election URLs are "races"; results are the same no matter the tab.

ELECTIONS: dict[str, dict | None] = {
    # date -> pinned election id/version, or None = discover from ElectionConstants.
    "2024-11-05": {"election_id": "49664", "version": "1012"},
    "2026-11-03": None,  # TODO: fill {"election_id": ..., "version": ...} once published.
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "COMPTROLLER OF PUBLIC ACCOUNTS": "COMP",
    "COMMISSIONER OF AGRICULTURE": "AGCOM",
    "COMMISSIONER OF THE GENERAL LAND OFFICE": "LANDCOM",
    "RAILROAD COMMISSIONER": "RRC",
}

_DISTRICT_LOCAL = (
    (re.compile(r"^JUSTICE, SUPREME COURT, PLACE (\d+)$"), "supreme-court-place-{}"),
    (re.compile(r"^JUDGE, COURT OF CRIMINAL APPEALS, PLACE (\d+)$"), "cca-place-{}"),
    (
        re.compile(r"^MEMBER, STATE BOARD OF EDUCATION, DISTRICT (\d+)(?: - UNEXPIRED TERM)?$"),
        "state-board-of-education-{}",
    ),
)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip().upper())


def race_key(name: str) -> RaceKey:
    """Map a Texas SOS contest name to a RaceKey (always returns one; unknown -> LOCAL)."""
    n = _norm(name)
    if n in ("U. S. SENATOR", "US SENATOR", "UNITED STATES SENATOR"):
        return RaceKey("TX", Level.SENATE, "SEN")
    m = re.match(r"^U\. S\. REPRESENTATIVE DISTRICT (\d+)$", n)
    if m:
        return RaceKey("TX", Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    if n in _STATEWIDE:
        return RaceKey("TX", Level.GOVERNOR, _STATEWIDE[n])
    m = re.match(r"^STATE SENATOR, DISTRICT (\d+)( - UNEXPIRED TERM)?$", n)
    if m:
        return RaceKey("TX", Level.STATE, "SD", f"{int(m.group(1)):02d}", bool(m.group(2)))
    m = re.match(r"^STATE REPRESENTATIVE DISTRICT (\d+)( - UNEXPIRED TERM)?$", n)
    if m:
        return RaceKey("TX", Level.STATE, "HD", f"{int(m.group(1)):02d}", bool(m.group(2)))
    for pattern, fmt in _DISTRICT_LOCAL:
        m = pattern.match(n)
        if m:
            return c.local_key("TX", fmt.format(m.group(1).lstrip("0") or "0"))
    return c.local_key("TX", n)


def _candidates(race: dict) -> list[CandidateResult]:
    out = []
    for cand in race.get("Candidates") or []:
        name, incumbent = c.strip_incumbent(str(cand.get("N") or "").strip())
        if not name:
            continue
        out.append(
            CandidateResult(
                name=name,
                party=c.norm_party(cand.get("P")),
                votes=c.to_int(cand.get("V")),
                pct=c.to_pct(cand.get("PE")),
                incumbent=incumbent,
                # Texas does not declare winners in these files (see Race.json for the call).
                winner=False,
            )
        )
    return out


def parse_tx(
    payloads: dict[str, dict | None],
    home: dict | None = None,
    *,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of the Texas static JSON files.

    `payloads` keys: "Federal", "StateWide", "Districted", "StateWideQ".
    """
    home = home or {}
    precincts = home.get("PrecinctsReporting") or {}
    pr, pt = c.to_int(precincts.get("PR")), c.to_int(precincts.get("PT"))
    updated = c.parse_updated_at(home.get("LastUpdatedTime"), tz="America/Chicago")

    results: list[RaceResult] = []
    seen: set[str] = set()
    for fname, payload in payloads.items():
        if not isinstance(payload, dict):
            continue
        for race in payload.get("Races") or []:
            title = str(race.get("N") or "").strip()
            if not title:
                continue
            key = race_key(title)
            # A general election can carry the same seat twice only for specials; keep both.
            ident = str(key)
            if ident in seen and not key.special:
                continue
            cands = _candidates(race)
            if not cands:
                continue
            total = c.to_int(race.get("T")) or sum(x.votes for x in cands)
            # Home.json's precinct counts are statewide: a district race would show the
            # whole state's reporting (and expected-vote %) as its own.
            statewide = key.level in (Level.SENATE, Level.GOVERNOR) or fname == "StateWideQ"
            seen.add(ident)
            results.append(
                c.make_result(
                    key,
                    title,
                    cands,
                    fetched_at,
                    source=TxSos.name,
                    url=HUMAN,
                    total_votes=total,
                    precincts_reporting=(pr or None) if statewide else None,
                    precincts_total=(pt or None) if statewide else None,
                    source_updated_at=updated,
                )
            )
    return results


def _data_url(election_id: str, version: str, name: str) -> str:
    return f"{BASE}/election/{election_id}/{version}/{name}"


async def _resolve(client: httpx.AsyncClient, date: str) -> dict:
    pinned = ELECTIONS.get(date)
    if pinned:
        return dict(pinned)
    # Discovery: constants version -> election constants -> per-election data version.
    ver_doc = await c.get_json(client, f"{BASE}/version.json")
    const_ver = str((ver_doc or {}).get("___versionNo") or "").strip()
    if not const_ver:
        raise SourceUnavailable("TX: could not read static/data/version.json")
    constants = await c.get_json(client, f"{BASE}/ElectionConstants_{const_ver}.json")
    year = date[:4]
    for et, entries in ((constants or {}).get("electionInfo", {}).get(year) or {}).items():
        for election_id, info in (entries or {}).items():
            name = str((info or {}).get("N") or "").upper()
            if et == "G" or "GENERAL" in name:
                per = await c.get_json(client, f"{BASE}/election/{election_id}/version.json")
                version = str((per or {}).get("___versionNo") or "").strip()
                if version:
                    return {"election_id": str(election_id), "version": version}
    raise SourceUnavailable(
        f"TX: ElectionConstants_{const_ver}.json has no general election for {year} yet; "
        f"poll https://results.texas-election.com/static/data/ElectionConstants_{const_ver}.json"
    )


async def _optional_json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        return await c.get_json(client, url)  # type: ignore[return-value]
    except SourceUnavailable:
        return None


@register
class TxSos(ResultsSource):
    name = "tx_sos"
    states = frozenset({"TX"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(k.state == "TX" for k in keys):
            return []
        date = c.election_date()
        election = await _resolve(client, date)
        eid, ver = election["election_id"], election["version"]
        home = await _optional_json(client, _data_url(eid, ver, "Home.json"))
        payloads: dict[str, dict | None] = {}
        for name in ("Federal", "StateWide", "Districted", "StateWideQ"):
            payloads[name] = await _optional_json(client, _data_url(eid, ver, f"{name}.json"))
        if not any(payloads.values()):
            raise SourceUnavailable(f"TX: no result files for election {eid} v{ver}")
        results = parse_tx(payloads, home, fetched_at=c.utcnow())
        return c.select(results, [k for k in keys if k.state == "TX"])

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "TX":
            return []
        date = c.election_date()
        election = await _resolve(client, date)
        doc = await _optional_json(client, _data_url(election["election_id"], election["version"], "Race.json"))
        out: list[tuple[RaceKey, str]] = []
        for office_type in (doc or {}).get("OfficeTypes") or []:
            for race in office_type.get("Races") or []:
                title = str(race.get("N") or "").strip()
                if title:
                    out.append((race_key(title), title))
        return out
