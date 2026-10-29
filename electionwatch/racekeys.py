"""Race-key shorthand used in config/races.yaml and slash commands.

    NC-01  NC-1  AK-AL           US House district (AL = at-large)
    TX SEN  TX-SEN               US Senate;  "OH SEN S" / "OH SEN SPECIAL" = special election
    TX GOV  TX AG  TX LTGOV ...  statewide executive office (see STATEWIDE)
    TX SD-05  TX HD-121          state senate / state house district (numbered)
    AK SD-A   VT HD-CHT-6-7      ... or named/lettered (Alaska A-T, Vermont CHT-6-7)
    TX LOCAL harris-county-judge local race, free-form slug (sources map it)

Long forms are accepted too: "Texas Senate", "North Carolina Congressional District 1",
"Georgia Governor". "<State> Senate" means the US Senate race (TX SEN), as in the README.
"""

from __future__ import annotations

import re

from electionwatch.models import Level, RaceKey

STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
# US territories elect a non-voting House member (key XX-AL, e.g. PR-AL) plus a territorial
# governor and legislature. Kept apart from STATES because adapters derive adapter
# coverage from STATES and no territory has a results adapter yet.
TERRITORIES = {
    "PR": "Puerto Rico",
    "GU": "Guam",
    "VI": "U.S. Virgin Islands",
    "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}
ALL_JURISDICTIONS = {**STATES, **TERRITORIES}
_BY_NAME = {v.lower(): k for k, v in ALL_JURISDICTIONS.items()}

STATEWIDE = {
    "GOV": "Governor",
    "LTGOV": "Lieutenant Governor",
    "AG": "Attorney General",
    "SOS": "Secretary of State",
    "TREAS": "Treasurer",
    "AUD": "Auditor",
    "COMP": "Comptroller",
    "SUPT": "Superintendent of Public Instruction",
    "AGCOM": "Agriculture Commissioner",
    "INSCOM": "Insurance Commissioner",
    "LANDCOM": "Land Commissioner",
    "RRC": "Railroad Commissioner",
}


class RaceKeyError(ValueError):
    pass


def _state(tok: str) -> str:
    st = tok.upper()
    if st not in ALL_JURISDICTIONS:
        raise RaceKeyError(f"unknown state {tok!r}")
    return st


#: Abbreviations people type for a state besides its two-letter code (dots and case ignored).
_ABBREVIATIONS = {
    "ala": "AL", "alaba": "AL", "bama": "AL", "ariz": "AZ", "ark": "AR",
    "cal": "CA", "cali": "CA", "calif": "CA", "socal": "CA", "norcal": "CA",
    "colo": "CO", "conn": "CT", "del": "DE", "fla": "FL", "ga": "GA",
    "ill": "IL", "ind": "IN", "kan": "KS", "kans": "KS", "ky": "KY",
    "la": "LA", "md": "MD", "mass": "MA", "mich": "MI", "minn": "MN",
    "miss": "MS", "mo": "MO", "mont": "MT", "neb": "NE", "nebr": "NE",
    "nev": "NV", "okla": "OK", "ore": "OR", "oreg": "OR", "penn": "PA",
    "penna": "PA", "tenn": "TN", "tex": "TX", "ut": "UT", "vt": "VT",
    "va": "VA", "wash": "WA", "wis": "WI", "wisc": "WI", "wyo": "WY",
    "wva": "WV", "w va": "WV", "n carolina": "NC", "s carolina": "SC",
    "n dakota": "ND", "s dakota": "SD", "w virginia": "WV", "n mexico": "NM",
    "n hampshire": "NH", "n jersey": "NJ", "n york": "NY", "r island": "RI",
    "washington dc": "DC", "washington d c": "DC", "d c": "DC", "district of columbia": "DC",
    "virgin islands": "VI", "us virgin islands": "VI", "usvi": "VI", "samoa": "AS",
    "american samoa": "AS", "northern marianas": "MP", "mariana islands": "MP",
    "cnmi": "MP", "puerto rico": "PR", "pr": "PR", "guam": "GU",
}  # fmt: skip
FUZZY_CUTOFF = 0.8  # how close a misspelt name must be (difflib ratio) to count as that state


def _normalize(text: str) -> str:
    """'N.C.' -> 'nc', 'the State of  Texas' -> 'texas', 'New York State' -> 'new york'."""
    cleaned = " ".join(text.replace(".", "").replace("-", " ").casefold().split())
    for prefix in ("the state of ", "state of ", "commonwealth of "):
        cleaned = cleaned.removeprefix(prefix)
    return cleaned.removesuffix(" state")


def _state_match(text: str, allowed) -> tuple[str, float] | None:
    """(code, closeness 0-1) for a state typed any common way: code, name, abbreviation,
    or a misspelt name or abbreviation (closeness < 1). None if no state is clearly meant."""
    import difflib

    cleaned = _normalize(text)
    if not cleaned:
        return None
    names = {name.casefold().replace(".", ""): code for code, name in allowed.items()}
    exact = {**{code.casefold(): code for code in allowed}, **names}
    exact.update({abbr: code for abbr, code in _ABBREVIATIONS.items() if code in allowed})
    if cleaned in exact:
        return exact[cleaned], 1.0
    if cleaned.replace(" ", "") in exact:  # "n c", "w va"
        return exact[cleaned.replace(" ", "")], 1.0
    if len(cleaned) < 4:  # too short to guess at: "nx" could be several states
        return None
    spelled = {spelling: code for spelling, code in exact.items() if len(spelling) >= 4}
    scored = sorted(
        ((difflib.SequenceMatcher(None, cleaned, spelling).ratio(), code) for spelling, code in spelled.items()),
        reverse=True,
    )
    best, code = scored[0]
    runner_up = next((score for score, other in scored if other != code), 0.0)
    # Close enough, and clearly closer to one state than to any other.
    if best >= FUZZY_CUTOFF and best - runner_up >= 0.05:
        return code, best
    return None


def resolve_state(text: str, allowed=None) -> str:
    """The state someone typed: 'PA', 'pa', 'Pennsylvania', 'Penn.', 'N.C.', 'pensylvania'.
    RaceKeyError if no state (in `allowed`, default every jurisdiction) is clearly meant."""
    match = _state_match(text, ALL_JURISDICTIONS if allowed is None else allowed)
    if match is None:
        raise RaceKeyError(f"unknown state {text.strip()[:60]!r}")
    return match[0]


def _leading_state(raw: str) -> tuple[str, str] | None:
    """('PA', ' 08') from 'pensylvaniua 08', ('NY', '-12') from 'new york-12', ('PA', '08')
    from 'pa08': the state the text starts with, and the rest. Exact spellings win; otherwise
    the closest misspelling of the first one to four words."""
    words = []
    end = 0
    for word in re.finditer(r"[A-Za-z.']+", raw):
        if raw[end : word.start()].strip(" -"):
            break
        words.append(word)
        end = word.end()
        if len(words) == 4:
            break
    best = None
    for count in range(len(words), 0, -1):
        head_end = words[count - 1].end()
        match = _state_match(raw[words[0].start() : head_end], ALL_JURISDICTIONS)
        if match is not None and (best is None or match[1] > best[1]):
            best = (match[0], match[1], head_end)
            if match[1] == 1.0:
                break  # an exact spelling, longest first: "west virginia" before "west"
    if best is None:
        return None
    return best[0], raw[best[2] :]


def parse_typed(text: str) -> RaceKey:
    """parse() for what people type into commands, forgiving about the state: 'pa 08', 'PA08',
    'pa-8', 'Pennsylvania-08', 'pensylvaniua 08', 'Penn. sen', 'N.C. 1', 'new york gov' all
    work. Feeds and races.yaml use the strict parse()."""
    try:
        return parse(text)
    except RaceKeyError as exc:
        error = exc
    raw = " ".join(text.split())
    split = _leading_state(raw)
    if split is None:
        raise error
    state, rest = split[0], split[1].strip(" -")
    if not rest:
        raise RaceKeyError(f"missing office in {raw!r}")
    house = re.fullmatch(r"(\d{1,2}|al|at[\s-]?large)(?:\s+(s|special))?", rest, re.I)
    if house:
        district = "AL" if house.group(1)[0] in "aA" else house.group(1)
        return parse(f"{state}-{district}" + (" S" if house.group(2) else ""))
    for candidate in (f"{ALL_JURISDICTIONS[state]} {rest}", f"{state} {rest}"):
        try:
            return parse(candidate)  # long forms ("senate", "congressional district 8"), then short
        except RaceKeyError:
            continue
    raise error


def _special(rest: list[str]) -> bool:
    if not rest:
        return False
    if len(rest) == 1 and rest[0].upper() in ("S", "SPECIAL"):
        return True
    raise RaceKeyError(f"unexpected trailing {' '.join(rest)!r}")


def parse(text: str) -> RaceKey:
    raw = text.strip()
    if not raw:
        raise RaceKeyError("empty race key")
    long = _parse_long(raw)
    if long:
        return long

    match = re.fullmatch(r"([A-Za-z]{2})-(\d{1,2}|AL|al)(?:\s+(S|SPECIAL))?", raw, re.I)
    if match:
        dist = match.group(2).upper()
        return RaceKey(
            _state(match.group(1)),
            Level.HOUSE,
            "HOUSE",
            "AL" if dist == "AL" else f"{int(dist):02d}",
            bool(match.group(3)),
        )

    toks = re.split(r"[\s-]+", raw, maxsplit=1)
    st = _state(toks[0])
    rest = toks[1].split() if len(toks) > 1 else []
    if not rest:
        raise RaceKeyError(f"missing office in {raw!r}")
    office = rest[0].upper()

    if office == "SEN":
        return RaceKey(st, Level.SENATE, "SEN", None, _special(rest[1:]))
    if office in STATEWIDE:
        return RaceKey(st, Level.GOVERNOR, office, None, _special(rest[1:]))
    match = re.fullmatch(r"(SD|HD)-?([A-Z]{1,4}(?:-(?:[A-Z]{1,4}|\d{1,3}[A-Z]?))*|\d{1,3}[A-Z]?)", office)
    if match:
        district = match.group(2)
        district = f"{int(district):02d}" if district.isdigit() else district
        return RaceKey(st, Level.STATE, match.group(1), district, _special(rest[1:]))
    if office in ("SD", "HD") and len(rest) >= 2:
        return parse(f"{st} {office}-{rest[1]}" + (" " + " ".join(rest[2:]) if rest[2:] else ""))
    if office == "LOCAL":
        if len(rest) < 2:
            raise RaceKeyError("LOCAL needs a slug, e.g. 'TX LOCAL harris-county-judge'")
        slug = "-".join(rest[1:]).lower()
        return RaceKey(st, Level.LOCAL, "LOCAL", slug)
    raise RaceKeyError(f"unknown office {rest[0]!r} in {raw!r}")


def _parse_long(raw: str) -> RaceKey | None:
    low = raw.lower()
    for name, st in sorted(_BY_NAME.items(), key=lambda kv: -len(kv[0])):
        if not low.startswith(name + " "):
            continue
        tail = low[len(name) + 1 :].strip()
        match = re.fullmatch(r"(?:congressional|house)\s+district\s+(\d{1,2})", tail)
        if match:
            return RaceKey(st, Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}")
        if tail in ("senate", "senate race", "us senate", "u.s. senate"):
            return RaceKey(st, Level.SENATE, "SEN")
        if tail in ("senate special", "special senate"):
            return RaceKey(st, Level.SENATE, "SEN", None, True)
        for code, title in STATEWIDE.items():
            if tail in (title.lower(), title.lower() + " race"):
                return RaceKey(st, Level.GOVERNOR, code)
        return None
    return None


def format_key(key: RaceKey) -> str:
    suffix = " S" if key.special else ""
    if key.level is Level.HOUSE:
        return f"{key.state}-{key.district}{suffix}"
    if key.level is Level.STATE:
        return f"{key.state} {key.office}-{key.district}{suffix}"
    if key.level is Level.LOCAL:
        return f"{key.state} LOCAL {key.district}"
    return f"{key.state} {key.office}{suffix}"


def describe(key: RaceKey) -> str:
    """Human name: 'North Carolina Congressional District 1', 'Texas Senate'."""
    state_name = ALL_JURISDICTIONS[key.state]
    suffix = " (Special)" if key.special else ""
    if key.level is Level.HOUSE and key.state in TERRITORIES:
        title = "Resident Commissioner" if key.state == "PR" else "Delegate to Congress"
        return f"{state_name} {title}{suffix}"
    if key.level is Level.HOUSE:
        if key.district == "AL":
            return f"{state_name} At-Large Congressional District{suffix}"
        return f"{state_name} Congressional District {int(key.district)}{suffix}"
    if key.level is Level.SENATE:
        return f"{state_name} Senate{suffix}"
    if key.level is Level.GOVERNOR:
        return f"{state_name} {STATEWIDE.get(key.office, key.office)}{suffix}"
    if key.level is Level.STATE:
        chamber = "State Senate" if key.office == "SD" else "State House"
        return f"{state_name} {chamber} District {key.district.lstrip('0') or '0'}{suffix}"
    return f"{state_name} local: {key.district.replace('-', ' ')}"
