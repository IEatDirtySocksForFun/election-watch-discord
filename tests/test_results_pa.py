import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.pa_dos import latest_rows, parse_pa, race_key

FIX = Path(__file__).parent / "fixtures" / "pa_dos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    # The fixture is a JSON string containing JSON, exactly like the API body.
    return json.loads((FIX / name).read_text())


def parse():
    results = []
    for name in (
        "office_us_senate.json",
        "office_us_house.json",
        "office_state_senate.json",
        "office_state_house.json",
    ):
        results.extend(parse_pa(load(name), fetched_at=AT))
    return {format_key(race_result.key): race_result for race_result in results}


def test_senate_payload_is_double_encoded_and_parsed():
    race_result = parse()["PA SEN"]
    assert race_result.key.level is Level.SENATE
    assert race_result.title == "United States Senator"
    assert race_result.source == "pa_dos"
    assert {"DEM", "REP"} <= {candidate.party for candidate in race_result.candidates}
    top = race_result.candidates[0]
    assert top.name == "Dave McCormick"
    assert top.votes == 3399295
    assert top.pct == 48.82
    assert race_result.total_votes == sum(candidate.votes for candidate in race_result.candidates)
    assert all(candidate.winner is False for candidate in race_result.candidates)


def test_house_and_state_legislature_districts():
    keys = parse()
    assert keys["PA-01"].key.level is Level.HOUSE
    assert keys["PA-01"].title == "1st Congressional District"
    assert keys["PA SD-01"].key.level is Level.STATE
    assert keys["PA HD-01"].key.district == "01"
    assert "PA-04" not in keys  # trimmed out of the fixture


def test_office_name_mapping():
    assert format_key(race_key("United States Senator")) == "PA SEN"
    assert format_key(race_key("Governor")) == "PA GOV"
    assert format_key(race_key("Attorney General")) == "PA AG"
    assert format_key(race_key("Auditor General")) == "PA AUD"
    assert format_key(race_key("State Treasurer")) == "PA TREAS"
    assert format_key(race_key("Representative in Congress", {"District": "8th Congressional District"})) == "PA-08"
    assert (
        format_key(race_key("Senator in the General Assembly", {"District": "1st Senatorial District"})) == "PA SD-01"
    )
    assert (
        format_key(race_key("Representative in the General Assembly", {"District": "121st Legislative District"}))
        == "PA HD-121"
    )
    assert format_key(race_key("President of the United States")) == "PA LOCAL president-of-the-united-states"
    # Unknown questions are kept as a stable LOCAL race, not dropped.
    assert format_key(race_key("Retention of Supreme Court Justice")) == "PA LOCAL retention-of-supreme-court-justice"


def test_latest_election_is_a_json_string_of_a_json_list():
    inner = [
        {
            "ElectionID": 117,
            "ElectionName": "2026 General Election",
            "ElectionDate": "2026-11-03T00:00:00",
            "ElectionType": "G",
        }
    ]
    body = json.dumps(json.dumps(inner))  # the API's double encoding, verbatim
    rows = latest_rows(body)
    assert rows and rows[0]["ElectionID"] == 117
    assert latest_rows("") == []
    assert latest_rows([]) == []


def test_empty_payload_is_not_an_error():
    assert parse_pa("", fetched_at=AT) == []
    assert parse_pa({"Election": {}}, fetched_at=AT) == []
