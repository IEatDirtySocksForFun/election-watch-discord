"""fox_results parses Fox News' state-level results feeds."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.fox_results import FOXResultsSource, parse_fox_results

FIX = Path(__file__).parent / "fixtures" / "fox_results"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
TX_38 = RaceKey("TX", Level.HOUSE, "HOUSE", "38")


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_result():
    results = parse_fox_results(_load("2024-tx-senate.json"), "TX", Level.SENATE, 2024, fetched_at=NOW)
    assert len(results) == 1
    sen = results[0]
    assert sen.key == TX_SEN
    assert sen.source == "fox_results"
    assert sen.total_votes == 11289029
    assert sen.precincts_reporting == 100
    assert sen.expected_vote_pct == 100
    assert sen.title == "U.S. Senate"
    assert sen.url.endswith("/texas")
    cruz = sen.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53.07)
    assert cruz.incumbent is True
    assert cruz.winner is False


def test_parse_house_result():
    results = {r.key: r for r in parse_fox_results(_load("2024-tx-house.json"), "TX", Level.HOUSE, 2024, fetched_at=NOW)}
    house = results[TX_38]
    assert house.title == "U.S. House District 38"
    assert house.candidates[0].votes == 215030
    assert house.total_votes == 342670


def test_parse_bad_payloads():
    assert parse_fox_results({"not": "expected"}, "TX", Level.SENATE, 2024) == []


def test_registered_off_by_default():
    load_all()
    assert REGISTRY["results"]["fox_results"] is FOXResultsSource
    assert FOXResultsSource.scrape is True
    assert FOXResultsSource.enabled_by_default is False
    assert {"TX", "DC"} <= FOXResultsSource.states


@respx.mock
async def test_fetch_filters_to_requested_keys():
    url = FOXResultsSource()._url(TX_SEN)
    route = respx.get(url).mock(return_value=httpx.Response(200, json=_load("2024-tx-senate.json")))
    async with httpx.AsyncClient() as client:
        results = await FOXResultsSource().fetch([TX_SEN], client)
    assert [r.key for r in results] == [TX_SEN]
    assert route.called


@respx.mock
async def test_fetch_404_is_empty():
    url = FOXResultsSource()._url(TX_SEN)
    respx.get(url).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await FOXResultsSource().fetch([TX_SEN], client) == []
