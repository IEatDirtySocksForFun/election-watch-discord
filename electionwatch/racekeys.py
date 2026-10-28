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
# US territories elect a non-voting House member (key XX-AL, e.g. PR-AL) plus their own
# governor and legislature. Kept apart from STATES because adapters derive their
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

    m = re.fullmatch(r"([A-Za-z]{2})-(\d{1,2}|AL|al)(?:\s+(S|SPECIAL))?", raw, re.I)
    if m:
        dist = m.group(2).upper()
        return RaceKey(
            _state(m.group(1)), Level.HOUSE, "HOUSE", "AL" if dist == "AL" else f"{int(dist):02d}", bool(m.group(3))
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
    m = re.fullmatch(r"(SD|HD)-?([A-Z]{1,4}(?:-(?:[A-Z]{1,4}|\d{1,3}[A-Z]?))*|\d{1,3}[A-Z]?)", office)
    if m:
        d = m.group(2)
        d = f"{int(d):02d}" if d.isdigit() else d
        return RaceKey(st, Level.STATE, m.group(1), d, _special(rest[1:]))
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
        m = re.fullmatch(r"(?:congressional|house)\s+district\s+(\d{1,2})", tail)
        if m:
            return RaceKey(st, Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
        if tail in ("senate", "senate race", "us senate", "u.s. senate"):
            return RaceKey(st, Level.SENATE, "SEN")
        if tail in ("senate special", "special senate"):
            return RaceKey(st, Level.SENATE, "SEN", None, True)
        for code, title in STATEWIDE.items():
            if tail in (title.lower(), title.lower() + " race"):
                return RaceKey(st, Level.GOVERNOR, code)
        return None
    return None


def format_key(k: RaceKey) -> str:
    sp = " S" if k.special else ""
    if k.level is Level.HOUSE:
        return f"{k.state}-{k.district}{sp}"
    if k.level is Level.STATE:
        return f"{k.state} {k.office}-{k.district}{sp}"
    if k.level is Level.LOCAL:
        return f"{k.state} LOCAL {k.district}"
    return f"{k.state} {k.office}{sp}"


def describe(k: RaceKey) -> str:
    """Human name: 'North Carolina Congressional District 1', 'Texas Senate'."""
    st = ALL_JURISDICTIONS[k.state]
    sp = " (Special)" if k.special else ""
    if k.level is Level.HOUSE and k.state in TERRITORIES:
        title = "Resident Commissioner" if k.state == "PR" else "Delegate to Congress"
        return f"{st} {title}{sp}"
    if k.level is Level.HOUSE:
        if k.district == "AL":
            return f"{st} At-Large Congressional District{sp}"
        return f"{st} Congressional District {int(k.district)}{sp}"
    if k.level is Level.SENATE:
        return f"{st} Senate{sp}"
    if k.level is Level.GOVERNOR:
        return f"{st} {STATEWIDE.get(k.office, k.office)}{sp}"
    if k.level is Level.STATE:
        ch = "State Senate" if k.office == "SD" else "State House"
        return f"{st} {ch} District {k.district.lstrip('0') or '0'}{sp}"
    return f"{st} local: {k.district.replace('-', ' ')}"
