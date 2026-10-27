import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results import ne_sos
from electionwatch.sources.results.ne_sos import parse_ne_page, race_key

FIX = Path(__file__).parent / "fixtures" / "ne_sos" / "ne"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def by_key(results):
    return {format_key(r.key): r for r in results}


def parsed(*names):
    results = []
    for name in names or ("sw.html", "cg.html", "ld.html", "se.html"):
        results.extend(parse_ne_page(load(name), fetched_at=AT))
    return results


def test_statewide_offices_from_the_sw_page():
    got = by_key(parsed("sw.html"))
    assert got["NE SEN"].title == "For United States Senator - 6 Year Term"
    assert got["NE SEN"].candidates[0].name == "Pete Ricketts"
    assert got["NE SEN"].candidates[0].party == "REP"
    assert got["NE GOV"].candidates[0].name == "Jim Pillen and Joe Kelly"   # ticket
    assert got["NE AUD"].key.office == "AUD"
    assert got["NE SEN"].source == "ne_sos"


def test_precincts_come_from_the_precincts_fully_line():
    sen = by_key(parsed("sw.html"))["NE SEN"]
    assert sen.precincts_total == 1322
    assert sen.precincts_reporting is None                    # 0 fully reported -> None
    assert sen.expected_vote_pct is None
    assert sen.total_votes == 0


def test_us_house_and_legislature_keys():
    got = by_key(parsed("cg.html", "ld.html"))
    assert set(k for k in got if k.startswith("NE-")) == {"NE-01", "NE-02", "NE-03"}
    assert got["NE-02"].key.level is Level.HOUSE
    assert got["NE SD-02"].key.level is Level.STATE
    assert got["NE SD-02"].candidates[0].party == "NONPARTISAN"


def test_state_board_of_education_is_local():
    got = by_key(parsed("se.html"))
    assert list(got) == ["NE LOCAL member-of-the-state-board-of-education-district-05"]
    assert got["NE LOCAL member-of-the-state-board-of-education-district-05"].title.startswith(
        "For Member of the State Board of Education")


def test_race_key_mapping():
    assert format_key(race_key("For United States Senator - 6  Year Term")) == "NE SEN"
    assert format_key(race_key("For Governor and Lt. Governor")) == "NE GOV"
    assert format_key(race_key("For Secretary of State")) == "NE SOS"
    assert format_key(race_key("For State Treasurer")) == "NE TREAS"
    assert format_key(race_key("For Attorney General")) == "NE AG"
    assert format_key(race_key("For Auditor of Public Accounts")) == "NE AUD"
    assert format_key(race_key("For Representative in Congress - 2  Year Term - District 02")) == \
        "NE-02"
    assert format_key(race_key("For Member of the Legislature - District 49")) == "NE SD-49"
    assert format_key(race_key("For Member of the State Board of Education - District 05")) == \
        "NE LOCAL member-of-the-state-board-of-education-district-05"


def test_scraper_flags():
    assert ne_sos.NeSos.scrape is True
    assert ne_sos.NeSos.states == frozenset({"NE"})
    # AGENTS.md: scrapers of sites without an API are opt-in.
    assert ne_sos.NeSos.enabled_by_default is False


def test_empty_html_is_not_an_error():
    assert parse_ne_page("", fetched_at=AT) == []
    assert parse_ne_page("<html><body></body></html>", fetched_at=AT) == []


def test_fetch_only_scrapes_pages_it_needs(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        family = request.url.params["type"]
        seen.append(family)
        return httpx.Response(200, text=load(f"{family.lower()}.html"))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ne_sos.NeSos().fetch([parse("NE SEN"), parse("NE GOV")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"NE SEN", "NE GOV"}
    assert seen == ["SW"]                     # no local/legislative page was fetched


def test_fetch_pulls_local_pages_when_a_local_race_is_watched(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        family = request.url.params["type"]
        seen.append(family)
        return httpx.Response(200, text=load(f"{family.lower()}.html") if family == "SE"
                              else "<html><body></body></html>")

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ne_sos.NeSos().fetch(
                [parse("NE LOCAL member-of-the-state-board-of-education-district-05")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"NE LOCAL member-of-the-state-board-of-education-district-05"}
    assert set(seen) >= set(ne_sos.LOCAL_PAGES)
