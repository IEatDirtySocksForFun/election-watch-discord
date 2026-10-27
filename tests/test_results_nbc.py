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
    results = {r.key: r for r in parse_nbc_results(_load("2024-senate.json"), fetched_at=NOW)}
    sen = results[TX_SEN]
    assert sen.source == "nbc_results"
    assert sen.total_votes == 11291854
    assert sen.expected_vote_pct == 100
    assert sen.title == "U.S. Senate"
    assert sen.url == "https://www.nbcnews.com/politics/2024-elections/tx-senate-results"
    cruz = sen.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53.1)
    assert cruz.incumbent is True
    assert cruz.winner is False


def test_parse_house_result():
    results = {r.key: r for r in parse_nbc_results(_load("2024-house.json"), fetched_at=NOW)}
    house = results[TX_38]
    assert house.title == "U.S. House District 38"
    assert house.candidates[0].votes == 215030
    assert house.total_votes == 342764


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
    assert [r.key for r in results] == [TX_SEN]
    assert route.called


@respx.mock
async def test_fetch_404_is_empty():
    respx.get(f"{NBCResultsSource.BASE}/{NBCResultsSource.YEAR}-elections/senate-results").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as client:
        assert await NBCResultsSource().fetch([TX_SEN], client) == []
