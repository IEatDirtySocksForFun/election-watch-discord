"""cnn_results parses CNN's public per-race results JSON."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.cnn_results import CNNResultsSource, parse_cnn_results

FIX = Path(__file__).parent / "fixtures" / "cnn_results"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")
CA_12 = RaceKey("CA", Level.HOUSE, "HOUSE", "12")


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_result():
    result = parse_cnn_results(_load("2024-SG-TX.json"), fetched_at=NOW)
    assert result is not None
    assert result.key == TX_SEN
    assert result.source == "cnn_results"
    assert result.total_votes == 11291854
    assert result.expected_vote_pct == 99
    assert result.title == "U.S. Senate"
    cruz = result.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.pct) == ("Ted Cruz", "REP", 5990741, 53)
    assert cruz.incumbent is True
    assert cruz.winner is False


def test_parse_house_result():
    result = parse_cnn_results(_load("2024-HG-CA-12.json"), fetched_at=NOW)
    assert result is not None
    assert result.key == CA_12
    assert result.title == "U.S. House District 12"
    assert result.candidates[0].party == "DEM"


def test_parse_bad_payloads():
    assert parse_cnn_results({"ecKey": "invalid"}) is None
    assert parse_cnn_results("not a dict") is None


def test_registered_off_by_default():
    load_all()
    assert REGISTRY["results"]["cnn_results"] is CNNResultsSource
    assert CNNResultsSource.scrape is True
    assert CNNResultsSource.enabled_by_default is False
    assert {"TX", "DC"} <= CNNResultsSource.states


@respx.mock
async def test_fetch_filters_to_requested_keys():
    code = "2024-SG-TX"
    route = respx.get(f"{CNNResultsSource.BASE}/{code}.json").mock(
        return_value=httpx.Response(200, json=_load("2024-SG-TX.json"))
    )
    async with httpx.AsyncClient() as client:
        results = await CNNResultsSource().fetch([TX_SEN], client)
    assert [r.key for r in results] == [TX_SEN]
    assert route.called


@respx.mock
async def test_fetch_404_is_empty():
    respx.get(f"{CNNResultsSource.BASE}/2024-SG-TX.json").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        assert await CNNResultsSource().fetch([TX_SEN], client) == []
