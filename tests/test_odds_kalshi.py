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
    reps = [s for s in snaps if s.outcome == "REP"]
    dems = [s for s in snaps if s.outcome == "DEM"]
    assert len(reps) == 1 and len(dems) == 1
    assert reps[0].key == RaceKey("TX", Level.SENATE, "SEN")
    assert dems[0].key == RaceKey("TX", Level.SENATE, "SEN")
    assert 0 <= reps[0].prob <= 1
    assert 0 <= dems[0].prob <= 1


def test_parse_governor_tx():
    payload = _load("govpartytexas-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    assert all(s.key == RaceKey("TX", Level.GOVERNOR, "GOV") for s in snaps)
    assert {s.outcome for s in snaps} == {"REP", "DEM"}


def test_parse_house_ca22():
    payload = _load("houseca22-26.json")
    snaps = parse_kalshi_event(payload)
    assert len(snaps) == 2
    assert all(s.key == RaceKey("CA", Level.HOUSE, "HOUSE", "22") for s in snaps)
    dem = next(s for s in snaps if s.outcome == "DEM")
    rep = next(s for s in snaps if s.outcome == "REP")
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
    assert all(datetime.now(timezone.utc) - s.ts < timedelta(minutes=1) for s in snaps)
