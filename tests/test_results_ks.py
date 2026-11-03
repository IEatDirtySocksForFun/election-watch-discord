from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results.ks_sos import FALLBACK_PAGES, KsSos, _earlier_election, nav_pages, parse_ks, race_key

FIX = Path(__file__).parent / "fixtures" / "ks_sos" / "KS"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_party_primary_halves_are_skipped():
    assert parse_ks(page("2026_primary_statewide.html"), fetched_at=AT) == []


def test_ballot_question_with_real_counts():
    (race_result,) = parse_ks(page("2026_primary_constitutional.html"), fetched_at=AT)
    assert format_key(race_result.key) == "KS LOCAL constitutional-amendment"
    assert race_result.title == "Constitutional Amendment"
    assert race_result.total_votes == 612155  # "Total Votes" row
    assert race_result.precincts_reporting == race_result.precincts_total == 4150
    assert race_result.expected_vote_pct == 100.0
    no, yes = race_result.candidates
    assert no.name == 'Constitutional Amendment - "NO"'
    assert (no.votes, no.pct) == (375355, 61.0)
    assert (yes.votes, yes.pct) == (236800, 39.0)
    assert no.winner is False  # the site declares no winner


def test_last_update_is_converted_to_utc():
    (race_result,) = parse_ks(page("2026_primary_constitutional.html"), fetched_at=AT)
    # <span id='time-stamp'>08/05/2026 4:25 AM</span> in America/Chicago
    assert race_result.source_updated_at == datetime(2026, 8, 5, 9, 25, tzinfo=timezone.utc)


def test_general_election_headings_parse():
    """The general-election pages carry the same blocks without the " (D)"/" (R)" suffix."""
    general = page("2026_primary_statewide.html").replace(" (D)</h3>", "</h3>").replace(" (R)</h3>", "</h3>")
    got = {format_key(race_result.key): race_result for race_result in parse_ks(general, fetched_at=AT)}
    gov = got["KS GOV"]
    assert gov.key.level is Level.GOVERNOR
    assert gov.total_votes == 220451
    assert [(candidate.name, candidate.votes) for candidate in gov.candidates] == [
        ("Cindy Holscher", 107077),
        ("Ethan Corson", 85567),
        ("Curt Skoog", 27807),
    ]
    assert got["KS SOS"].candidates[0].votes == 174813
    # identical headings (both party halves) fall back to the checkbox id, so nothing is lost
    assert "KS LOCAL governor-lt-governor-1012" in got


def test_race_key_mapping():
    assert format_key(race_key("United States Senate")) == "KS SEN"
    assert format_key(race_key("United States House of Representatives 3")) == "KS-03"
    assert format_key(race_key("Governor / Lt. Governor")) == "KS GOV"
    assert format_key(race_key("Attorney General")) == "KS AG"
    assert format_key(race_key("Kansas Senate 5")) == "KS SD-05"
    assert format_key(race_key("Kansas House of Representatives 12")) == "KS HD-12"
    assert format_key(race_key("Member, State Board of Education 3")) == "KS LOCAL member-state-board-of-education-3"
    assert format_key(race_key("District Court Judge 14-1")) == "KS LOCAL district-court-judge-14-1"


def test_navigation_skips_county_and_favorite_pages():
    assert nav_pages(page("2026_primary_statewide.html")) == [
        "constitutional.html",
        "national.html",
        "statewide.html",
        "ks-house.html",
        "ks-boe.html",
        "judicial.html",
    ]
    html = "<a href='allen.html#top'>Allen</a><a href='favorite-races.html'>Fav</a><a href='democratic.html'>D</a>"
    assert nav_pages(html) == list(FALLBACK_PAGES)


# --- the site still showing an earlier election ---------------------------------------------------


def ks_client():
    """The site as it was on 2026-11-02: the August primary pages (last update 08/05/2026)."""
    pages = {"statewide.html": "2026_primary_statewide.html", "constitutional.html": "2026_primary_constitutional.html"}

    def handler(request):
        name = pages.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=page(name)) if name else httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_primary_pages_are_not_general_election_results(monkeypatch):
    monkeypatch.delenv("ELECTION_DATE", raising=False)  # tracks 2026-11-03
    async with ks_client() as client:
        keys = [key for key, _ in await KsSos().list_races("KS", client)]
        assert keys == []
        with pytest.raises(SourceUnavailable, match="still shows an earlier election .last update 2026-08-05"):
            await KsSos().fetch([RaceKey("KS", Level.SENATE, "SEN")], client)


async def test_pages_of_the_tracked_election_are_read(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-08-04")  # the primary itself
    async with ks_client() as client:
        listed = await KsSos().list_races("KS", client)
        assert listed
        results = await KsSos().fetch([key for key, _ in listed], client)
        assert {format_key(result.key) for result in results} == {format_key(key) for key, _ in listed}


def test_stale_cutoff(monkeypatch):
    monkeypatch.delenv("ELECTION_DATE", raising=False)  # tracks 2026-11-03
    assert not _earlier_election("<p>no stamp</p>")
    fresh = "<span id = 'time-stamp' >10/20/2026 9:00 PM </span>"
    assert _earlier_election(fresh) is None  # 14 days before: could be this election's page
    assert _earlier_election(page("2026_primary_statewide.html")) is not None


# --- the 2026 general election (published 2026-11-03) ---------------------------------------


def general(name):
    return parse_ks(page(name), fetched_at=AT)


def general_results():
    results = general("2026_general_statewide.html") + general("2026_general_national.html")
    return {format_key(result.key): result for result in results}


def test_general_election_candidates_carry_the_party_prefix():
    gov = general_results()["KS GOV"]
    assert [(candidate.name, candidate.party) for candidate in gov.candidates] == [
        ("Cindy Holscher", "DEM"),
        ("Ty Masterson", "REP"),
    ]
    assert [(candidate.votes, candidate.pct) for candidate in gov.candidates] == [(0, 0.0), (0, 0.0)]
    assert gov.total_votes == 0
    assert (gov.precincts_reporting, gov.precincts_total) == (0, 4168)


def test_general_election_two_letter_and_lowercase_party_codes():
    assert [(candidate.name, candidate.party) for candidate in general_results()["KS SOS"].candidates] == [
        ("Jennifer Day", "DEM"),
        ("Pat Proctor", "REP"),
        ("Scott E. Morgan", "UK"),
    ]
    assert [(candidate.name, candidate.party) for candidate in general_results()["KS SEN"].candidates] == [
        ("Adam Hamilton", "DEM"),
        ("Roger Marshall", "REP"),
        ("David C Graham", "LIB"),
    ]


def test_general_election_house_district_comes_from_the_heading():
    house = general_results()["KS-01"]
    assert house.title == "United States House of Representatives 1"
    assert [(candidate.name, candidate.party) for candidate in house.candidates] == [
        ("Lauren Reinhold", "DEM"),
        ("Tracey Mann", "REP"),
        ("Steven Jacob", "LIB"),
    ]
    assert house.precincts_total == 1403


def test_general_election_stamp_is_election_day():
    # <span id='time-stamp'>11/03/2026 7:59 AM</span> in America/Chicago
    assert general_results()["KS GOV"].source_updated_at == datetime(2026, 11, 3, 13, 59, tzinfo=timezone.utc)


def test_general_navigation_adds_the_state_senate_tab():
    pages = nav_pages(page("2026_general_statewide.html"))
    assert "ks-senate.html" in pages
    assert pages[0] == "national.html"  # the general page is the current election, no stale skip


async def test_general_pages_are_fetched_and_parsed(monkeypatch):
    monkeypatch.delenv("ELECTION_DATE", raising=False)  # tracks 2026-11-03
    pages = {
        "statewide.html": "2026_general_statewide.html",
        "national.html": "2026_general_national.html",
    }

    def handler(request):
        name = pages.get(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=page(name)) if name else httpx.Response(404)

    keys = [RaceKey("KS", Level.GOVERNOR, "GOV"), RaceKey("KS", Level.SENATE, "SEN")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await KsSos().fetch(keys, client)

    got = {format_key(result.key): result for result in results}
    assert set(got) == {"KS GOV", "KS SEN"}
    assert got["KS GOV"].candidates[0].party == "DEM"
    assert got["KS SEN"].candidates[1].name == "Roger Marshall"
