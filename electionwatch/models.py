"""Shared data model. Every source adapter returns these; the bot and store consume them.

Keep this file stable: adapters, storage and Discord code all rely on it. Times are UTC-aware.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum


class Level(str, Enum):
    """Navigator tabs (Congressional, Senate, State, Local) plus statewide executive."""

    HOUSE = "house"  # US House
    SENATE = "senate"  # US Senate
    GOVERNOR = "governor"  # governor + other statewide executive offices
    STATE = "state"  # state legislature (state senate / state house)
    LOCAL = "local"  # county, municipal, school board, ballot measures, ...


@dataclass(frozen=True)
class RaceKey:
    """Canonical race identity. Build with electionwatch.racekeys.parse(), print with str()."""

    state: str  # USPS code, "TX"
    level: Level
    office: str  # "SEN", "GOV", "AG", "HOUSE", "SD", "HD", or a local slug
    district: str | None = None  # "01", "AL" (at-large), "12", local sub-key
    special: bool = False  # special election (second race for the same seat)

    def __post_init__(self) -> None:
        # Single-seat states: feeds number the seat "1" / "01" / "0", the bot calls it AL.
        # Normalising here makes every adapter, user input and history row agree.
        if self.level is Level.HOUSE and self.district in _SEAT_ONE and self.state in at_large_states():
            object.__setattr__(self, "district", "AL")

    def __str__(self) -> str:
        from electionwatch.racekeys import format_key

        return format_key(self)


_SEAT_ONE = frozenset({"0", "00", "1", "01"})
# One House seat (DC and territories: one non-voting member). Montana had one seat until
# the 2022 reapportionment; the election year comes from ELECTION_DATE (default 2026).
_AT_LARGE = frozenset({"AK", "DE", "ND", "SD", "VT", "WY", "DC", "PR", "GU", "VI", "AS", "MP"})


def at_large_states() -> frozenset[str]:
    year = int((os.environ.get("ELECTION_DATE") or "2026")[:4])
    return _AT_LARGE | {"MT"} if year < 2022 else _AT_LARGE


@dataclass
class CandidateResult:
    name: str
    party: str | None  # "DEM", "REP", "LIB", "GRN", "IND", "NPA", ...
    votes: int
    pct: float | None = None  # 0-100; computed by RaceResult.finalize() if None
    incumbent: bool = False
    winner: bool = False  # only as declared by the *source* (official/certified)


@dataclass
class RaceResult:
    key: RaceKey
    candidates: list[CandidateResult]
    source: str  # adapter name, e.g. "tx_sos"
    fetched_at: datetime
    source_updated_at: datetime | None = None
    precincts_reporting: int | None = None
    precincts_total: int | None = None
    expected_vote_pct: float | None = None  # 0-100 if the source estimates it
    total_votes: int | None = None
    registered_voters: int | None = None  # for turnout
    turnout_pct: float | None = None  # 0-100; total ballots / registered
    url: str | None = None  # human-readable results page
    title: str | None = None  # source's own race name, e.g. "U.S. Senate"

    def finalize(self) -> "RaceResult":
        # Feeds mix ballot statistics into the candidate list ("Blank Ballots", "Times Over
        # Voted", "Registered Voters"...). Drop them so they neither show up as candidates nor
        # dilute the percentages; keep a registered-voter count for turnout.
        kept = []
        for c in self.candidates:
            label = c.name.strip().strip(":*").strip().lower()  # "Overvotes:", "Write-In**"
            if _NOT_A_CANDIDATE.fullmatch(label):
                if label.startswith("registered") and self.registered_voters is None:
                    self.registered_voters = c.votes
                continue
            c.name = pretty_name(c.name)
            kept.append(c)
        if len(kept) != len(self.candidates):
            for c in kept:
                c.pct = None  # the source's shares included the dropped rows
            self.candidates = kept
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
    outlet: str  # "AP", "CBS", "FOX", "NBC", "CNN", "ABC", "DDHQ", "VOTEHUB", "NYT"
    winner: str  # candidate name as the outlet prints it
    party: str | None
    called_at: datetime
    url: str | None = None
    retracted: bool = False


@dataclass
class OddsSnapshot:
    key: RaceKey
    market: str  # "kalshi" | "polymarket"
    outcome: str  # party or candidate the price is for, e.g. "REP"
    prob: float  # 0-1 implied probability (last trade or mid)
    ts: datetime
    market_id: str | None = None
    url: str | None = None


@dataclass
class ExitPoll:
    key: RaceKey
    outlet: str
    url: str
    summary: str | None = None  # 1-3 short lines, condensed; None = link only
    fetched_at: datetime | None = None


@dataclass
class PollResult:
    name: str  # candidate (or "Generic D")
    party: str | None
    pct: float  # 0-100


@dataclass
class Poll:
    key: RaceKey
    pollster: str
    results: list[PollResult]
    end_date: date  # last field date
    start_date: date | None = None
    sample_size: int | None = None
    population: str | None = None  # "LV", "RV", "A"
    sponsor: str | None = None  # who paid for it
    partisan: str | None = None  # "DEM"/"REP" for internal / partisan-sponsored polls, else None
    source: str = ""  # adapter name, e.g. "270towin"
    url: str | None = None

    @property
    def internal(self) -> bool:
        return self.partisan is not None


@dataclass
class ImportantRace:
    """One entry of the guild's watch list (config/races.yaml or /watch add)."""

    key: RaceKey
    label: str | None = None  # display override, "Texas Senate"
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

    return {k: conv(v) for k, v in asdict(obj).items()} | ({"key": str(obj.key)} if hasattr(obj, "key") else {})


_PARTY_ALIASES = {
    "R": "REP",
    "GOP": "REP",
    "REPUBLICAN": "REP",
    "REP": "REP",
    "D": "DEM",
    "DEMOCRAT": "DEM",
    "DEMOCRATIC": "DEM",
    "DFL": "DEM",
    "DEM": "DEM",
    "L": "LIB",
    "LIBERTARIAN": "LIB",
    "G": "GRN",
    "GREEN": "GRN",
    "I": "IND",
    "INDEPENDENT": "IND",
    "NONPARTISAN": "NP",
}


def normalize_party(party: str | None) -> str | None:
    """Canonical party code (REP/DEM/LIB/GRN/IND/...). Adapters should store this form."""
    if not party:
        return None
    p = party.strip().upper().rstrip(".")
    return _PARTY_ALIASES.get(p, p)


# Ballot statistics some feeds list as if they were candidates. Real ballot options (Nevada's
# "None of These Candidates") and counted write-ins are kept.
_NOT_A_CANDIDATE = re.compile(
    r"(times? )?(blank|over|under)( ?voted| ?votes?| ballots?)?|blank/void|void|spoiled( ballots)?"
    r"|(total )?ballots cast|registered voters|total( votes)?( cast)?|unqualified write[- ]?ins?|scattering"
    r"|not qualified( \d+)?|blanks?|voids?|cast votes|invalid( votes| ballots)?|rejected write[- ]?ins?"
    r"|((absentee|early|election day|mail)( voting)? )?ballots cast|federal ballots|special votes"
    r"|(bvs |contest )?sub-?totals?|(contest )?totals?|scatter(ed|ings?)( votes)?|write[- ]?ins?: scattered"
    r"|blank write[- ]?ins?|(federal|state) (votes|ballots)|(under|over)votes?[- /]voids?"
    r"|undervotes?|overvotes?|times cast"
)
_CHOICES = ("yes", "no", "for", "against")
_VOWELLESS_NAMES = {"ng"}  # real two-letter names a feed may have title-cased correctly
_SURNAME = re.compile(r"[A-Za-z][A-Za-z'\-]+")
_SUFFIX = {"ii": "II", "iii": "III", "iv": "IV", "jr": "Jr.", "sr": "Sr."}


_MOJIBAKE = re.compile("[ÃÂ√‚]")


def _demojibake(name: str) -> str:
    """Undo UTF-8 read as cp1252/Mac Roman ("CastaÃ±eda", "Casta√±eda" -> "Castañeda")."""
    if not _MOJIBAKE.search(name):
        return name
    for encoding in ("cp1252", "mac_roman"):
        try:
            fixed = name.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if fixed != name:
            return fixed
    return name


def pretty_name(name: str) -> str:
    """Display form of a candidate name from a feed: ALL-CAPS feeds get title case, and
    title-casing mistakes are repaired ("Mcsally" -> "McSally", "Iii" -> "III", "Jr" -> "Jr.")."""
    # U+FFFD = a character the source already lost (OpenElections' 2014 Indiana file has
    # "Andr\ufffd Carson"); drop it rather than post a replacement box.
    n = " ".join(_demojibake(name).replace("\ufffd", "").split())
    # "Begich, Nick" -> "Nick Begich" (Alaska and others list surname first); keep
    # "Angus King, Jr." as is.
    if n.count(",") == 1:
        last, first = (x.strip() for x in n.split(","))
        if (
            _SURNAME.fullmatch(last)
            and 1 <= len(first.split()) <= 3
            and '"' not in first
            and first.strip(".").lower() not in _SUFFIX
            and last.lower() not in _CHOICES
            and first.split()[0].lower() not in _CHOICES
        ):
            if last.isupper() and not first.isupper():
                last = last.title()
            n = f"{first} {last}"
    caps = n.isupper() and len(n) > 1
    if caps:
        n = n.title()
    parts = []
    for i, word in enumerate(n.split(" ")):
        bare = word.strip(".,").lower()
        if bare in _SUFFIX and i > 0:
            word = _SUFFIX[bare] + ("," if word.endswith(",") else "")
        elif (
            i == 0
            and len(word) == 2
            and word.isalpha()
            and not set(bare) & set("aeiouy")
            and (caps or (word.istitle() and bare not in _VOWELLESS_NAMES))
            and " " in n
        ):
            # Vowel-less two-letter first names are initials: "JB PRITZKER" -> "JB Pritzker",
            # and feeds that title-cased them already ("Jd Vance") -> "JD Vance".
            word = word.upper()
        else:
            word = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), word)
        parts.append(word)
    return " ".join(parts)
