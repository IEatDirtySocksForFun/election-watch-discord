from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.nc_sbe import _rows, parse_nc, race_key

FIX = Path(__file__).parent / "fixtures" / "nc_sbe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return _rows((FIX / name).read_text(encoding="utf-8-sig"))


def parse():
    return parse_nc(load("contest.txt"), load("results_0.txt"), load("county.txt"), fetched_at=AT)


def test_governor_and_party():
    gov = {format_key(r.key): r for r in parse()}["NC GOV"]
    assert gov.key.level is Level.GOVERNOR
    assert gov.candidates[0].name == "Josh Stein"
    assert gov.candidates[0].party == "DEM"
    assert gov.candidates[0].pct == 54.9  # source sends 0.5490
    assert gov.total_votes == sum(c.votes for c in gov.candidates)
    assert gov.title == "NC GOVERNOR"


def test_federal_state_and_house_keys():
    keys = {format_key(r.key) for r in parse()}
    assert "NC-01" in keys  # US House district 01
    assert "NC SD-01" in keys  # state senate
    assert "NC HD-01" in keys  # state house (district 001)
    assert "NC LOCAL us-president" in keys
    assert "NC LOCAL nc-supreme-court-associate-justice-seat-06" in keys


def test_turnout_and_updated_at_come_from_county_file():
    any_race = parse()[0]
    assert any_race.registered_voters == 7763502
    assert any_race.turnout_pct == 73.73
    assert any_race.source_updated_at == datetime(2024, 12, 5, 1, 59, tzinfo=timezone.utc)


def test_precincts_and_percent_reported():
    # District contests only count the precincts inside the district (43 for HD-01).
    r = {format_key(x.key): x for x in parse()}["NC HD-01"]
    assert r.precincts_total == r.precincts_reporting > 0
    assert r.expected_vote_pct == 100.0


def test_constitutional_amendment_becomes_local():
    keys = {format_key(r.key) for r in parse()}
    assert "NC LOCAL constitutional-amendment-citizens-only-voting" in keys


def test_race_key_mapping():
    assert format_key(race_key("US SENATE")) == "NC SEN"
    assert format_key(race_key("US HOUSE OF REPRESENTATIVES DISTRICT 07")) == "NC-07"
    assert format_key(race_key("NC ATTORNEY GENERAL")) == "NC AG"
    assert format_key(race_key("NC STATE TREASURER")) == "NC TREAS"
    assert format_key(race_key("NC COMMISSIONER OF LABOR")) == "NC LOCAL nc-commissioner-of-labor"
    assert format_key(race_key("NC STATE SENATE DISTRICT 01")) == "NC SD-01"
    assert format_key(race_key("NC HOUSE OF REPRESENTATIVES DISTRICT 121")) == "NC HD-121"
    assert format_key(race_key("NC HOUSE OF REPRESENTATIVES DISTRICT 019 (UNEXPIRED)")) == "NC HD-19 S"


def test_empty_payload_is_not_an_error():
    assert parse_nc([], [], [], fetched_at=AT) == []
