from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ak_doe import parse_ak, race_key

FIX = Path(__file__).parent / "fixtures" / "ak_doe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    return parse_ak((FIX / "ENRbyPrecinct.csv").read_text(encoding="utf-8"),
                    fetched_at=AT, url="https://www.elections.alaska.gov/results/24GENR/")


def by_key():
    return {format_key(r.key): r for r in parse()}


def test_us_house_is_at_large_and_sums_precincts():
    house = by_key()["AK-AL"]
    assert house.key.level is Level.HOUSE
    assert house.key.district == "AL"
    assert house.candidates[0].name == "Begich, Nick"
    assert house.candidates[0].party == "REP"
    # Every precinct row for the contest is summed, and percentages are filled in.
    assert house.total_votes == sum(x.votes for x in house.candidates)
    assert house.candidates[0].pct == round(
        100.0 * house.candidates[0].votes / house.total_votes, 2)
    assert house.url == "https://www.elections.alaska.gov/results/24GENR/"


def test_lettered_senate_and_numbered_house_districts():
    keys = by_key()
    assert keys["AK SD-B"].key.district == "B"
    assert keys["AK HD-01"].key.level is Level.STATE
    assert keys["AK HD-01"].key.district == "01"


def test_president_and_ballot_measure_are_local():
    keys = by_key()
    assert keys["AK LOCAL us-president"].candidates
    assert "AK LOCAL bm-1-23amls" in keys
    assert "AK LOCAL supreme-court-borghesan" in keys


def test_titles_are_the_states_own():
    house = by_key()["AK-AL"]
    assert house.title == "U.S. Representative"


def test_race_key_mapping_for_2026_offices():
    assert format_key(race_key("U.S. Senator")) == "AK SEN"
    assert format_key(race_key("Governor / Lt. Governor")) == "AK GOV"
    assert format_key(race_key("House District 40")) == "AK HD-40"
    assert format_key(race_key("Senate District T")) == "AK SD-T"
    assert format_key(race_key("U.S. Representative")) == "AK-AL"


def test_empty_payload_is_not_an_error():
    assert parse_ak("", fetched_at=AT) == []
