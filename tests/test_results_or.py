import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.or_sos import parse_or, race_key

FIX = Path(__file__).parent / "fixtures" / "or_sos" / "OR"
AT = datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc)


def parse(name):
    return {format_key(r.key): r for r in parse_or(json.loads((FIX / name).read_text(encoding="utf-8")), fetched_at=AT)}


def test_governor_fusion_parties():
    got = parse("2026_general_swpar.json")
    assert set(got) == {"OR GOV"}
    gov = got["OR GOV"]
    assert gov.title == "Governor"
    assert [(c.name, c.party) for c in gov.candidates] == [
        ("Brett Smith", "GRN"),  # Pacific Green
        ("Tina Kotek", "DEM"),  # "Democrat, Working Families"
        ("Christine Drazan", "REP"),  # "Republican, Libertarian"
        ("Write-in", None),  # write-ins carry no party
    ]
    assert gov.total_votes == 0
    assert gov.precincts_total == 1318
    assert gov.precincts_reporting == 0
    assert gov.expected_vote_pct is None  # nothing has reported yet
    assert all(c.pct is None for c in gov.candidates)


def test_us_senate_and_house():
    got = parse("2026_general_fed.json")
    assert len(got) == 7
    assert got["OR SEN"].key.level is Level.SENATE
    assert {"OR-01", "OR-06"} <= set(got)
    assert got["OR-01"].key.level is Level.HOUSE
    assert got["OR-01"].title == "US Representative, 1st District"
    assert "Jeff Merkley" in [c.name for c in got["OR SEN"].candidates]
    assert got["OR SEN"].candidates[0].party in {"DEM", "REP", "LIB", "GRN", "NPA", None}


def test_state_legislative_districts():
    house = parse("2026_general_house.json")
    assert len(house) == 2
    assert all(k.startswith("OR HD-") for k in house)
    assert "OR HD-01" in house
    assert house["OR HD-01"].key.level is Level.STATE

    senate = parse("2026_general_senate.json")
    assert all(k.startswith("OR SD-") for k in senate)
    assert senate["OR SD-03"].title == "State Senator, 3rd District"


def test_empty_measure_payload_is_not_an_error():
    assert parse("2026_general_measure.json") == {}


def test_counts_and_winner_flag_from_constructed_rows():
    # The captured election has no votes counted yet, so the vote/percentage/winner branch
    # is exercised with rows in the documented shape (same fields the service sends).
    payload = {
        "d": [
            {
                "RaceID": 1,
                "RaceName": "Governor",
                "AreaNum": "Statewide Partisan",
                "PartyCode": "NON",
                "PartyName": "Democrat",
                "calcCandidate": "Tina Kotek",
                "calcCandidateVotes": 900,
                "TotalPrecincts": 100,
                "PrecinctsReporting": 60,
                "PrecinctsPartial": 5,
                "Winner": False,
            },
            {
                "RaceID": 1,
                "RaceName": "Governor",
                "AreaNum": "Statewide Partisan",
                "PartyCode": "NON",
                "PartyName": "Republican, Libertarian",
                "calcCandidate": "Christine Drazan",
                "calcCandidateVotes": 600,
                "TotalPrecincts": 100,
                "PrecinctsReporting": 60,
                "PrecinctsPartial": 5,
                "Winner": True,
            },
            {
                "RaceID": 1,
                "RaceName": "Governor",
                "AreaNum": "Statewide Partisan",
                "PartyCode": "",
                "PartyName": "",
                "calcCandidate": "Write-in",
                "calcCandidateVotes": 10,
                "TotalPrecincts": 100,
                "PrecinctsReporting": 60,
                "PrecinctsPartial": 5,
                "Winner": False,
            },
        ]
    }
    (gov,) = parse_or(payload, fetched_at=AT)
    assert [c.name for c in gov.candidates] == ["Tina Kotek", "Christine Drazan", "Write-in"]
    assert [c.pct for c in gov.candidates] == [59.6, 39.74, 0.66]
    assert gov.total_votes == 1510
    assert gov.precincts_reporting == 65  # fully + partially reporting
    assert gov.expected_vote_pct == 65.0
    assert [c.winner for c in gov.candidates] == [False, True, False]


def test_race_key_mapping():
    assert format_key(race_key("US Senator")) == "OR SEN"
    assert format_key(race_key("US Representative, 5th District", "US Representative 5th District")) == "OR-05"
    assert format_key(race_key("Governor")) == "OR GOV"
    assert format_key(race_key("Secretary of State")) == "OR SOS"
    assert format_key(race_key("Attorney General")) == "OR AG"
    assert format_key(race_key("State Treasurer")) == "OR TREAS"
    assert format_key(race_key("State Representative, 12th District")) == "OR HD-12"
    assert format_key(race_key("State Senator, 3rd District")) == "OR SD-03"
    assert format_key(race_key("Measure 118")) == "OR LOCAL measure-118"
    assert race_key("Governor", "Statewide Partisan").state == "OR"
