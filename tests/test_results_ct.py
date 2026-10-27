import csv
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ct_sots import parse_ct, race_key

FIX = Path(__file__).parent / "fixtures" / "ct_sots"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load():
    with (FIX / "2024-general.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_federal_keys():
    keys = by_key(parse_ct(load(), fetched_at=AT))
    assert "CT LOCAL us-president" in keys
    assert "CT SEN" in keys
    assert "CT-01" in keys


def test_state_legislature_keys():
    keys = by_key(parse_ct(load(), fetched_at=AT))
    assert "CT SD-01" in keys
    assert "CT HD-02" in keys


def test_write_in_candidates_included():
    r = by_key(parse_ct(load(), fetched_at=AT))["CT LOCAL us-president"]
    assert any(c.party == "WRI" for c in r.candidates)


def test_president_aggregated_votes():
    r = by_key(parse_ct(load(), fetched_at=AT))["CT LOCAL us-president"]
    assert r.key.level is Level.LOCAL
    assert r.candidates[0].name == "Kamala Harris and Tim Walz"
    assert r.candidates[0].party == "DEM"
    assert r.candidates[0].votes == 1748399
    assert r.total_votes == sum(c.votes for c in r.candidates)


def test_us_house_district_padded():
    r = by_key(parse_ct(load(), fetched_at=AT))["CT-01"]
    assert r.key.level is Level.HOUSE
    assert r.key.district == "01"


def test_race_key_mapping():
    assert format_key(race_key("President")) == "CT LOCAL us-president"
    assert format_key(race_key("United States Senator")) == "CT SEN"
    assert format_key(race_key("Representative in Congress", "Representative in Congress District 5")) == "CT-05"
    assert format_key(race_key("State Senator", "State Senate District 12")) == "CT SD-12"
    assert format_key(race_key("State Representative", "State Representative District 42")) == "CT HD-42"
    assert format_key(race_key("Constitutional Amendment 1")) == "CT LOCAL constitutional-amendment-1"
