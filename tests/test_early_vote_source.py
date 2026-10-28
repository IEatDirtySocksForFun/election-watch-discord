"""civicAPI early vote: parsers on recorded responses, and the client's cache, rate limit and errors."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
import respx
from fake_civicapi_early_vote import ROUTES, fixture_client, handler, load, mock_http_client

from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.early_vote import (
    API,
    CATEGORIES,
    USER_AGENT,
    EarlyVoteClient,
    comparison_date,
    election_day,
    page_url,
    parse_breakdown,
    parse_capabilities,
    parse_counties,
    parse_demographics,
    parse_national,
    parse_tally,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _national():
    return parse_national({c: load(f"us_{c}.json") for c in CATEGORIES})


# --------------------------------------------------------------------------- parsers
def test_national_map_lists_states_with_ballots_per_category():
    national = _national()
    assert national.updated_at == date(2026, 10, 27)
    assert {"NC", "GA", "TX", "TN", "IL"} <= set(national.states)
    nc = national.states["NC"]
    assert nc["returned"].total == 65841
    assert nc["returned"].by_party == {
        "Democratic": 27481,
        "No Party Affiliation": 26223,
        "Republican": 11949,
        "Other": 188,
    }
    assert nc["inperson"].total == 1708048
    assert national.total("NC") == 65841 + 1708048
    # GA has no party registration: every ballot is "Unspecified", so no party split.
    assert national.states["GA"]["inperson"].by_party == {}
    assert national.states["GA"]["inperson"].total == 1697619
    assert set(national.states["TN"]) == {"inperson"}  # in-person only
    assert set(national.states["IL"]) == {"grace"}  # (trimmed fixture)
    assert national.total("IL") == 0  # grace votes are not in the total


def test_national_matches_the_state_endpoint_total():
    state = parse_tally(load("nc_returned.json"), "returned")
    assert state.total == _national().states["NC"]["returned"].total
    assert state.label == "North Carolina Ballots Returned"


def test_counties_demographics_and_capabilities():
    counties = parse_counties(load("nc_inperson.json"))
    assert counties["Wake"] == sum(v["votes"] for v in load("nc_inperson.json")["regions"]["Wake"].values())
    assert len(counties) == 6
    age = parse_demographics(load("ga_inperson_demographics_age_range.json"))
    assert list(age) == ["Age 18 - 25", "Age 26 - 40", "Age 41 - 65", "Age Over 65"]
    assert sum(age.values()) == 1693913
    assert parse_demographics(load("nc_returned_demographics_party.json"))["Libertarian"] == 161
    caps = parse_capabilities(load("nc_capabilities.json"))
    assert caps["requested"]["age"] is True and caps["returned"]["age"] is False
    assert set(parse_capabilities(load("tn_capabilities.json"))) == {"inperson"}


def test_historical_snapshot_parses_like_a_current_one():
    tally = parse_tally(load("historical_2022_nc_returned.json"), "returned")
    assert tally.total == 507122 + 382921 + 2929 + 403062
    assert tally.label == "NC Absentee Ballots Returned and Accepted"
    assert parse_tally(load("historical_not_found.json"), "returned") is None


@pytest.mark.parametrize(
    "raw", [None, [], "x", {"Democratic": "x"}, {"Democratic": {"votes": None}}, {"Democratic": {"votes": -5}}]
)
def test_breakdown_tolerates_junk(raw):
    assert parse_breakdown(raw) == (0, {})


def test_election_days_and_comparison_dates():
    assert [election_day(y) for y in (2020, 2022, 2024, 2026)] == [
        date(2020, 11, 3),
        date(2022, 11, 8),
        date(2024, 11, 5),
        date(2026, 11, 3),
    ]
    assert comparison_date(date(2026, 10, 27), 2022) == date(2022, 11, 1)  # 7 days out
    assert comparison_date(date(2026, 10, 27), 2024) == date(2024, 10, 29)
    assert comparison_date(date(2026, 11, 4), 2022) is None  # after the election
    assert comparison_date(date(2026, 6, 1), 2022) is None  # not early-vote season
    assert page_url("NC") == "https://civicapi.org/early-vote/nc"


# --------------------------------------------------------------------------- client
async def test_state_view_with_comparison_counties_and_age():
    async with mock_http_client() as http:
        client = fixture_client(http)
        nc = await client.state("nc", counties=True)
        assert nc.state == "NC" and nc.name == "North Carolina"
        assert nc.total_ballots == 1773889
        assert nc.url == "https://civicapi.org/early-vote/nc"
        assert nc.updated_at == date(2026, 10, 27)
        assert nc.by_method == {"returned": 65841, "inperson": 1708048, "requested": 164597}
        assert list(nc.counties)[:2] == ["Wake", "Mecklenburg"]  # most ballots first
        assert nc.comparison.year == 2022 and nc.comparison.on == date(2022, 11, 1)
        assert nc.comparison.total == 1296034  # in-person 2022: 404, skipped
        assert nc.comparison.labels == ("NC Absentee Ballots Returned and Accepted",)
        ga = await client.state("GA", age=True)
        assert ga.by_party == {} and ga.comparison is None
        assert ga.age_categories == ("inperson",)  # returned demographics: 404
        assert sum(ga.age.values()) == 1693913


async def test_state_without_data_is_none():
    async with mock_http_client() as http:
        client = fixture_client(http)
        assert await client.state("NY") is None
        assert await client.state("ZZ") is None


@respx.mock
async def test_answers_are_cached_and_expire():
    clock = Clock()
    routes = {
        c: respx.get(f"{API}/US/{c}").mock(return_value=httpx.Response(200, json=load(f"us_{c}.json")))
        for c in CATEGORIES
    }
    async with httpx.AsyncClient() as http:
        client = EarlyVoteClient(http, clock=clock, sleep=clock.sleep, ttl=600)
        assert client.cached_states() is None
        await client.national()
        await client.national()
        assert all(route.call_count == 1 for route in routes.values())
        assert "NC" in client.cached_states()
        clock.now += 601
        assert client.cached_states() is None
        await client.national()
        assert all(route.call_count == 2 for route in routes.values())


@respx.mock
async def test_requests_are_spaced_one_at_a_time_with_a_descriptive_user_agent():
    clock = Clock()
    for c in CATEGORIES:
        respx.get(f"{API}/US/{c}").mock(return_value=httpx.Response(200, json=load(f"us_{c}.json")))
    async with httpx.AsyncClient() as http:
        client = EarlyVoteClient(http, clock=clock, sleep=clock.sleep, min_interval=1.0)
        await asyncio.gather(client.national(), client.national())  # concurrent callers share the cache
    assert respx.calls.call_count == len(CATEGORIES)
    assert clock.slept == [1.0] * (len(CATEGORIES) - 1)
    assert all(call.request.headers["User-Agent"] == USER_AGENT for call in respx.calls)


@respx.mock
async def test_missing_state_404_is_cached_as_no_data():
    route = respx.get(f"{API}/NY/capabilities").mock(return_value=httpx.Response(404, json=load("not_found.json")))
    async with httpx.AsyncClient() as http:
        client = EarlyVoteClient(http, min_interval=0)
        assert await client._get("NY/capabilities") is None
        assert await client._get("NY/capabilities") is None
    assert route.call_count == 1


@respx.mock
async def test_error_body_with_200_is_no_data():
    respx.get(f"{API}/historical/2022/GA/returned").mock(
        return_value=httpx.Response(200, json=load("historical_not_found.json"))
    )
    async with httpx.AsyncClient() as http:
        client = EarlyVoteClient(http, min_interval=0)
        assert await client._get("historical/2022/GA/returned", {"date": "2022-11-01"}) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="oops"),
        httpx.Response(400, json=load("invalid_parameters.json")),
        httpx.Response(429, text="slow down"),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_failures_raise_source_unavailable_and_are_not_cached(response):
    with respx.mock:
        mocked = respx.get(f"{API}/US/returned")
        if isinstance(response, Exception):
            mocked.mock(side_effect=response)
        else:
            mocked.mock(return_value=response)
        async with httpx.AsyncClient() as http:
            client = EarlyVoteClient(http, min_interval=0)
            with pytest.raises(SourceUnavailable):
                await client.national()
            with pytest.raises(SourceUnavailable):
                await client.state("NC")
            assert mocked.call_count == 2  # the failure was not cached
            mocked.mock(return_value=httpx.Response(200, json=load("us_returned.json")))
            for c in ("requested", "inperson", "grace"):
                respx.get(f"{API}/US/{c}").mock(return_value=httpx.Response(200, json=load(f"us_{c}.json")))
            assert "NC" in (await client.national()).states


async def test_states_with_data_times_out_and_keeps_warming():
    gate = asyncio.Event()

    async def slow(request):
        await gate.wait()
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        client = EarlyVoteClient(http, min_interval=0)
        assert await client.states_with_data(timeout=0.01) is None  # cold + slow: give up fast
        gate.set()
        await client._warming  # the fetch carried on
        assert "NC" in client.cached_states()
        assert "NC" in await client.states_with_data(timeout=0.01)


async def test_states_with_data_is_none_when_civicapi_is_down():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as http:
        assert await EarlyVoteClient(http, min_interval=0).states_with_data(timeout=1) is None


def test_every_fixture_route_has_a_file():
    for name in ROUTES.values():
        assert load(name)
