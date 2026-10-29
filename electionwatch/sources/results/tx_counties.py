"""Texas — county election-night feeds (Clarity ENR), used when results.texas-election.com
is behind its Cloudflare challenge.

The Secretary of State's statewide site (`tx_sos`) is the primary source; this adapter is the
official alternate. The largest counties publish county Clarity ENR feeds, which carry the
full county ballot: US Senate, the congressional district(s) the county sits in, the statewide
executives, the legislature, the appeals courts and the local races.

    https://results.enr.clarityelections.com/TX/<County>/elections.json
    https://results.enr.clarityelections.com/TX/<County>/<EID>/current_ver.txt
    https://results.enr.clarityelections.com/TX/<County>/<EID>/<ver>/json/en/summary.json

Counties publish different contest names for the same office ("United States Representative,
District 4" in Denton, "US Representative, Dist 16" in El Paso, "U. S. Representative
District 25" in Hood, "Comm General Land Office" in Kaufman), so the mapping lives here
rather than in the shared vendor module. Races that every county publishes (Senate, governor,
the statewide courts) are merged into one result flagged " (partial: N counties)"; local races
are namespaced with the county slug so two counties' "Sheriff" do not collide.

Twenty Clarity counties are configured, plus Harris County, which publishes its own JSON
(`_harris`, merged into the same results). Tarrant, Fort Bend and Bexar list nothing in
`elections.json`; those ids come from the links on the counties' own results pages and are
pinned in ELECTIONS. Dallas and Williamson list the 2026 primaries/runoffs and are picked up
from `elections.json` once the general is listed. Collin, Montgomery, Brazoria, Hays and
Hidalgo (Clarity only until 2020) have no current directory on the CDN; see
docs/state-coverage-plan.md. Pin any Clarity county with TX_COUNTY_FEEDS:

    TX_COUNTY_FEEDS='[{"county":"Dallas","election_id":"123456","date":"2026-11-03"}]'

TX_HARRIS_CODE overrides the Harris election code (default: month + 2-digit year, "1126");
TX_HARRIS_CODE=off drops Harris.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import httpx

from electionwatch.models import Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c
from . import _county, _harris
from .clarity import parse_settings

HUMAN = "https://results.texas-election.com/races"

#: County name as it appears in the Clarity URL -> slug used for local race keys.
#: The big counties that publish an election-night feed on the vendor CDN; the four largest
#: (Harris, Bexar, Collin, Montgomery) run other systems and are not listed.
COUNTIES: dict[str, str] = {
    "Dallas": "dallas",
    "Denton": "denton",
    "El_Paso": "el-paso",
    "Fort_Bend": "fort-bend",
    "Tarrant": "tarrant",
    "Travis": "travis",
    # Added 2026-10-27 from the same CDN, verified with each county's 2024 (and where published,
    # 2026) general-election feeds.
    "Bastrop": "bastrop",
    "Bell": "bell",
    "Comal": "comal",
    "Galveston": "galveston",
    "Gregg": "gregg",
    "Hood": "hood",
    "Kaufman": "kaufman",
    "Lamar": "lamar",
    "Midland": "midland",
    "Rockwall": "rockwall",
    "Rusk": "rusk",
    # Added 2026-10-27 (docs/state-coverage-plan.md). Bexar moved to Clarity for 2026 (its
    # elections page links TX/Bexar/127291); Williamson lists its 2026 runoff on the CDN.
    "Bexar": "bexar",
    "Williamson": "williamson",
}

#: date -> county -> election id. Anything missing is discovered from the county's
#: elections.json; these are the ids verified while writing the fixtures.
ELECTIONS: dict[str, dict[str, str]] = {
    "2024-11-05": {
        "Bastrop": "122753",
        "Bell": "122428",
        "Comal": "122856",
        "Denton": "122426",
        "El_Paso": "122595",
        "Galveston": "122842",
        "Gregg": "122313",
        "Hood": "122858",
        "Kaufman": "122824",
        "Lamar": "122497",
        "Midland": "122496",
        "Rockwall": "122491",
        "Rusk": "122760",
        "Travis": "122432",
        # From the counties' own 2024 results pages (elections.json lists nothing for these):
        # tarrantcountytx.gov ".../november-5--2024--general-election-results.html" and
        # fortbendcountytx.gov ".../2024-election-results".
        "Tarrant": "122489",
        "Fort_Bend": "122596",
    },
    "2026-11-03": {
        "Bastrop": "127476",
        "Denton": "127398",
        "El_Paso": "127348",
        "Galveston": "127478",
        "Hood": "127400",
        "Kaufman": "127485",
        "Lamar": "127484",
        "Rusk": "127574",
        # Linked from tarrantcountytx.gov/en/elections/current-election-information.html,
        # fortbendcountytx.gov ".../2026-election-results" and elections.bexar.gov;
        # all three answered with a zero-vote 2026 general summary on 2026-10-27.
        "Tarrant": "127297",
        "Fort_Bend": "127480",
        "Bexar": "127291",
    },
}

_STATEWIDE = {
    "GOVERNOR": "GOV",
    "LIEUTENANT GOVERNOR": "LTGOV",
    "ATTORNEY GENERAL": "AG",
    "COMPTROLLER OF PUBLIC ACCOUNTS": "COMP",
    "COMMISSIONER OF THE GENERAL LAND OFFICE": "LANDCOM",
    "COMMISSIONER GENERAL LAND OFFICE": "LANDCOM",  # "Comm General Land Office"
    "COMMISSIONER OF AGRICULTURE": "AGCOM",
    "RAILROAD COMMISSIONER": "RRC",
}

_SENATE = {"UNITED STATES SENATOR", "US SENATOR", "U.S. SENATOR", "SENATOR"}

#: County feeds abbreviate unevenly ("Chief Justice, 8th Crt of App Dist" in El Paso vs
#: "... 8th Court of Appeals District" in Denton). Expand before matching so the same
#: statewide contest merges.
_ABBREV = {
    "CRT": "COURT",
    "CT": "COURT",
    "CNTY": "COUNTY",
    "CRTY": "COUNTY",
    "APP": "APPEALS",
    "CRIM": "CRIMINAL",
    "DIST": "DISTRICT",
    "PL": "PLACE",
    "ST": "STATE",
    "MUN": "MUNICIPAL",
    "BOE": "BOARD OF EDUCATION",
    "COMM": "COMMISSIONER",
    "UNEXP": "UNEXPIRED TERM",
    "SUPERME": "SUPREME",  # Hood County's own typo, 2024
}

_HOUSE = re.compile(r"^(?:UNITED STATES|US|U\.S\.) REPRESENTATIVE,?\s*(?:DISTRICT|DIST\.?)\s*(\d+)$")
_STATE_SENATE = re.compile(r"^STATE SENATOR,?\s*(?:DISTRICT|DIST\.?)\s*(\d+)")
_STATE_HOUSE = re.compile(r"^STATE REPRESENTATIVE,?\s*(?:DISTRICT|DIST\.?)\s*(\d+)")

#: Contests on every county's ballot: merged statewide, so no county prefix.
_SHARED_LOCAL = (
    (re.compile(r"^CHIEF JUSTICE, SUPREME COURT$"), lambda m: "supreme-court-chief-justice"),
    (
        re.compile(r"^JUSTICE, SUPREME COURT, PLACE (\d+)(?:,? UNEXPIRED TERM)?$"),
        lambda m: f"supreme-court-place-{_id(m.group(1))}",
    ),
    (
        re.compile(r"^PRESIDING JUDGE, COURT OF CRIMINAL APPEALS(?:, PLACE \d+)?$"),
        lambda m: "presiding-judge-court-criminal-appeals",
    ),
    (
        re.compile(r"^JUDGE, COURT OF CRIMINAL APPEALS, PLACE (\d+)(?:,? UNEXPIRED TERM)?$"),
        lambda m: f"court-criminal-appeals-place-{_id(m.group(1))}",
    ),
    (
        re.compile(r"^CHIEF JUSTICE, (\d+)(?:ST|ND|RD|TH) COURT OF APPEALS(?: DISTRICT)?$"),
        lambda m: f"court-of-appeals-{_id(m.group(1))}-chief-justice",
    ),
    (
        re.compile(
            r"^JUSTICE, (\d+)(?:ST|ND|RD|TH) COURT OF APPEALS DISTRICT"
            r"(?:, PLACE (\d+))?$"
        ),
        lambda m: f"court-of-appeals-{_id(m.group(1))}-place-{_id(m.group(2) or '1')}",
    ),
    (
        re.compile(r"^MEMBER, STATE BOARD OF EDUCATION, DISTRICT (\d+)"),
        lambda m: f"state-board-of-education-{_id(m.group(1))}",
    ),
    (re.compile(r"^PRESIDENT\s*/\s*VICE[- ]PRESIDENT$"), lambda m: "president-vice-president"),
    (re.compile(r"^PRESIDENT (?:AND|&) VICE[- ]PRESIDENT$"), lambda m: "president-vice-president"),
    (re.compile(r"^(?:STATE )?PROPOSITION (\d+)$"), lambda m: f"proposition-{_id(m.group(1))}"),
    (re.compile(r"^CONSTITUTIONAL AMENDMENT (\d+)$"), lambda m: f"constitutional-amendment-{_id(m.group(1))}"),
)


def _norm(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").replace("\u00a0", " ").strip()).upper()
    cleaned = re.sub(r"\bU\.\s*S\.?(?=\s)", "U.S.", cleaned)  # "U. S. Representative" -> "U.S. ..."
    # Fort Bend repeats the district: "United States Representative, District 7 District 7".
    cleaned = re.sub(r"\b(DISTRICT (\d+)) DISTRICT \2$", r"\1", cleaned)
    # Tarrant writes "Justice, Supreme Court, Place 2, (Unexpired Term)".
    cleaned = re.sub(r",?\s*\(UNEXPIRED TERM\)$", ", UNEXPIRED TERM", cleaned)
    return re.sub(r"\s+-\s+", " ", cleaned)  # "Place 2 - Unexpired Term"


def _id(part: str) -> str:
    return part.lstrip("0") or "0"


def _expand(name: str) -> str:
    return re.sub(r"[A-Z]+", lambda match: _ABBREV.get(match.group(0), match.group(0)), name)


def shared_key(name: str) -> RaceKey | None:
    """Key for a contest every county publishes, or None for a purely local one."""
    office_name = _expand(_norm(name))
    if office_name in _SENATE:
        return RaceKey("TX", Level.SENATE, "SEN")
    match = _HOUSE.match(office_name)
    if match:
        return RaceKey("TX", Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}")
    if office_name in _STATEWIDE:
        return RaceKey("TX", Level.GOVERNOR, _STATEWIDE[office_name])
    match = _STATE_SENATE.match(office_name)
    if match:
        return RaceKey("TX", Level.STATE, "SD", f"{int(match.group(1)):02d}")
    match = _STATE_HOUSE.match(office_name)
    if match:
        return RaceKey("TX", Level.STATE, "HD", f"{int(match.group(1)):02d}")
    for pattern, build in _SHARED_LOCAL:
        match = pattern.match(office_name)
        if match:
            return c.local_key("TX", build(match))
    return None


def race_key(county_slug: str, name: str) -> RaceKey:
    """Every contest gets a key; unrecognised ones become TX LOCAL <county>-<race>."""
    shared = shared_key(name)
    if shared is not None:
        return shared
    return c.local_key("TX", f"{county_slug}-{_norm(name)}")


def parse_tx_county(
    summary,
    settings=None,
    *,
    county: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    """Pure parse of one Texas county summary.json."""
    slug = COUNTIES.get(county, county.lower())
    updated = parse_settings(settings)
    return _county.parse_contests(
        summary,
        key_fn=lambda name: race_key(slug, name),
        source=TxCounties.name,
        url=HUMAN,
        fetched_at=fetched_at,
        updated=updated,
    )


def counties(date: str) -> list[dict]:
    """Configured counties for an election date, with optional pinned election ids."""
    override = _county.county_feeds_from_env("TX_COUNTY_FEEDS")
    pins = ELECTIONS.get(date, {})
    configured = {str(county_name).lower(): county_entry for county_name, county_entry in COUNTIES.items()}
    if override is None:
        return [{"county": name, "slug": slug, "election_id": pins.get(name)} for name, slug in COUNTIES.items()]
    feeds = []
    for entry in override:
        county = str(entry["county"])
        if entry.get("date") and entry["date"] != date:
            continue
        feeds.append(
            {
                "county": county,
                "slug": configured.get(county.lower(), county.lower()),
                "election_id": entry["election_id"],
            }
        )
    return feeds


def harris_entries(date: str) -> list[dict]:
    """Harris County's own JSON feed (TX_HARRIS_CODE overrides the code, "off" drops it)."""
    raw = os.environ.get("TX_HARRIS_CODE", "").strip()
    if raw.lower() in ("off", "none", "0", "false"):
        return []
    code = raw or _harris.election_code(date)
    return [{"county": "Harris", "slug": "harris", "vendor": "harris", "code": code}]


def parse_harris(payload, *, fetched_at: datetime, date: str | None = None) -> list[RaceResult]:
    """Pure parse of Harris County's resultsbyballot.json into tx_counties results.

    With `date`, a file for another election (a code collision, e.g. a special election in
    the same month) yields nothing instead of the wrong numbers."""
    if date and _harris.payload_date(payload) not in (None, date):
        return []
    return _harris.parse_contests(
        payload,
        key_fn=lambda name: race_key("harris", name),
        source=TxCounties.name,
        fetched_at=fetched_at,
        url=HUMAN,
    )


def all_entries(date: str) -> list[dict]:
    """Every configured county feed: the Clarity counties, then Harris."""
    return counties(date) + harris_entries(date)


async def _one(
    client: httpx.AsyncClient,
    entry: dict,
    date: str,
    fetched_at: datetime,
) -> list[RaceResult]:
    county = entry["county"]
    if entry.get("vendor") == "harris":
        payload = await c.get_json(client, _harris.results_url(entry["code"]))
        return parse_harris(payload, fetched_at=fetched_at, date=date)
    eid = await _county.election_id(client, "TX", county, date, entry.get("election_id"))
    if not eid:
        return []
    summary, settings, _ = await _county.fetch_summary(client, "TX", county, eid)
    return parse_tx_county(summary, settings, county=county, fetched_at=fetched_at)


@register
class TxCounties(ResultsSource):
    name = "tx_counties"
    states = frozenset({"TX"})
    scrape = False
    enabled_by_default = True

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        if not any(key.state == "TX" for key in keys):
            return []
        date = c.election_date()
        fetched_at = c.utcnow()
        collected: list[RaceResult] = []
        failures = 0
        entries = all_entries(date)
        for entry in entries:
            try:
                collected.extend(await _one(client, entry, date, fetched_at))
            except SourceUnavailable:
                failures += 1  # one dead county must not hide the others
        if failures and failures == len(entries):
            raise SourceUnavailable("tx_counties: no Texas county feed answered")
        return c.select(_county.merge(collected, source=self.name, url=HUMAN), keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "TX":
            return []
        date = c.election_date()
        fetched_at = c.utcnow()
        found: list[tuple[RaceKey, str]] = []
        seen: set[str] = set()
        for entry in all_entries(date):
            try:
                results = await _one(client, entry, date, fetched_at)
            except SourceUnavailable:
                continue
            for race_result in results:
                if str(race_result.key) not in seen:
                    seen.add(str(race_result.key))
                    found.append((race_result.key, race_result.title or str(race_result.key)))
        return found
