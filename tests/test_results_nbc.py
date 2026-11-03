"""nbc_results parses the NBC firecracker feed used by the nbc call adapter."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.nbc_results import NBCResultsSource, parse_nbc_results

FIX = Path(__file__).parent / "fixtures" / "nbc_results"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
TX_38 = RaceKey("TX", Level.HOUSE, "HOUSE", "38")


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_result():
    results = {
        race_result.key: race_result for race_result in parse_nbc_results(_load("2024-senate.json"), fetched_at=NOW)
    }
    sen = results[TX_SEN]
    assert sen.source == "nbc_results"
    assert sen.total_votes == 11291854
    assert sen.expected_vote_pct == 100
    assert sen.title == "U.S. Senate"
    assert sen.url == "https://www.nbcnews.com/politics/2024-elections/texas-senate-results"
    cruz = sen.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53.1)
    assert cruz.incumbent is True
    assert cruz.winner is False


def test_parse_house_result():
    results = {
        race_result.key: race_result for race_result in parse_nbc_results(_load("2024-house.json"), fetched_at=NOW)
    }
    house = results[TX_38]
    assert house.title == "U.S. House District 38"
    assert house.candidates[0].votes == 215030
    assert house.total_votes == 342764


def test_parse_2026_general_senate_feed():
    """Live 2026 general feed (fetched before counting): 0 votes, ISO 8601 lastModified, per-race href."""
    results = {
        race_result.key: race_result for race_result in parse_nbc_results(_load("2026-senate.json"), fetched_at=NOW)
    }
    assert set(results) == {RaceKey("AK", Level.SENATE, "SEN"), RaceKey("CO", Level.SENATE, "SEN")}
    ak = results[RaceKey("AK", Level.SENATE, "SEN")]
    assert ak.url == "https://www.nbcnews.com/politics/2026-elections/alaska-senate-results"
    assert ak.title == "U.S. Senate"
    assert (ak.total_votes, ak.expected_vote_pct) == (0, 0)
    assert ak.source_updated_at == datetime(2026, 11, 3, 12, 44, 39, 873000, tzinfo=timezone.utc)
    assert [(c.name, c.party, c.votes, c.pct) for c in ak.candidates] == [
        ("Gerald Heikes", "REP", 0, 0),
        ("Mary Peltola", "DEM", 0, 0),
        ("Dan S. Sullivan", "REP", 0, 0),
        ("Daniel J. Sullivan Jr.", "UDC", 0, 0),
        ("Total Write-ins", "OTH", 0, 0),
    ]
    assert all(candidate.winner is False for candidate in ak.candidates)


def test_parse_2026_general_governor_and_house_feeds():
    governor = {
        race_result.key: race_result for race_result in parse_nbc_results(_load("2026-governor.json"), fetched_at=NOW)
    }
    al_gov = governor[RaceKey("AL", Level.GOVERNOR, "GOV")]
    assert al_gov.url == "https://www.nbcnews.com/politics/2026-elections/alabama-governor-results"
    assert al_gov.title == "Governor"
    assert [candidate.name for candidate in al_gov.candidates] == ["Doug Jones", "Tommy Tuberville"]

    house = {
        race_result.key: race_result for race_result in parse_nbc_results(_load("2026-house.json"), fetched_at=NOW)
    }
    al_01 = house[RaceKey("AL", Level.HOUSE, "HOUSE", "01")]
    assert al_01.url == "https://www.nbcnews.com/politics/2026-elections/alabama-us-house-district-1-results"
    assert al_01.title == "U.S. House District 1"
    assert (al_01.total_votes, al_01.expected_vote_pct) == (0, 0)
    assert [candidate.name for candidate in al_01.candidates] == ["Clyde Jones", "Jerry Carl"]


def test_parse_http_date_reads_iso_2026_stamp_and_falls_back():
    from electionwatch.sources.calls._common import parse_http_date

    assert parse_http_date("2026-11-03T12:44:39.873Z") == datetime(2026, 11, 3, 12, 44, 39, 873000, tzinfo=timezone.utc)
    assert parse_http_date("Fri, 22 Aug 2025 13:29:37 GMT") == datetime(2025, 8, 22, 13, 29, 37, tzinfo=timezone.utc)
    assert parse_http_date("2024") is None
    assert parse_http_date("not a date") is None
    assert parse_http_date(None) is None


def test_feed_href_only_accepts_site_relative_links():
    from electionwatch.sources.calls._common import feed_href

    assert feed_href("/politics/2026-elections/alaska-senate-results") == (
        "https://www.nbcnews.com/politics/2026-elections/alaska-senate-results"
    )
    assert feed_href("https://example.org/elsewhere") is None
    assert feed_href(None) is None


def test_parse_bad_payloads():
    assert parse_nbc_results({"not": "a dict with mapData"}) == []
    assert parse_nbc_results(["not", "a", "dict"]) == []


def test_registered_off_by_default():
    load_all()
    assert REGISTRY["results"]["nbc_results"] is NBCResultsSource
    assert NBCResultsSource.scrape is True
    assert NBCResultsSource.enabled_by_default is False
    assert {"TX", "DC"} <= NBCResultsSource.states


@respx.mock
async def test_fetch_filters_to_requested_keys():
    route = respx.get(f"{NBCResultsSource.BASE}/{NBCResultsSource.YEAR}-elections/senate-results").mock(
        return_value=httpx.Response(200, json=_load("2024-senate.json"))
    )
    async with httpx.AsyncClient() as client:
        results = await NBCResultsSource().fetch([TX_SEN], client)
    assert [race_result.key for race_result in results] == [TX_SEN]
    assert route.called


@respx.mock
async def test_fetch_404_is_empty():
    respx.get(f"{NBCResultsSource.BASE}/{NBCResultsSource.YEAR}-elections/senate-results").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as client:
        assert await NBCResultsSource().fetch([TX_SEN], client) == []


def test_house_title_handles_at_large():
    from electionwatch.sources.calls._common import house_title

    assert house_title("07") == "U.S. House District 7"
    assert house_title("AL") == "U.S. House At-Large"
