"""Clarity / Scytl election-night reporting (results.enr.clarityelections.com).

Many counties and a few states publish through the same vendor. Every feed has a stable
base URL and a data version that changes on each upload:

    https://results.enr.clarityelections.com/<ST>/<ElectionID>/current_ver.txt   -> "382192"
    https://results.enr.clarityelections.com/<ST>/<Jurisdiction>/<ElectionID>/...
    .../<version>/json/en/summary.json      contest results
    .../<version>/json/en/electionsettings.json   "websiteupdatedat", entity names

summary.json is a JSON array of contest objects (older builds wrap it in
{"Contests": [...]}; both are accepted):

    {"C": "U.S. SENATOR", "CAT": "Federal", "K": "0301", "VF": 1,
     "TP": 81, "PR": 0,                 # precincts total / reporting
     "CH": ["Juliana Stratton", ...],   # choices
     "P":  ["DEM", "REP", ...],         # party per choice ("" for non-partisan)
     "V":  [0, 0, ...], "PCT": [0, 0, ...], "T": [0],
     "W":  [0, 0, ...]}                 # 1 = the vendor marks the choice the winner

Feeds are per-election, so they are configured rather than discovered. Built-in entries
live in ELECTIONS; CLARITY_FEEDS (JSON) overrides them for any election:

    CLARITY_FEEDS='[{"state":"IL","jurisdiction":"McLean","election_id":"127349"}]'

Entries take state, election_id, optional jurisdiction and optional date. Without a
feed for a requested state the adapter reports itself unavailable instead of guessing
URLs.

Clarity primary feeds split one office into "... - REP" and "... - DEM" contests; those
are skipped (the bot tracks the general election), while anything unrecognised becomes a
LOCAL race so ballot questions and county offices still show up.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://results.enr.clarityelections.com"
HUMAN = BASE + "/"

#: date -> list of feeds.  jurisdiction None = statewide feed (/<ST>/<id>).
ELECTIONS: dict[str, list[dict]] = {
    "2024-11-05": [
        # West Virginia 2024 general (129 contests; precincts 55/53 reporting at capture).
        {"state": "WV", "election_id": "122766"},
    ],
    "2026-11-03": [
        # McLean County, IL — 2026 general ("2026 General Election - Nov 3"),
        # electionsettings websiteupdatedat 10/26/2026.
        {"state": "IL", "jurisdiction": "McLean", "election_id": "127349"},
    ],
}

_PRIMARY_SUFFIX = re.compile(r"\s*-\s*(?:REP|DEM|LIB|GRE|GRN|CON|CST|IND|NPA|NP)\s*$", re.I)

_ABBREV = {
    "U.S. SENATOR": "SEN", "US SENATOR": "SEN", "UNITED STATES SENATOR": "SEN",
    "GOVERNOR": "GOV", "GOV. AND LT. GOV": "GOV",
    "GOVERNOR AND LIEUTENANT GOVERNOR": "GOV",
    "LT. GOV": "LTGOV", "LT. GOVERNOR": "LTGOV", "LIEUTENANT GOVERNOR": "LTGOV",
    "ATT. GEN": "AG", "ATTORNEY GENERAL": "AG",
    "SEC. OF STATE": "SOS", "SECRETARY OF STATE": "SOS",
    "COMPTROLLER": "COMP", "STATE COMPTROLLER": "COMP",
    "TREASURER": "TREAS", "STATE TREASURER": "TREAS",
    "AUDITOR": "AUD", "STATE AUDITOR": "AUD",
}

_CONGRESS = re.compile(
    r"^(?:REP\.?|REPRESENTATIVE|U\.S\. REP\.?|US REP\.?)\s+IN\s+CONGRESS[, ]*"
    r"(?:DISTRICT|DIST\.?|D)\s*(\d+)$")
_STATE_SENATE = re.compile(r"^STATE SENA(?:TE|TOR)\b")
_STATE_HOUSE = re.compile(
    r"^(?:REP\.? IN THE G\.A\.|REP\.? IN GENERAL ASSEMBLY|STATE REPRESENTATIVE|"
    r"STATE HOUSE|HOUSE OF DELEGATES|ASSEMBLY ?MEMBER|REPRESENTATIVE IN THE GENERAL ASSEMBLY)")


def _district_of(name: str) -> str | None:
    """'... DISTRICT 16' / '... D16' / '..., 16th District' -> '16'."""
    m = re.search(r"(?:DISTRICT|DIST\.?|D)\s*(\d+)", name, re.I)
    if not m:
        m = re.search(r"\b(\d+)(?:st|nd|rd|th)\b", name, re.I)
    return f"{int(m.group(1)):02d}" if m else None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()


def race_key(state: str, name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    if n in ("U.S. SENATE", "US SENATE"):
        return RaceKey(state, Level.SENATE, "SEN", None, special)
    if n in _ABBREV:
        office = _ABBREV[n]
        level = Level.SENATE if office == "SEN" else Level.GOVERNOR
        return RaceKey(state, level, office, None, special)
    m = _CONGRESS.match(n) or re.match(
        r"^(?:U\.S\.|US )?HOUSE[, ]*(?:DISTRICT|DIST\.?|D)\s*(\d+)$", n)
    if m:
        return RaceKey(state, Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}", special)
    if "HOUSE OF REPRESENTATIVES" in n or "CONGRESSIONAL DISTRICT" in n:
        dist = c.district_from(n)
        if dist:
            return RaceKey(state, Level.HOUSE, "HOUSE", dist, special)
    m = _STATE_SENATE.match(n)
    if m:
        return RaceKey(state, Level.STATE, "SD", _district_of(n) or "00", special)
    m = _STATE_HOUSE.match(n)
    if m:
        return RaceKey(state, Level.STATE, "HD", _district_of(n) or "00", special)
    return c.local_key(state, n)


_ZONES = {
    "EDT": "America/New_York", "EST": "America/New_York",
    "CDT": "America/Chicago", "CST": "America/Chicago",
    "MDT": "America/Denver", "MST": "America/Denver",
    "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles",
    "AKDT": "America/Anchorage", "AKST": "America/Anchorage",
    "HST": "Pacific/Honolulu", "UTC": "UTC", "GMT": "UTC",
}


def parse_settings(settings) -> datetime | None:
    """Last-updated stamp out of electionsettings.json ('10/26/2026 11:42:44 AM CDT')."""
    if not isinstance(settings, dict):
        return None
    raw = settings.get("websiteupdatedat")
    if not raw:
        return None
    text = str(raw).strip()
    tz = None
    m = re.search(r"\s+([A-Z]{2,5})$", text)
    if m:
        tz = _ZONES.get(m.group(1))
        text = text[: m.start()]
    return c.parse_updated_at(text, tz=tz)


def parse_clarity(
    summary,
    settings=None,
    *,
    state: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of one Clarity summary.json (+ optional electionsettings.json)."""
    contests = summary if isinstance(summary, list) else (
        (summary or {}).get("Contests") or (summary or {}).get("contests") or [])
    updated = parse_settings(settings)
    out: list[RaceResult] = []
    seen: set[str] = set()
    for con in contests:
        if not isinstance(con, dict):
            continue
        name = str(con.get("C") or "").strip()
        if not name or _PRIMARY_SUFFIX.search(name):
            continue
        key = race_key(state, name)
        if str(key) in seen:
            continue
        choices = con.get("CH") or []
        votes = con.get("V") or []
        parties = con.get("P") or []
        pcts = con.get("PCT") or []
        winners = con.get("W") or []
        cands = []
        for i, choice in enumerate(choices):
            cand_name, incumbent = c.strip_incumbent(str(choice or "").strip())
            if not cand_name or cand_name.upper() == "NO CANDIDATE FILED":
                continue
            cands.append(CandidateResult(
                name=cand_name,
                party=c.norm_party(parties[i] if i < len(parties) else None),
                votes=c.to_int(votes[i] if i < len(votes) else 0),
                pct=c.to_pct(pcts[i] if i < len(pcts) else None),
                incumbent=incumbent,
                winner=c.to_int(winners[i] if i < len(winners) else 0) == 1,
            ))
        if not cands:
            continue
        seen.add(str(key))
        total = con.get("T")
        if isinstance(total, list):
            total = total[0] if total else None
        out.append(c.make_result(
            key, name, cands, fetched_at,
            source=Clarity.name,
            url=HUMAN,
            total_votes=c.to_int(total) or sum(x.votes for x in cands),
            precincts_reporting=c.to_int(con.get("PR")) or None,
            precincts_total=c.to_int(con.get("TP")) or None,
            source_updated_at=updated,
        ))
    return out


def feeds(date: str) -> list[dict]:
    """Configured Clarity feeds for an election date (CLARITY_FEEDS wins)."""
    raw = os.environ.get("CLARITY_FEEDS")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError as exc:
            raise SourceUnavailable("CLARITY_FEEDS is not valid JSON") from exc
        if not isinstance(entries, list):
            raise SourceUnavailable("CLARITY_FEEDS must be a JSON list")
        out = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("state") or not entry.get("election_id"):
                raise SourceUnavailable("CLARITY_FEEDS entries need state and election_id")
            if entry.get("date") and entry["date"] != date:
                continue
            out.append(entry)
        return out
    return list(ELECTIONS.get(date, []))


def feed_base(entry: dict) -> str:
    parts = [BASE, str(entry["state"]).upper()]
    if entry.get("jurisdiction"):
        parts.append(str(entry["jurisdiction"]))
    parts.append(str(entry["election_id"]))
    return "/".join(parts)


async def _optional_settings(client: httpx.AsyncClient, url: str):
    try:
        return await c.get_json(client, url)
    except SourceUnavailable:
        return None


def scope(entry: dict, results: list[RaceResult]) -> list[RaceResult]:
    """A county feed (entry with a jurisdiction) only speaks for its local races: its
    U.S. Senate / Governor / House totals cover one county and must not stand in for the
    statewide result (this adapter registers ahead of the state's own adapter)."""
    if not entry.get("jurisdiction"):
        return results
    return [r for r in results if r.key.level is Level.LOCAL]


def configured_states() -> frozenset[str]:
    """States with a configured Clarity feed: the built-in ELECTIONS entries plus
    whatever CLARITY_FEEDS adds. A state without a feed is not covered by this
    adapter, so it must not be advertised as such to the coverage/navigator code."""
    states = {
        str(entry["state"]).upper()
        for entries in ELECTIONS.values() for entry in entries if entry.get("state")
    }
    raw = os.environ.get("CLARITY_FEEDS")
    if raw:
        try:
            entries = json.loads(raw)
        except ValueError:
            entries = []  # feeds() raises the SourceUnavailable; coverage stays truthful
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("state"):
                    states.add(str(entry["state"]).upper())
    return frozenset(states)


class _ConfiguredStates:
    """Descriptor for ``states``: computed from the feeds on every access.

    Callers read ``states`` on the class (the registry, tools) or on an instance
    (the poller, the navigator), so both must work — and CLARITY_FEEDS is read at
    access time because it can be set per deployment.
    """

    def __get__(self, instance, owner=None) -> frozenset[str]:
        return configured_states()


@register
class Clarity(ResultsSource):
    name = "clarity"
    #: Not a hard-coded "Clarity states" list: only configured feeds are covered.
    states = _ConfiguredStates()
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        wanted = {k.state for k in keys}
        date = c.election_date()
        selected = [f for f in feeds(date) if str(f["state"]).upper() in wanted]
        if not selected:
            raise SourceUnavailable(
                f"clarity: no feed configured for {sorted(wanted)} on {date}; "
                f"set CLARITY_FEEDS")
        results: list[RaceResult] = []
        for entry in selected:
            base = feed_base(entry)
            version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
            if not version or not re.fullmatch(r"[\w.\-]+", version):
                raise SourceUnavailable(f"clarity: bad current_ver.txt from {base}")
            summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
            settings = await _optional_settings(
                client, f"{base}/{version}/json/en/electionsettings.json")
            results.extend(scope(entry, parse_clarity(
                summary, settings, state=str(entry["state"]).upper(), fetched_at=c.utcnow())))
        return c.select(results, keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        date = c.election_date()
        for entry in feeds(date):
            if str(entry["state"]).upper() != state.upper():
                continue
            base = feed_base(entry)
            try:
                version = (await c.get_text(client, f"{base}/current_ver.txt")).strip()
                summary = await c.get_json(client, f"{base}/{version}/json/en/summary.json")
            except SourceUnavailable:
                continue
            return [(r.key, r.title or str(r.key))
                    for r in scope(entry, parse_clarity(summary, state=state, fetched_at=c.utcnow()))]
        return []
