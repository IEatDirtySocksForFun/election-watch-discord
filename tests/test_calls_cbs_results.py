"""cbs_results parses the same real CBS races2 capture as the cbs call adapter
(tests/fixtures/cbs/races2-2024-tx.json; URL + date in that README.txt)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.cbs_results import CBSResultsSource, parse_cbs_results

FIX = Path(__file__).parent / "fixtures" / "cbs" / "races2-2024-tx.json"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
TX_38 = RaceKey("TX", Level.HOUSE, "HOUSE", "38")


def _payload():
    return json.loads(FIX.read_text())


def test_parse_senate_result():
    results = {r.key: r for r in parse_cbs_results(_payload(), fetched_at=NOW)}
    sen = results[TX_SEN]
    assert sen.source == "cbs_results"
    assert sen.total_votes == 11291854
    assert sen.expected_vote_pct == 99.0
    assert sen.source_updated_at.isoformat() == "2025-01-14T17:23:47+00:00"
    assert sen.url == "https://www.cbsnews.com/elections/2024/tx/senate/"
    assert sen.title == "U.S. Senate"
    cruz = sen.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53.1)
    assert cruz.incumbent is True
    # CBS' isWinner is a projection, not an official declaration.
    assert cruz.winner is False


def test_parse_house_result():
    results = {r.key: r for r in parse_cbs_results(_payload(), fetched_at=NOW)}
    house = results[TX_38]
    assert house.title == "U.S. House District 38"
    assert house.candidates[0].votes == 215030
    assert house.total_votes == 342764


def test_parse_bad_payloads():
    assert parse_cbs_results({"not": "a list"}) == []
    assert parse_cbs_results([{"stateCode": "TX", "office": "X"}, {"stateCode": "??"}]) == []


def test_registered_off_by_default():
    load_all()
    assert REGISTRY["results"]["cbs_results"] is CBSResultsSource
    assert CBSResultsSource.scrape is True
    assert CBSResultsSource.enabled_by_default is False
    assert {"TX", "DC"} <= CBSResultsSource.states


@respx.mock
async def test_fetch_filters_to_requested_keys():
    route = respx.get(f"{CBSResultsSource.BASE}/{CBSResultsSource.YEAR}/G").mock(
        return_value=httpx.Response(200, json=_payload())
    )
    async with httpx.AsyncClient() as client:
        results = await CBSResultsSource().fetch([TX_SEN], client)
    assert [r.key for r in results] == [TX_SEN]
    assert route.calls[0].request.url.params["filter.officeCode"] == "S"
    assert route.calls[0].request.url.params["filter.state"] == "TX"


@respx.mock
async def test_fetch_404_is_empty():
    respx.get(f"{CBSResultsSource.BASE}/{CBSResultsSource.YEAR}/G").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await CBSResultsSource().fetch([TX_SEN], client) == []
