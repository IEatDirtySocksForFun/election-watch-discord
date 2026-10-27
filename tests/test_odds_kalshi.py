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
