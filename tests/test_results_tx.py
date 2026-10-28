import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.tx_sos import parse_tx, race_key

FIX = Path(__file__).parent / "fixtures" / "tx_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture(scope="module")
def results():
    payloads = {
        "Federal": load("federal.json"),
        "StateWide": load("statewide.json"),
        "Districted": load("districted.json"),
        "StateWideQ": load("statewide_questions.json"),
    }
    return parse_tx(payloads, load("home.json"), fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_senate_maps_to_canonical_key(results):
    r = by_key(results)["TX SEN"]
    assert r.source == "tx_sos"
    assert r.key.level is Level.SENATE
    assert r.title.strip() == "U. S. SENATOR"
    assert [c.name for c in r.candidates][:2] == ["Ted Cruz", "Colin Allred"]
    # Texas publishes no winner flag in these files.
    assert all(c.winner is False for c in r.candidates)
    assert r.url == "https://results.texas-election.com/races"
    assert r.fetched_at == AT


def test_house_districts_are_zero_padded(results):
    keys = by_key(results)
    assert keys["TX-01"].key.level is Level.HOUSE
    assert keys["TX-01"].key.district == "01"
    assert keys["TX-34"].key.district == "34"
    assert keys["TX-01"].total_votes > 0


def test_statewide_offices_and_incumbent_flag(results):
    rrc = by_key(results)["TX RRC"]
    assert rrc.key.level is Level.GOVERNOR
    top = rrc.candidates[0]
    assert top.name == "Christi Craddick"  # "(I)" stripped into the flag
    assert top.incumbent is True
    assert top.party == "REP"
    assert rrc.candidates == sorted(rrc.candidates, key=lambda c: c.votes, reverse=True)


def test_state_legislature_and_local_slugs(results):
    keys = by_key(results)
    assert keys["TX SD-06"].key.level is Level.STATE
    assert keys["TX HD-01"].key.district == "01"
    sboe = keys["TX LOCAL state-board-of-education-1"]
    assert sboe.key.level is Level.LOCAL
    assert "TX LOCAL president-vice-president" in keys


def test_statewide_reporting_block_is_applied(results):
    r = by_key(results)["TX SEN"]
    assert (r.precincts_reporting, r.precincts_total) == (9885, 9885)
    assert r.expected_vote_pct == 100.0
    assert r.source_updated_at == datetime(2024, 11, 26, 18, 57, 43, tzinfo=timezone.utc)
    assert r.total_votes == sum(c.votes for c in r.candidates)


def test_race_key_mapping_cases():
    assert format_key(race_key("U. S. SENATOR ")) == "TX SEN"
    assert format_key(race_key("U. S. REPRESENTATIVE DISTRICT 7")) == "TX-07"
    assert format_key(race_key("GOVERNOR")) == "TX GOV"
    assert format_key(race_key("LIEUTENANT GOVERNOR")) == "TX LTGOV"
    assert format_key(race_key("ATTORNEY GENERAL")) == "TX AG"
    assert format_key(race_key("Comptroller Of Public Accounts")) == "TX COMP"
    assert format_key(race_key("Commissioner Of The General Land Office")) == "TX LANDCOM"
    assert format_key(race_key("STATE SENATOR, DISTRICT 15 - UNEXPIRED TERM")) == "TX SD-15 S"
    assert race_key("STATE SENATOR, DISTRICT 15 - UNEXPIRED TERM").special is True
    assert format_key(race_key("JUSTICE, SUPREME COURT, PLACE 4")) == "TX LOCAL supreme-court-place-4"
    # Unknown contests still get a stable LOCAL slug rather than being dropped.
    assert format_key(race_key("Some County Measure A")) == "TX LOCAL some-county-measure-a"


def test_statewide_precinct_counts_are_not_given_to_district_races(results):
    keys = by_key(results)
    for district in ("TX-01", "TX SD-06", "TX HD-01"):
        assert keys[district].precincts_total is None
        assert keys[district].expected_vote_pct is None
