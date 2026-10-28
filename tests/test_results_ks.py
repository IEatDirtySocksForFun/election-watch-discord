from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ks_sos import FALLBACK_PAGES, nav_pages, parse_ks, race_key

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
