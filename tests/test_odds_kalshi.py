import json
from pathlib import Path

import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.sources.odds.kalshi import KalshiSource, parse_kalshi_event

FIX = Path(__file__).parent / "fixtures" / "kalshi"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_tx():
    payload = _load("senatetx-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    reps = [snapshot for snapshot in snaps if snapshot.outcome == "REP"]
    dems = [snapshot for snapshot in snaps if snapshot.outcome == "DEM"]
    assert len(reps) == 1 and len(dems) == 1
    assert reps[0].key == RaceKey("TX", Level.SENATE, "SEN")
    assert dems[0].key == RaceKey("TX", Level.SENATE, "SEN")
    assert 0 <= reps[0].prob <= 1
    assert 0 <= dems[0].prob <= 1


def test_parse_governor_tx():
    payload = _load("govpartytexas-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    assert all(snapshot.key == RaceKey("TX", Level.GOVERNOR, "GOV") for snapshot in snaps)
    assert {snapshot.outcome for snapshot in snaps} == {"REP", "DEM"}


def test_parse_house_ca22():
    payload = _load("houseca22-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    assert all(snapshot.key == RaceKey("CA", Level.HOUSE, "HOUSE", "22") for snapshot in snaps)
    dem = next(snapshot for snapshot in snaps if snapshot.outcome == "DEM")
    rep = next(snapshot for snapshot in snaps if snapshot.outcome == "REP")
    assert dem.prob == pytest.approx(0.915, abs=0.01)
    assert rep.prob == pytest.approx(0.09, abs=0.01)


def test_source_key_to_event_ticker():
    src = KalshiSource()
    assert src._key_to_event_ticker(RaceKey("TX", Level.SENATE, "SEN")) == "SENATETX-26"
    assert src._key_to_event_ticker(RaceKey("TX", Level.GOVERNOR, "GOV")) == "GOVPARTYTX-26"
    assert src._key_to_event_ticker(RaceKey("CA", Level.HOUSE, "HOUSE", "01")) == "HOUSECA1-26"
    assert src._key_to_event_ticker(RaceKey("AK", Level.GOVERNOR, "GOV")) == "KXGOVAK-26"


def test_unknown_event_ticker_yields_no_snapshots():
    # Presidential / control-of-chamber events must not be mislabelled as US SEN.
    payload = {
        "event": {"event_ticker": "PRES-2028", "series_ticker": "PRES"},
        "markets": [
            {
                "ticker": "PRES-2028-D",
                "title": "Will the Democratic Party win the presidency?",
                "last_price_dollars": "0.42",
            }
        ],
    }
    assert parse_kalshi_event(payload) == []


def test_market_prob_falls_back_to_cent_fields():
    payload = {
        "event": {"event_ticker": "SENATETX-26", "series_ticker": "SENATETX"},
        "markets": [
            {
                "ticker": "SENATETX-26-R",
                "title": "Will Republicans win?",
                "last_price": 57,
            }
        ],
    }
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 1
    assert snaps[0].prob == pytest.approx(0.57)


def test_out_of_range_price_is_skipped():
    from electionwatch.sources.odds.kalshi import _market_prob

    assert _market_prob({"last_price_dollars": "1.4"}) is None
    assert _market_prob({"last_price_dollars": "nan"}) is None


def test_suffix_wins_over_rules_mentioning_other_party():
    from electionwatch.sources.odds.kalshi import _market_outcome

    market = {
        "ticker": "SENATETX-26-D",
        "title": "Will the Democratic Party win the Texas Senate race?",
        "rules_primary": "If the Republican candidate is certified...",
    }
    assert _market_outcome(market) == "DEM"


@pytest.mark.asyncio
async def test_at_large_key_does_not_abort_the_fetch():
    import httpx

    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        keys = [RaceKey("AK", Level.HOUSE, "HOUSE", "AL"), RaceKey("TX", Level.SENATE, "SEN")]
        assert await KalshiSource().fetch_odds(keys, client) == []
    assert any(path.endswith("/SENATETX-26") for path in seen)


@pytest.mark.asyncio
async def test_snapshots_are_stamped_with_fetch_time_not_stale_metadata_time():
    from datetime import datetime, timedelta, timezone

    import httpx

    payload = _load("senatetx-26.json")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        snaps = await KalshiSource().fetch_odds([RaceKey("TX", Level.SENATE, "SEN")], client)
    assert snaps
    assert all(datetime.now(timezone.utc) - snapshot.ts < timedelta(minutes=1) for snapshot in snaps)


def test_parse_house_at_large_ak():
    # Real HOUSEAKAL-26 payload: the at-large seat's ticker spells the district out.
    payload = _load("houseakal-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    assert all(snapshot.key == RaceKey("AK", Level.HOUSE, "HOUSE", "AL") for snapshot in snaps)
    by_outcome = {snapshot.outcome: snapshot for snapshot in snaps}
    # Tight books are priced at their midpoint, not the last trade: REP 0.77/0.78 (last 0.78),
    # DEM 0.012/0.027 (last 0.03, outside the current book).
    assert by_outcome["REP"].prob == pytest.approx(0.775, abs=1e-6)
    assert by_outcome["DEM"].prob == pytest.approx(0.0195, abs=1e-6)
    assert by_outcome["REP"].url == "https://kalshi.com/markets/houseakal"


def test_source_key_to_event_ticker_at_large():
    src = KalshiSource()
    assert src._key_to_event_ticker(RaceKey("AK", Level.HOUSE, "HOUSE", "AL")) == "HOUSEAKAL-26"
    assert src._key_to_event_ticker(RaceKey("DE", Level.HOUSE, "HOUSE", "AL")) == "HOUSEDEAL-26"
    # A single-seat state's seat 1 is normalised to AL by RaceKey itself.
    assert src._key_to_event_ticker(RaceKey("AK", Level.HOUSE, "HOUSE", "01")) == "HOUSEAKAL-26"
    assert src._key_to_event_ticker(RaceKey("TX", Level.HOUSE, "HOUSE", "01")) == "HOUSETX1-26"


@pytest.mark.asyncio
async def test_fetch_odds_requests_the_at_large_event():
    import httpx

    payload = _load("houseakal-26.json")
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snaps = await KalshiSource().fetch_odds([RaceKey("AK", Level.HOUSE, "HOUSE", "AL")], client)
    assert seen == ["/trade-api/v2/events/HOUSEAKAL-26"]
    assert {snapshot.outcome for snapshot in snaps} == {"REP", "DEM"}
    assert all(snapshot.key == RaceKey("AK", Level.HOUSE, "HOUSE", "AL") for snapshot in snaps)


def test_special_elections_have_their_own_series():
    # Ohio's and Florida's 2026 Senate races are specials; Kalshi has no SENATEOH-26 event.
    src = KalshiSource()
    assert src._key_to_event_ticker(RaceKey("OH", Level.SENATE, "SEN", special=True)) == "SENATEOHS-26"
    assert src._key_to_event_ticker(RaceKey("FL", Level.SENATE, "SEN", special=True)) == "SENATEFLS-26"
    assert src._key_to_event_ticker(RaceKey("TX", Level.SENATE, "SEN")) == "SENATETX-26"


def test_special_election_event_parses_to_the_special_key():
    payload = json.loads((FIX / "senateohs-26.json").read_text(encoding="utf-8"))
    snapshots = parse_kalshi_event(payload)
    assert snapshots and {str(snapshot.key) for snapshot in snapshots} == {"OH SEN S"}
    assert {snapshot.outcome for snapshot in snapshots} == {"DEM", "REP"}


# --- prices: midpoint of a tight book, one batched request per poll ------------------------------


def test_a_stray_last_trade_does_not_move_a_tight_book():
    from electionwatch.sources.odds.kalshi import _market_prob

    # PA-01 on 2026-11-03 01:24 ET: one trade at 0.69 against a 0.36/0.37 book.
    assert _market_prob({"last_price_dollars": "0.6900", "yes_bid_dollars": "0.3600", "yes_ask_dollars": "0.3700"}) == (
        pytest.approx(0.365)
    )
    # A wide book (or one side missing) falls back to the last trade, then the bid.
    assert _market_prob({"last_price_dollars": "0.4000", "yes_bid_dollars": "0.1000", "yes_ask_dollars": "0.9000"}) == (
        pytest.approx(0.40)
    )
    assert _market_prob({"last_price_dollars": "0.0000", "yes_bid_dollars": "0.2000", "yes_ask_dollars": "0"}) == (
        pytest.approx(0.20)
    )
    assert _market_prob({"last_price": 55, "yes_bid": 50, "yes_ask": 54}) == pytest.approx(0.52)


async def test_layout_is_looked_up_once_then_prices_come_from_one_batch():
    import httpx

    event = _load("senatetx-26.json")
    tickers = [market["ticker"] for market in event.get("markets") or event["event"]["markets"]]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/markets"):
            asked = request.url.params["tickers"].split(",")
            rows = [{"ticker": ticker, "yes_bid_dollars": "0.6000", "yes_ask_dollars": "0.6200"} for ticker in asked]
            return httpx.Response(200, json={"markets": rows})
        if request.url.path.endswith("/events/SENATENE-26"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=event)

    clock = [0.0]
    source = KalshiSource(clock=lambda: clock[0])
    keys = [RaceKey("TX", Level.SENATE, "SEN"), RaceKey("NE", Level.SENATE, "SEN")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await source.fetch_odds(keys, client)
        clock[0] = 120.0
        second = await source.fetch_odds(keys, client)
        clock[0] = 3700.0
        await source.fetch_odds(keys, client)
    assert first and {snapshot.key for snapshot in first} == {RaceKey("TX", Level.SENATE, "SEN")}
    # First poll: one /events per race (NE answers 404). Second: one batch, NE not asked again.
    assert seen[:2] == ["/trade-api/v2/events/SENATETX-26", "/trade-api/v2/events/SENATENE-26"]
    assert seen[2:3] == ["/trade-api/v2/markets"]
    assert {snapshot.market_id for snapshot in second} == set(tickers)
    assert all(snapshot.prob == pytest.approx(0.61) for snapshot in second)
    # After an hour both races are looked up again.
    assert seen[3:] == ["/trade-api/v2/events/SENATETX-26", "/trade-api/v2/events/SENATENE-26"]


async def test_a_failed_batch_is_an_error_not_old_prices():
    import httpx

    from electionwatch.sources.base import SourceUnavailable

    event = _load("senatetx-26.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/markets"):
            return httpx.Response(503)
        return httpx.Response(200, json=event)

    clock = [0.0]
    source = KalshiSource(clock=lambda: clock[0])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await source.fetch_odds([RaceKey("TX", Level.SENATE, "SEN")], client)
        clock[0] = 60.0
        with pytest.raises(SourceUnavailable):
            await source.fetch_odds([RaceKey("TX", Level.SENATE, "SEN")], client)
