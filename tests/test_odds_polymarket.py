import json
from pathlib import Path

from electionwatch.models import Level, RaceKey
from electionwatch.sources.odds.polymarket import (
    PolymarketSource,
    _event_title_to_key,
    parse_polymarket_event,
)

FIX = Path(__file__).parent / "fixtures" / "polymarket"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_event_title_to_key():
    assert _event_title_to_key("Texas Senate Election Winner") == RaceKey("TX", Level.SENATE, "SEN")
    assert _event_title_to_key("California Governor Election Winner") == RaceKey("CA", Level.GOVERNOR, "GOV")
    assert _event_title_to_key("New York House Election Winner District 14") == RaceKey(
        "NY", Level.HOUSE, "HOUSE", "14"
    )
    assert _event_title_to_key("Some random event") is None


def test_parse_texas_senate():
    event = _load("texas-senate.json")
    snaps = parse_polymarket_event(event)
    assert len(snaps) == 2
    assert all(snapshot.key == RaceKey("TX", Level.SENATE, "SEN") for snapshot in snaps)
    dem = next(snapshot for snapshot in snaps if snapshot.outcome == "DEM")
    rep = next(snapshot for snapshot in snaps if snapshot.outcome == "REP")
    assert dem.prob == 0.675
    assert rep.prob == 0.325
    assert dem.market_id == "630963"


def test_parse_california_governor():
    event = _load("california-governor.json")
    snaps = parse_polymarket_event(event)
    assert len(snaps) >= 2
    assert all(snapshot.key == RaceKey("CA", Level.GOVERNOR, "GOV") for snapshot in snaps)
    # At least one named-candidate outcome should be present.
    assert any(snapshot.outcome not in ("DEM", "REP") for snapshot in snaps)


def test_source_has_expected_registry_name():
    assert PolymarketSource.name == "polymarket"
    assert PolymarketSource.enabled_by_default is True


def test_prob_aligns_with_yes_outcome():
    from electionwatch.sources.odds.polymarket import _market_prob

    reversed_market = {
        "outcomes": json.dumps(["No", "Yes"]),
        "outcomePrices": json.dumps(["0.3", "0.7"]),
    }
    assert _market_prob(reversed_market) == 0.7

    normal_market = {
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.7", "0.3"]),
    }
    assert _market_prob(normal_market) == 0.7


def test_invalid_price_is_skipped():
    from electionwatch.sources.odds.polymarket import _market_prob

    assert _market_prob({"outcomes": '["Yes","No"]', "outcomePrices": '["1.5","0"]'}) is None
    assert _market_prob({"outcomes": '["Yes","No"]', "outcomePrices": '["x","0"]'}) is None


async def test_snapshots_are_stamped_with_fetch_time_not_stale_metadata_time():
    from datetime import datetime, timedelta, timezone

    import httpx

    from electionwatch.sources.odds.polymarket import PolymarketSource

    raw = _load("texas-senate.json")
    events = raw if isinstance(raw, list) else [raw]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=events))
    async with httpx.AsyncClient(transport=transport) as client:
        snaps = await PolymarketSource().fetch_odds([RaceKey("TX", Level.SENATE, "SEN")], client)
    assert snaps
    assert all(datetime.now(timezone.utc) - snapshot.ts < timedelta(minutes=1) for snapshot in snaps)


def test_two_candidates_of_one_party_do_not_share_an_outcome_label():
    from electionwatch.sources.odds.polymarket import parse_polymarket_event

    def market(mid, title, price):
        return {
            "id": mid,
            "groupItemTitle": title,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": f'["{price}", "{1 - price}"]',
        }

    event = {
        "title": "California Governor Election Winner",
        "slug": "ca-gov",
        "markets": [
            market(1, "Alice Adams (D)", 0.7),
            market(2, "Bob Brown (D)", 0.25),
            market(3, "Carl Cole (R)", 0.05),
        ],
    }
    outcomes = {snapshot.outcome: snapshot.prob for snapshot in parse_polymarket_event(event)}
    assert outcomes == {"Alice Adams (D)": 0.7, "Bob Brown (D)": 0.25, "REP": 0.05}


# --- House events, hourly discovery and batched CLOB prices (2026-11-03) -------------------------

PA01 = RaceKey("PA", Level.HOUSE, "HOUSE", "01")
CA11 = RaceKey("CA", Level.HOUSE, "HOUSE", "11")


def _house_events() -> list[dict]:
    return _load("house-2026-11-03.json")


def test_house_events_are_titled_by_seat():
    assert _event_title_to_key("PA-01 House Election Winner") == PA01
    assert _event_title_to_key("AK-AL House Election Winner") == RaceKey("AK", Level.HOUSE, "HOUSE", "AL")
    assert _event_title_to_key("CA-11 House Election Winner (Individual)") == CA11
    assert _event_title_to_key("CA-04 House Election Winner (by individual)") == RaceKey(
        "CA", Level.HOUSE, "HOUSE", "04"
    )
    assert _event_title_to_key("PA-01 House Election Margin of Victory") is None
    assert _event_title_to_key("XX-01 House Election Winner") is None


def test_house_party_markets_get_party_outcomes():
    event = next(event for event in _house_events() if event["title"] == "PA-01 House Election Winner")
    snaps = {snapshot.outcome: snapshot for snapshot in parse_polymarket_event(event)}
    assert set(snaps) == {"DEM", "REP"}  # "Other" and the A-E placeholders carry no price
    assert snaps["REP"].candidate == "Brian Fitzpatrick"
    assert snaps["DEM"].candidate == "Bob Harvie"


def _routes(events, *, mids=None, spreads=None, last=None, seen=None):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.host == "gamma-api.polymarket.com":
            return httpx.Response(200, json=events if request.url.params.get("offset") == "0" else [])
        asked = [row["token_id"] for row in json.loads(request.content)]
        if request.url.path == "/midpoints":
            return httpx.Response(200, json={token: mids(token) for token in asked})
        if request.url.path == "/spreads":
            return httpx.Response(200, json={token: spreads(token) for token in asked})
        return httpx.Response(200, json=[{"token_id": token, "price": last(token)} for token in asked])

    return httpx.MockTransport(handler)


async def test_tag_is_read_once_an_hour_and_polls_in_between_use_batched_prices():
    import httpx

    seen: list[str] = []
    transport = _routes(_house_events(), mids=lambda token: "0.4", spreads=lambda token: "0.01", seen=seen)
    clock = [0.0]
    source = PolymarketSource(clock=lambda: clock[0])
    async with httpx.AsyncClient(transport=transport) as client:
        first = await source.fetch_odds([PA01, CA11], client)
        clock[0] = 60.0
        second = await source.fetch_odds([PA01, CA11], client)
    assert seen[:1] == ["/events"] and "/midpoints" not in seen[:1]
    assert seen[1:] == ["/midpoints", "/spreads"]  # one batch each, no Gamma read
    assert {snapshot.key for snapshot in first} == {PA01, CA11}
    assert {snapshot.prob for snapshot in second} == {0.4}


def test_a_same_party_race_uses_its_individual_event():
    import asyncio

    import httpx

    async def go():
        transport = _routes(_house_events(), mids=lambda token: "0.5", spreads=lambda token: "0.01")
        async with httpx.AsyncClient(transport=transport) as client:
            return await PolymarketSource().fetch_odds([CA11], client)

    snaps = asyncio.run(go())
    # The party event says "Democratic Party 96%"; the race is Wiener against Chan.
    assert {snapshot.candidate for snapshot in snaps} == {"Scott Wiener", "Connie Chan"}


async def test_a_wide_spread_is_priced_at_the_last_trade():
    import httpx

    transport = _routes(
        _house_events(), mids=lambda token: "0.305", spreads=lambda token: "0.57", last=lambda token: "0.02"
    )
    clock = [0.0]
    source = PolymarketSource(clock=lambda: clock[0])
    async with httpx.AsyncClient(transport=transport) as client:
        await source.fetch_odds([PA01], client)
        clock[0] = 60.0
        snaps = await source.fetch_odds([PA01], client)
    assert snaps and {snapshot.prob for snapshot in snaps} == {0.02}


async def test_a_failed_price_batch_is_an_error_not_old_prices():
    import httpx
    import pytest

    from electionwatch.sources.base import SourceUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gamma-api.polymarket.com":
            return httpx.Response(200, json=_house_events() if request.url.params.get("offset") == "0" else [])
        return httpx.Response(502)

    clock = [0.0]
    source = PolymarketSource(clock=lambda: clock[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await source.fetch_odds([PA01], client)
        clock[0] = 60.0
        with pytest.raises(SourceUnavailable):
            await source.fetch_odds([PA01], client)


async def test_market_keys_are_the_discovered_races():
    import httpx

    transport = _routes(_house_events(), mids=lambda token: "0.5", spreads=lambda token: "0.01")
    source = PolymarketSource(clock=lambda: 0.0)
    async with httpx.AsyncClient(transport=transport) as client:
        keys = await source.market_keys(client)
    assert set(keys) == {PA01, CA11, RaceKey("AK", Level.HOUSE, "HOUSE", "AL")}
