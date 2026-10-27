"""Shared data model. Every source adapter returns these; the bot and store consume them.

Keep this file stable: all work packages code against it. Times are timezone-aware UTC.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum


class Level(str, Enum):
    """Navigator tabs (Congressional, Senate, State, Local) plus statewide executive."""
    HOUSE = "house"          # US House
    SENATE = "senate"        # US Senate
    GOVERNOR = "governor"    # governor + other statewide executive offices
    STATE = "state"          # state legislature (state senate / state house)
    LOCAL = "local"          # county, municipal, school board, ballot measures, ...


@dataclass(frozen=True)
class RaceKey:
    """Canonical race identity. Build with electionwatch.racekeys.parse(), print with str()."""
    state: str                    # USPS code, "TX"
    level: Level
    office: str                   # "SEN", "GOV", "AG", "HOUSE", "SD", "HD", or a local slug
    district: str | None = None   # "01", "AL" (at-large), "12", local sub-key
    special: bool = False         # special election (second race for the same seat)

    def __str__(self) -> str:
        from electionwatch.racekeys import format_key
        return format_key(self)


@dataclass
class CandidateResult:
    name: str
    party: str | None             # "DEM", "REP", "LIB", "GRN", "IND", "NPA", ...
    votes: int
    pct: float | None = None      # 0-100; computed by RaceResult.finalize() if None
    incumbent: bool = False
    winner: bool = False          # only as declared by the *source* (official/certified)


@dataclass
class RaceResult:
    key: RaceKey
    candidates: list[CandidateResult]
    source: str                              # adapter name, e.g. "tx_sos"
    fetched_at: datetime
    source_updated_at: datetime | None = None
    precincts_reporting: int | None = None
    precincts_total: int | None = None
    expected_vote_pct: float | None = None   # 0-100 if the source estimates it
    total_votes: int | None = None
    registered_voters: int | None = None     # for turnout
    turnout_pct: float | None = None         # 0-100; total ballots / registered
    url: str | None = None                   # human-readable results page
    title: str | None = None                 # source's own race name, e.g. "U.S. Senate"

    def finalize(self) -> "RaceResult":
        tot = sum(c.votes for c in self.candidates)
        if self.total_votes is None:
            self.total_votes = tot
        for c in self.candidates:
            if c.pct is None and tot:
                c.pct = round(100.0 * c.votes / tot, 2)
        if self.turnout_pct is None and self.registered_voters and self.total_votes:
            self.turnout_pct = round(100.0 * self.total_votes / self.registered_voters, 2)
        self.candidates.sort(key=lambda c: c.votes, reverse=True)
        return self

    def fingerprint(self) -> str:
        """Changes iff the vote picture changed. Used for "updated_results == true"."""
        core = {
            "k": str(self.key),
            "c": sorted((c.name, c.votes) for c in self.candidates),
            "p": self.precincts_reporting,
            "e": None if self.expected_vote_pct is None else round(self.expected_vote_pct, 1),  # as displayed
        }
        return hashlib.sha1(json.dumps(core, sort_keys=True).encode()).hexdigest()


@dataclass
class RaceCall:
    key: RaceKey
    outlet: str                   # "AP", "CBS", "FOX", "NBC", "CNN", "ABC", "DDHQ", "VOTEHUB", "NYT"
    winner: str                   # candidate name as the outlet prints it
    party: str | None
    called_at: datetime
    url: str | None = None
    retracted: bool = False


@dataclass
class OddsSnapshot:
    key: RaceKey
    market: str                   # "kalshi" | "polymarket"
    outcome: str                  # party or candidate the price is for, e.g. "REP"
    prob: float                   # 0-1 implied probability (last trade or mid)
    ts: datetime
    market_id: str | None = None
    url: str | None = None


@dataclass
class ExitPoll:
    key: RaceKey
    outlet: str
    url: str
    summary: str | None = None    # 1-3 short lines, condensed; None = link only
    fetched_at: datetime | None = None


@dataclass
class PollResult:
    name: str                     # candidate (or "Generic D")
    party: str | None
    pct: float                    # 0-100


@dataclass
class Poll:
    key: RaceKey
    pollster: str
    results: list[PollResult]
    end_date: date                # last field date
    start_date: date | None = None
    sample_size: int | None = None
    population: str | None = None # "LV", "RV", "A"
    sponsor: str | None = None    # who paid for it
    partisan: str | None = None   # "DEM"/"REP" for internal / partisan-sponsored polls, else None
    source: str = ""              # adapter name, e.g. "270towin"
    url: str | None = None

    @property
    def internal(self) -> bool:
        return self.partisan is not None


@dataclass
class ImportantRace:
    """One entry of the guild's watch list (config/races.yaml or /watch add)."""
    key: RaceKey
    label: str | None = None      # display override, "Texas Senate"
    notes: str | None = None


def to_doc(obj) -> dict:
    """dataclass -> Mongo/JSON-safe dict (RaceKey becomes its string form)."""
    def conv(v):
        if isinstance(v, RaceKey):
            return str(v)
        if isinstance(v, Enum):
            return v.value
        if isinstance(v, list):
            return [conv(x) for x in v]
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v
    return {k: conv(v) for k, v in asdict(obj).items()} | (
        {"key": str(obj.key)} if hasattr(obj, "key") else {}
    )




_PARTY_ALIASES = {
    "R": "REP", "GOP": "REP", "REPUBLICAN": "REP", "REP": "REP",
    "D": "DEM", "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM", "DFL": "DEM", "DEM": "DEM",
    "L": "LIB", "LIBERTARIAN": "LIB", "G": "GRN", "GREEN": "GRN",
    "I": "IND", "INDEPENDENT": "IND", "NONPARTISAN": "NP",
}


def normalize_party(party: str | None) -> str | None:
    """Canonical party code (REP/DEM/LIB/GRN/IND/...). Adapters should store this form."""
    if not party:
        return None
    p = party.strip().upper().rstrip(".")
    return _PARTY_ALIASES.get(p, p)
