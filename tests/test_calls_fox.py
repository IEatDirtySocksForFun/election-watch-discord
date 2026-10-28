import json
from pathlib import Path

from electionwatch.models import Level, RaceKey
from electionwatch.sources.calls.fox import FOXSource, parse_fox_state

FIX = Path(__file__).parent / "fixtures" / "fox"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate_call():
    calls = parse_fox_state(_load("2024-tx-senate.json"), "TX", Level.SENATE, 2024)
    assert len(calls) == 1
    call = calls[0]
    assert call.key == RaceKey("TX", Level.SENATE, "SEN")
    assert call.winner == "Ted Cruz"
    assert call.party == "REP"
    assert call.outlet == "FOX"
    assert call.url.endswith("/texas")


def test_parse_house_calls():
    calls = parse_fox_state(_load("2024-tx-house.json"), "TX", Level.HOUSE, 2024)
    by_district = {call.key.district: call for call in calls}
    assert by_district["38"].winner == "Wesley Hunt"
    assert by_district["38"].party == "REP"
    assert by_district["07"].winner == "Lizzie Fletcher"
    assert by_district["07"].party == "DEM"
    ts = by_district["38"].called_at.isoformat()
    assert ts.startswith("2024-11-") or ts.startswith("2025-01-")


def test_uncalled_senate_returns_empty():
    payload = _load("2024-tx-senate.json")
    for entry in payload["results"]:
        entry["candidate"]["isWinner"] = False
    assert parse_fox_state(payload, "TX", Level.SENATE, 2024) == []


def test_metadata():
    assert FOXSource.name == "fox"
    assert FOXSource.scrape is True
    assert FOXSource.enabled_by_default is False
    assert FOXSource.env == ()
