import json
from pathlib import Path

from electionwatch.models import Level, RaceKey, RaceCall
from electionwatch.sources.calls.ap import APSource, parse_ap_response

FIX = Path(__file__).parent / "fixtures" / "ap"


def _load(name: str):
    return json.loads((FIX / name).read_text())


def test_parse_senate():
    payload = _load("2024-TX-SEN.json")
    calls = parse_ap_response(payload)
    assert len(calls) == 1
    call = calls[0]
    assert call.key == RaceKey("TX", Level.SENATE, "SEN")
    assert call.outlet == "AP"
    assert call.winner == "Ted Cruz"
    assert call.party == "REP"


def test_parse_house():
    payload = _load("2024-CA-HOUSE.json")
    calls = parse_ap_response(payload)
    assert len(calls) == 1
    call = calls[0]
    assert call.key == RaceKey("CA", Level.HOUSE, "HOUSE", "12")
    assert call.winner == "Lateefah Simon"
    assert call.party == "DEM"


def test_no_winner_no_call():
    payload = {
        "races": [
            {
                "statePostal": "TX",
                "officeID": "S",
                "candidates": [
                    {"first": "A", "last": "B", "party": "Dem", "voteCount": 1}
                ],
            }
        ]
    }
    assert parse_ap_response(payload) == []


def test_house_seat_name_with_district_word():
    payload = {
        "races": [
            {
                "statePostal": "CA",
                "officeID": "H",
                "seatName": "District 12",
                "candidates": [{"first": "A", "last": "B", "party": "Dem", "winner": "X"}],
            }
        ]
    }
    calls = parse_ap_response(payload)
    assert len(calls) == 1
    assert calls[0].key == RaceKey("CA", Level.HOUSE, "HOUSE", "12")
    # No timestamp in the payload: still returns a real datetime, not None.
    assert calls[0].called_at is not None


def test_source_requires_key():
    assert APSource.env == ("AP_API_KEY",)
