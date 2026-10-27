import json
from pathlib import Path

from electionwatch.models import Level, RaceKey, RaceCall
from electionwatch.sources.calls.cnn import CNNSource, parse_cnn_race
from electionwatch.sources.calls._common import key_to_cnn_code

FIX = Path(__file__).parent / "fixtures" / "cnn"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_called():
    payload = _load("2024-SG-TX.json")
    call = parse_cnn_race(payload)
    assert isinstance(call, RaceCall)
    assert call.key == RaceKey("TX", Level.SENATE, "SEN")
    assert call.outlet == "CNN"
    assert call.winner == "Ted Cruz"
    assert call.party == "REP"


def test_called_at_uses_calls_release_not_vote_timestamp():
    payload = _load("2024-SG-TX.json")
    call = parse_cnn_race(payload)
    # raceEmbargo.callsRelease (2024-11-06T02:00Z), not the January 2025 vote dump.
    assert call.called_at.isoformat() == "2024-11-06T02:00:00+00:00"


def test_parse_house_called():
    payload = _load("2024-HG-CA-12.json")
    call = parse_cnn_race(payload)
    assert call.key == RaceKey("CA", Level.HOUSE, "HOUSE", "12")
    assert call.party == "DEM"


def test_parse_uncalled_returns_none():
    assert parse_cnn_race({"editorialStatus": "too early to call"}) is None


def test_key_to_cnn_code():
    assert key_to_cnn_code(RaceKey("TX", Level.SENATE, "SEN")) == "2026-SG-TX"
    assert key_to_cnn_code(RaceKey("CA", Level.GOVERNOR, "GOV")) == "2026-GO-CA"
    assert key_to_cnn_code(RaceKey("CA", Level.HOUSE, "HOUSE", "12")) == "2026-HG-CA-12"


def test_source_registry():
    assert CNNSource.name == "cnn"
    assert CNNSource.outlet == "CNN"
    assert CNNSource.enabled_by_default is False
