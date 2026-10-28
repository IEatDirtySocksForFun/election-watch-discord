import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.dc_boe import parse_dc, race_key

FIX = Path(__file__).parent / "fixtures" / "dc_boe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    text = (FIX / "2024-general.xml").read_text(encoding="utf-8")
    return parse_dc(ET.fromstring(text.encode("utf-8")), fetched_at=AT)


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_federal_keys():
    keys = by_key(parse())
    assert "DC LOCAL us-president" in keys
    assert "DC-AL" in keys
    assert "DC LOCAL shadow-senator" in keys
    assert "DC LOCAL shadow-representative" in keys


def test_delegate_votes_and_party():
    race_result = by_key(parse())["DC-AL"]
    assert race_result.key.level is Level.HOUSE
    assert race_result.candidates[0].name == "Eleanor Holmes Norton"
    assert race_result.candidates[0].party == "DEM"
    assert race_result.candidates[0].votes == 251540
    assert race_result.total_votes == sum(candidate.votes for candidate in race_result.candidates)


def test_local_keys():
    keys = by_key(parse())
    assert "DC LOCAL council-at-large" in keys
    assert "DC LOCAL council-ward-2" in keys
    assert "DC LOCAL sboe-at-large" in keys
    assert "DC LOCAL sboe-ward-2" in keys
    assert "DC LOCAL initiative-83" in keys
    assert "DC LOCAL anc-8e05" in keys


def test_turnout_from_pseudo_contests():
    race_result = by_key(parse())["DC LOCAL us-president"]
    assert race_result.registered_voters == 464502
    assert race_result.turnout_pct == round(100.0 * 328871 / 464502, 2)


def test_over_under_votes_are_excluded():
    race_result = by_key(parse())["DC LOCAL us-president"]
    names = {candidate.name for candidate in race_result.candidates}
    assert "OVER VOTES" not in names
    assert "UNDER VOTES" not in names
    assert "Write-in" not in names


def test_race_key_mapping():
    assert format_key(race_key("DELEGATE TO THE HOUSE OF REPRESENTATIVES FROM THE DISTRICT OF COLUMBIA")) == "DC-AL"
    assert format_key(race_key("UNITED STATES SENATOR")) == "DC LOCAL shadow-senator"
    assert (
        format_key(race_key("AT-LARGE MEMBER OF THE COUNCIL OF THE DISTRICT OF COLUMBIA"))
        == "DC LOCAL council-at-large"
    )
    assert (
        format_key(race_key("WARD TWO MEMBER OF THE COUNCIL OF THE DISTRICT OF COLUMBIA")) == "DC LOCAL council-ward-2"
    )
    assert format_key(race_key("INITIATIVE 83")) == "DC LOCAL initiative-83"
    assert format_key(race_key("ANC-8E05")) == "DC LOCAL anc-8e05"
