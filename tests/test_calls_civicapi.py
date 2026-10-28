"""civicAPI: race-key mapping, results, calls, test-data filtering and paging."""
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from electionwatch.__main__ import build_sources
from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import parse
from electionwatch.sources.base import REGISTRY, load_all
from electionwatch.sources.calls.civicapi import (
    API, PAGE_SIZE, CivicAPICallSource, CivicAPIResultsSource, _CivicAPI, parse_call, parse_race,
    race_key,
)

FIX = Path(__file__).parent / "fixtures" / "civicapi"
NOW = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)
TX_SEN = RaceKey("TX", Level.SENATE, "SEN")


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_ranked_after_the_official_adapters_and_on_by_default():
    load_all()
    assert "civicapi" in REGISTRY["calls"]
    built = [source.name for source in build_sources(REGISTRY, {"EW_ENABLED_SOURCES": "fox_results"})["results"]]
    assert built.index("civicapi") > built.index("tx_sos")
    assert built.index("fox_results") > built.index("tx_counties")
    assert not any(getattr(REGISTRY["results"][name], "fallback", False) for name in built[: built.index("civicapi")])


def test_race_keys_from_search_rows():
    keys = {row["election_name"]: race_key(row) for row in _load("search_tx_2026.json")["races"]}
    assert keys["Texas US Senate"] == TX_SEN
    assert keys["Texas US House 35"] == parse("TX-35")
    assert keys["Texas Lieutenant Governor"] == parse("TX LTGOV")
    assert keys["Texas State House 121"] == parse("TX HD-121")
    assert keys["Texas State House"] is None          # chamber-control summary, not a race
    assert keys["El Paso District Judge, 327th Judicial District"] == parse(
        "TX LOCAL el-paso-district-judge-327th-judicial-district")


@pytest.mark.parametrize("row,expected", [
    ({"country": "US", "province": "AK", "type": "House of Representatives", "district": "AK-AL"}, "AK-AL"),
    ({"country": "US", "province": "GA", "type": "US Senate", "election_name": "Georgia US Senate Special"}, "GA SEN S"),
    ({"country": "US", "province": "TX", "type": "Treasurer", "election_type": "Local", "district": "Bexar",
      "election_name": "Bexar County Treasurer"}, "TX LOCAL bexar-county-treasurer"),
    ({"country": "CA", "province": "ON", "type": "Mayor", "election_name": "Toronto Mayor"}, None),
    ({"country": "US", "province": "TX", "type": "House of Representatives", "district": "garbage"}, None),
])
def test_race_key_edge_cases(row, expected):
    key = race_key(row)
    assert (key if expected is None else key == parse(expected)) in (None, True)


def test_parse_a_real_race():
    result = parse_race(_load("race_tx_sen_2024.json"), 107793, TX_SEN, NOW)
    assert result.source == "civicapi"
    assert result.url == "https://civicapi.org/results/elections/107793"
    assert result.expected_vote_pct == 100
    cruz = result.candidates[0]
    assert (cruz.name, cruz.party, cruz.votes, cruz.incumbent, cruz.winner) == (
        "Ted Cruz", "REP", 5990741, True, False)
    assert result.total_votes == sum(c.votes for c in result.candidates)


def test_a_real_call():
    call = parse_call(_load("race_tx_sen_2024.json"), 107793, TX_SEN)
    assert call is None or (call.outlet, call.winner, call.party) == ("CIVICAPI", "Ted Cruz", "REP")


def test_test_data_is_never_a_result_or_a_call():
    race = _load("race_tx_sen_2026_test.json")
    assert race["test_data"] is True and any(c["winner"] for c in race["candidates"])
    assert parse_race(race, 87438, TX_SEN, NOW) is None
    assert parse_call(race, 87438, TX_SEN) is None


def test_votes_before_the_polls_open_are_not_results():
    race = _load("race_tx_sen_2026_test.json") | {"test_data": False}
    before = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    assert parse_race(race, 87438, TX_SEN, before) is None
    assert parse_race(race, 87438, TX_SEN, NOW) is not None


@respx.mock
async def test_sources_share_one_request_per_race():
    search = respx.get(f"{API}/race/search").respond(json=_load("search_tx_2026.json"))
    real = _load("race_tx_sen_2024.json") | {"called_at": "2024-11-06T02:10:00.000Z"}
    detail = respx.get(f"{API}/race/87438").respond(json=real)
    api = _CivicAPI()
    async with httpx.AsyncClient() as client:
        results = await CivicAPIResultsSource(api).fetch([TX_SEN, parse("PA SEN")], client)
        calls = await CivicAPICallSource(api).fetch_calls([TX_SEN], client)
        races = await CivicAPIResultsSource(api).list_races("TX", client)
    assert [r.key for r in results] == [TX_SEN]
    assert [(c.outlet, c.winner) for c in calls] == [("CIVICAPI", "Ted Cruz")]
    assert search.call_count == 2          # TX (cached afterwards) and PA
    assert detail.call_count == 1          # results and calls share the cached race
    assert (TX_SEN, "Texas US Senate") in races


@respx.mock
async def test_test_data_from_the_live_api_is_dropped():
    respx.get(f"{API}/race/search").respond(json=_load("search_tx_2026.json"))
    respx.get(f"{API}/race/87438").respond(json=_load("race_tx_sen_2026_test.json"))
    async with httpx.AsyncClient() as client:
        api = _CivicAPI()
        assert await CivicAPIResultsSource(api).fetch([TX_SEN], client) == []
        assert await CivicAPICallSource(api).fetch_calls([TX_SEN], client) == []


@respx.mock
async def test_state_index_pages_past_the_limit():
    row = _load("search_tx_2026.json")["races"][-1]
    full = [dict(row, id=i, election_name=f"Race {i}", type="Judge", district=None) for i in range(PAGE_SIZE)]
    route = respx.get(f"{API}/race/search")
    route.side_effect = [
        httpx.Response(200, json={"races": full}),
        httpx.Response(200, json={"races": [dict(row, id=99999)]}),
    ]
    async with httpx.AsyncClient() as client:
        index = await _CivicAPI().state_races("KY", client)
    assert route.call_count == 2
    assert route.calls[1].request.url.params["offset"] == str(PAGE_SIZE)
    assert index[TX_SEN][0] == 99999


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, text="<html>")])
async def test_failures_raise_source_unavailable(response):
    from electionwatch.sources.base import SourceUnavailable

    respx.get(f"{API}/race/search").mock(return_value=response)
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceUnavailable):
            await CivicAPIResultsSource(_CivicAPI()).fetch([TX_SEN], client)


@respx.mock
async def test_a_called_race_is_not_asked_again():
    respx.get(f"{API}/race/search").respond(json=_load("search_tx_2026.json"))
    real = _load("race_tx_sen_2024.json") | {"called_at": "2024-11-06T02:10:00.000Z"}
    detail = respx.get(f"{API}/race/87438").respond(json=real)
    source = CivicAPICallSource(_CivicAPI())
    async with httpx.AsyncClient() as client:
        first = await source.fetch_calls([TX_SEN], client)
        source.api.races.clear()                      # even with nothing cached
        second = await source.fetch_calls([TX_SEN], client)
    assert first == second and len(first) == 1
    assert detail.call_count == 1


@respx.mock
async def test_uncalled_races_are_rechecked_every_five_minutes_not_every_tick(monkeypatch):
    import electionwatch.sources.calls.civicapi as civic

    respx.get(f"{API}/race/search").respond(json=_load("search_tx_2026.json"))
    detail = respx.get(f"{API}/race/87438").respond(json=_load("race_tx_sen_2024.json"))
    clock = [1000.0]
    monkeypatch.setattr(civic.time, "monotonic", lambda: clock[0])
    source = CivicAPICallSource(_CivicAPI())
    async with httpx.AsyncClient() as client:
        for minute in range(6):
            clock[0] = 1000.0 + minute * 60
            await source.fetch_calls([TX_SEN], client)
    assert detail.call_count == 2                     # t=0 and t=300 s


@pytest.mark.parametrize("row,expected", [
    # AZ/CA/DE/MD label their US Senate race "Senate".
    ({"country": "US", "province": "AZ", "type": "Senate", "district": None,
      "election_name": "Arizona Senate"}, "AZ SEN"),
    ({"country": "US", "province": "TX", "type": "House of Representatives", "district": "TX-18",
      "election_name": "Texas US House 18 Special"}, "TX-18 S"),
    ({"country": "US", "province": "TX", "type": "State House", "district": "TX-HD-56",
      "election_name": "Texas State House 56 Special"}, "TX HD-56 S"),
    ({"country": "US", "province": "VA", "type": "House of Representatives", "district": "VA-05",
      "election_name": "Virginia US House 5 Democratic Primary"}, None),
])
def test_race_key_specials_primaries_and_plain_senate(row, expected):
    key = race_key(row)
    assert key == (parse(expected) if expected else None)


@respx.mock
async def test_the_row_with_a_county_breakdown_wins_over_a_shadow_row():
    shadow = {"id": 1, "country": "US", "province": "MA", "type": "US Senate", "district": None,
              "election_name": "Massachusetts US Senate", "has_breakdown": False}
    live = dict(shadow, id=2, has_breakdown=True)
    respx.get(f"{API}/race/search").respond(json={"races": [shadow, live]})
    async with httpx.AsyncClient() as client:
        index = await _CivicAPI().state_races("MA", client)
    assert index[parse("MA SEN")][0] == 2


def test_zero_percent_reporting_with_votes_is_unknown():
    race = _load("race_tx_sen_2024.json") | {"percent_reporting": 0}
    assert parse_race(race, 107793, TX_SEN, NOW).expected_vote_pct is None
    empty = race | {"candidates": [dict(c, votes=0) for c in race["candidates"]]}
    assert parse_race(empty, 107793, TX_SEN, NOW).expected_vote_pct == 0


def test_party_names_use_the_party_table():
    race = _load("race_tx_sen_2024.json")
    parties = {c.name: c.party for c in parse_race(race, 107793, TX_SEN, NOW).candidates}
    assert parties["Analisa Roche"] == "ASP"     # "American Solidarity"
