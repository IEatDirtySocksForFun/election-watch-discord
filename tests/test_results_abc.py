"""abc_results parses ABC News' server-rendered state results widget."""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.abc_results import ABCResultsSource, parse_abc_results

FIX = Path(__file__).parent / "fixtures" / "abc_results"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
TX_38 = RaceKey("TX", Level.HOUSE, "HOUSE", "38")


def _load(name: str) -> str:
    return (FIX / name).read_text()


def test_parse_senate_result():
    results = parse_abc_results(_load("2024-tx-senate.html"), fetched_at=NOW)
    assert len(results) == 1
    sen = results[0]
    assert sen.key == TX_SEN
    assert sen.source == "abc_results"
    assert sen.total_votes == 11291854
    assert sen.expected_vote_pct == 99
    assert sen.title == "U.S. Senate"
    assert "texas-county-senate-election-results" in (sen.url or "")
    cruz = sen.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53)
    assert cruz.incumbent is True
    assert cruz.winner is False


def test_parse_house_result():
    results = parse_abc_results(_load("2024-tx-house.html"), fetched_at=NOW)
    assert len(results) == 1
    house = results[0]
    assert house.key == TX_38
    assert house.candidates[0].votes == 215030


def test_parse_bad_payloads():
    assert parse_abc_results("<html>no data</html>") == []


def test_registered_off_by_default():
    load_all()
    assert REGISTRY["results"]["abc_results"] is ABCResultsSource
    assert ABCResultsSource.scrape is True
    assert ABCResultsSource.enabled_by_default is False
    assert {"TX", "DC"} <= ABCResultsSource.states


@respx.mock
async def test_fetch_filters_to_requested_keys():
    url = ABCResultsSource()._url(TX_SEN)
    route = respx.get(url).mock(return_value=httpx.Response(200, text=_load("2024-tx-senate.html")))
    async with httpx.AsyncClient() as client:
        results = await ABCResultsSource().fetch([TX_SEN], client)
    assert [r.key for r in results] == [TX_SEN]
    assert route.called


@respx.mock
async def test_fetch_404_is_empty():
    url = ABCResultsSource()._url(TX_SEN)
    respx.get(url).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await ABCResultsSource().fetch([TX_SEN], client) == []
