"""District of Columbia — Board of Elections results XML export.

DCBOE publishes a certified results XML export for each election. The XML link
is discovered from the election info endpoint:

    https://electionresults.dcboe.org/electionResults/getElectionResultsId
    https://electionresults.dcboe.org/electionResults/getElectionInfo/<UrlText>
    -> XmlLink

The XML is citywide-aggregated: each ``ContestDataExportViewModel`` contains
one ``ElectionDataExportViewModel`` per candidate/choice. Turnout comes from
the ``REGISTERED VOTERS - TOTAL`` and ``BALLOTS CAST - TOTAL`` pseudo-contests.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.sources.base import ResultsSource, SourceUnavailable, register

from . import _common as c

BASE = "https://electionresults.dcboe.org"
HUMAN = BASE + "/election_results/"

#: date -> UrlText for getElectionInfo. None = discover.
ELECTIONS: dict[str, str | None] = {
    "2024-11-05": "2024-General-Election",
    "2026-11-03": None,
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).upper()


_WORD_TO_DIGIT = {
    "ONE": "1",
    "TWO": "2",
    "THREE": "3",
    "FOUR": "4",
    "FIVE": "5",
    "SIX": "6",
    "SEVEN": "7",
    "EIGHT": "8",
    "NINE": "9",
    "TEN": "10",
}


def _ward_digit(tok: str) -> str:
    t = tok.strip().upper()
    if t in _WORD_TO_DIGIT:
        return _WORD_TO_DIGIT[t]
    return str(int(t))


def race_key(name: str) -> RaceKey:
    n = _norm(name)
    special = c.is_special(n)
    if n in (
        "ELECTORS OF PRESIDENT AND VICE PRESIDENT OF THE UNITED STATES",
        "PRESIDENT AND VICE PRESIDENT",
        "US PRESIDENT",
    ):
        return c.local_key("DC", "us-president")
    if n == "UNITED STATES SENATOR":
        # DC's "U.S. Senator" contest is the shadow senator; map to LOCAL.
        return c.local_key("DC", "shadow-senator")
    if n == "UNITED STATES REPRESENTATIVE":
        return c.local_key("DC", "shadow-representative")
    if n == "DELEGATE TO THE HOUSE OF REPRESENTATIVES FROM THE DISTRICT OF COLUMBIA":
        return RaceKey("DC", Level.HOUSE, "HOUSE", "AL", special)
    if n in ("MAYOR OF THE DISTRICT OF COLUMBIA", "MAYOR"):
        return RaceKey("DC", Level.GOVERNOR, "GOV", None, special)
    if n in ("ATTORNEY GENERAL FOR THE DISTRICT OF COLUMBIA", "ATTORNEY GENERAL"):
        return RaceKey("DC", Level.GOVERNOR, "AG", None, special)
    if n in ("CHAIRMAN OF THE COUNCIL", "CHAIRMAN OF THE COUNCIL OF THE DISTRICT OF COLUMBIA"):
        return RaceKey("DC", Level.GOVERNOR, "COUNCILCHAIR", None, special)
    if n == "AT-LARGE MEMBER OF THE COUNCIL OF THE DISTRICT OF COLUMBIA":
        return c.local_key("DC", "council-at-large")
    m = re.search(r"^WARD\s+(\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\s+MEMBER\s+OF\s+THE\s+COUNCIL", n)
    if m:
        return c.local_key("DC", f"council-ward-{_ward_digit(m.group(1))}")
    if n == "AT-LARGE MEMBER OF THE STATE BOARD OF EDUCATION":
        return c.local_key("DC", "sboe-at-large")
    m = re.search(
        r"^WARD\s+(\d+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\s+MEMBER\s+OF\s+THE\s+STATE\s+BOARD\s+OF\s+EDUCATION",
        n,
    )
    if m:
        return c.local_key("DC", f"sboe-ward-{_ward_digit(m.group(1))}")
    m = re.match(r"^(INITIATIVE\s+\d+|REFERRAL\s+\d+|CHARTER\s+AMENDMENT\s+\w+)", n)
    if m:
        return c.local_key("DC", c.slug(m.group(1)))
    m = re.match(r"^ANC-([0-9A-Z]+)", n)
    if m:
        return c.local_key("DC", f"anc-{m.group(1).lower()}")
    return c.local_key("DC", name)


def _clean_candidate(raw: str) -> tuple[str, str | None]:
    """'DEM Kamala D. Harris - Tim Walz' -> ('Kamala D. Harris - Tim Walz', 'DEM')."""
    text = (raw or "").strip()
    m = re.match(r"^(DEM|REP|IND|LIB|GRN|STG|NPN)\s+(.+)$", text, re.I)
    if m:
        return m.group(2).strip(), m.group(1).upper()
    return text, None


def _skip_choice(candidate: str | None, party: str | None) -> bool:
    n = (candidate or "").upper()
    p = (party or "").upper()
    return n in ("OVER VOTES", "UNDER VOTES") or n.startswith("WRITE-IN") or p == "CITYWIDE"


def parse_dc(root: ET.Element, *, fetched_at: datetime) -> list[RaceResult]:
    """Pure parse of the DCBOE XML export."""
    registered = 0
    ballots = 0
    out: list[RaceResult] = []
    seen: set[str] = set()

    for contest in root.findall(".//ContestDataExportViewModel"):
        title = contest.findtext("ContestName", "").strip()
        n = _norm(title)
        if n == "REGISTERED VOTERS - TOTAL":
            registered = c.to_int(contest.findtext(".//Votes"))
            continue
        if n == "BALLOTS CAST - TOTAL":
            ballots = c.to_int(contest.findtext(".//Votes"))
            continue

        key = race_key(title)
        if str(key) in seen:
            continue
        cands = []
        for choice in contest.findall(".//ElectionDataExportViewModel"):
            raw_name = choice.findtext("Candidate") or ""
            party = choice.findtext("Party") or ""
            if _skip_choice(raw_name, party):
                continue
            name, pref_party = _clean_candidate(raw_name)
            votes = c.to_int(choice.findtext("Votes"))
            cands.append(
                CandidateResult(
                    name=name,
                    party=c.norm_party(pref_party or party),
                    votes=votes,
                )
            )
        if not cands:
            continue
        seen.add(str(key))
        out.append(
            c.make_result(
                key,
                title,
                cands,
                fetched_at,
                source=DcBoe.name,
                url=HUMAN,
                total_votes=sum(x.votes for x in cands),
                registered_voters=registered or None,
                turnout_pct=c.to_pct(ballots / registered * 100) if registered else None,
            )
        )

    # Apply turnout to every race now that we have the totals.
    if registered and ballots:
        for r in out:
            r.registered_voters = registered
            r.turnout_pct = round(100.0 * ballots / registered, 2)
    return out


async def _election_url(client: httpx.AsyncClient, date: str) -> str:
    url_text = ELECTIONS.get(date)
    if url_text:
        return f"{BASE}/electionResults/getElectionInfo/{url_text}"
    try:
        text = await c.get_text(client, f"{BASE}/electionResults/getElectionResultsId")
        elections = json.loads(text)
    except (ValueError, SourceUnavailable) as exc:
        raise SourceUnavailable(f"DC: could not list elections ({exc})") from exc
    for entry in elections:
        if entry.get("ElectionDate", "").startswith(date) and entry.get("ElectionName") == "General Election":
            return f"{BASE}/electionResults/getElectionInfo/{entry['UrlText']}"
    raise SourceUnavailable(f"DC: no general election found for {date}")


async def _xml_url(client: httpx.AsyncClient, date: str) -> str:
    info_url = await _election_url(client, date)
    info = json.loads(await c.get_text(client, info_url))
    xml = info.get("XmlLink")
    if not xml:
        raise SourceUnavailable(f"DC: no XML link in election info ({info_url})")
    return xml


@register
class DcBoe(ResultsSource):
    name = "dc_boe"
    states = frozenset({"DC"})
    scrape = False

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        dc_keys = [k for k in keys if k.state == "DC"]
        if not dc_keys:
            return []
        xml = await _xml_url(client, c.election_date())
        text = await c.get_text(client, xml)
        parsed = parse_dc(ET.fromstring(text.encode("utf-8")), fetched_at=c.utcnow())
        return c.select(parsed, dc_keys)

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        if state != "DC":
            return []
        xml = await _xml_url(client, c.election_date())
        text = await c.get_text(client, xml)
        return [
            (r.key, r.title or str(r.key)) for r in parse_dc(ET.fromstring(text.encode("utf-8")), fetched_at=c.utcnow())
        ]
