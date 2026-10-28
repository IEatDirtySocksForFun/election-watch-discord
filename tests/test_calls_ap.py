import json
from pathlib import Path

from electionwatch.models import Level, RaceKey
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
                "candidates": [{"first": "A", "last": "B", "party": "Dem", "voteCount": 1}],
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


async def test_at_large_key_queries_seat_1(monkeypatch):
    # AP has no "AL": the at-large seat is SeatNum 1, and the race comes back as
    # seatName "1" (RaceKey normalises it to AK-AL). Without SeatNum the query returns
    # nothing for a single-seat state.
    import httpx

    monkeypatch.setenv("AP_API_KEY", "test-key")
    params = []

    def handler(request):
        params.append(dict(request.url.params))
        return httpx.Response(200, json={"races": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        keys = [RaceKey("AK", Level.HOUSE, "HOUSE", "AL"), RaceKey("TX", Level.SENATE, "SEN")]
        assert await APSource().fetch_calls(keys, client) == []
    assert len(params) == 2
    assert params[0]["SeatNum"] == "1"
    assert "SeatNum" not in params[1]


async def test_at_large_call_is_kept_for_the_al_key(monkeypatch):
    import httpx

    monkeypatch.setenv("AP_API_KEY", "test-key")
    payload = _load("2024-AK-HOUSE.json")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        calls = await APSource().fetch_calls([RaceKey("AK", Level.HOUSE, "HOUSE", "AL")], client)
    assert [c.key for c in calls] == [RaceKey("AK", Level.HOUSE, "HOUSE", "AL")]
    assert calls[0].winner == "Nick Begich"
    assert calls[0].party == "REP"


def test_at_large_seat_name_maps_to_al():
    payload = _load("2024-AK-HOUSE.json")
    calls = parse_ap_response(payload)
    assert [c.key for c in calls] == [RaceKey("AK", Level.HOUSE, "HOUSE", "AL")]
