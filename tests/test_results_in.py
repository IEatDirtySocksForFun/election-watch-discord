import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.in_sos import categories, parse_category, race_key

FIX = Path(__file__).parent / "fixtures" / "in_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def parse(name):
    url = f"https://enr.indianavoters.in.gov/archive/2024General/data/{name}"
    return parse_category(load(name), fetched_at=AT, url=url)


def by_key(name="OffCatC_1005_A.json"):
    return {format_key(r.key): r for r in parse(name)}


def test_us_senate_and_house_districts():
    senate = {format_key(r.key): r for r in parse("OffCatC_1006_A.json")}["IN SEN"]
    assert senate.key.level is Level.SENATE
    assert senate.candidates[0].name == "Jim Banks"
    assert senate.candidates[0].party == "REP"
    assert senate.candidates[0].winner is True
    assert senate.title == "United States Senator From Indiana, Class 1"

    house = by_key()
    assert house["IN-01"].candidates[0].name == "Frank J. Mrvan"
    assert house["IN-01"].candidates[0].winner is True
    assert house["IN-02"].key.level is Level.HOUSE
    assert house["IN-02"].total_votes == sum(x.votes for x in house["IN-02"].candidates)


def test_state_legislature_districts_are_zero_padded():
    sd = by_key("OffCatC_1018_A.json")
    hd = by_key("OffCatC_1039_A.json")
    assert "IN SD-02" in sd and sd["IN SD-02"].key.level is Level.STATE
    assert "IN HD-01" in hd and hd["IN HD-01"].title == "State Representative, District 001"


def test_governor_ticket_and_percentages():
    gov = {format_key(r.key): r for r in parse("OffCatC_1007_A.json")}["IN GOV"]
    assert gov.candidates[0].name.startswith("Mike Braun")
    assert gov.candidates[0].winner is True
    assert gov.candidates[0].pct == round(
        100.0 * gov.candidates[0].votes / gov.total_votes, 2)
    # WriteTime is the file's own stamp (certified build, not a live update clock).
    assert gov.source_updated_at == datetime(2025, 4, 17, 18, 31, 53, tzinfo=timezone.utc)


def test_category_index_selects_federal_and_state_only():
    cats = categories(load("statewideElectionsC_A.json"))
    assert ("1006", "US Senator") in cats
    assert ("1039", "State Representative") in cats
    assert len(cats) == 5


def test_race_key_mapping():
    assert format_key(race_key("United States Representative, Ninth District")) == "IN-09"
    assert format_key(race_key("State Senator, District 02")) == "IN SD-02"
    assert format_key(race_key("State Representative, District 100")) == "IN HD-100"
    assert format_key(race_key("Judge, Superior Court")) == "IN LOCAL judge-superior-court"


def test_empty_payload_is_not_an_error():
    assert parse_category({}, fetched_at=AT) == []
    assert parse_category({"Root": {"StatewideSummary": {"Race": []}}}, fetched_at=AT) == []
