import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ca_sos import parse_ca, race_key

FIX = Path(__file__).parent / "fixtures" / "ca_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load():
    return json.loads((FIX / "2026-general.json").read_text())


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_statewide_executive_keys():
    keys = by_key(parse_ca(load(), fetched_at=AT))
    assert "CA GOV" in keys
    assert "CA LTGOV" in keys
    assert "CA SOS" in keys
    assert "CA COMP" in keys
    assert "CA TREAS" in keys
    assert "CA AG" in keys
    assert "CA INSCOM" in keys
    assert "CA SUPT" in keys


def test_house_and_legislature_keys():
    keys = by_key(parse_ca(load(), fetched_at=AT))
    assert "CA-01" in keys
    assert "CA-12" in keys
    assert "CA-30" in keys
    assert "CA SD-02" in keys
    assert "CA SD-10" in keys
    assert "CA HD-01" in keys
    assert "CA HD-50" in keys


def test_ballot_measures_become_local():
    keys = by_key(parse_ca(load(), fetched_at=AT))
    # The fixture keeps the first ballot measure.
    assert any(k.startswith("CA LOCAL proposition-") for k in keys)


def test_governor_votes_and_party():
    r = by_key(parse_ca(load(), fetched_at=AT))["CA GOV"]
    assert r.key.level is Level.GOVERNOR
    assert r.candidates[0].name == "Steve Hilton"
    assert r.candidates[0].party == "REP"
    assert r.candidates[0].votes == 11585066
    assert r.total_votes == sum(c.votes for c in r.candidates)
    assert r.precincts_reporting == 23863
    assert r.precincts_total == 23863


def test_house_district_is_padded():
    r = by_key(parse_ca(load(), fetched_at=AT))["CA-01"]
    assert r.key.level is Level.HOUSE
    assert r.key.district == "01"


def test_source_updated_at():
    r = by_key(parse_ca(load(), fetched_at=AT))["CA GOV"]
    assert r.source_updated_at == datetime(2026, 10, 26, 12, 28, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("Governor")) == "CA GOV"
    assert format_key(race_key("Secretary of State")) == "CA SOS"
    assert format_key(race_key("United States Representative District 7")) == "CA-07"
    assert format_key(race_key("State Senate District 15")) == "CA SD-15"
    assert format_key(race_key("State Assembly Member District 42")) == "CA HD-42"
    assert format_key(race_key("Board of Equalization Member District 3")) == \
        "CA LOCAL board-of-equalization-district-3"
