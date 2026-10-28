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
