from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.il_sbe import (
    categories, election_label, parse_il, race_key,
)

FIX = Path(__file__).parent / "fixtures" / "il_sbe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def by_key(name="federal_statewide.html"):
    return {format_key(r.key): r for r in parse_il(load(name), fetched_at=AT)}


def test_statewide_offices():
    keys = by_key()
    senate = keys["IL SEN"]
    assert senate.key.level is Level.SENATE
    assert senate.candidates[0].name == "JULIANA STRATTON"
    assert senate.candidates[0].party == "DEM"
    assert senate.total_votes == sum(x.votes for x in senate.candidates)
    assert keys["IL GOV"].candidates[0].name == "JB PRITZKER"
    assert keys["IL AG"].key.level is Level.GOVERNOR
    assert keys["IL SOS"].key.office == "SOS"
    assert keys["IL COMP"].key.office == "COMP"
    assert keys["IL TREAS"].key.office == "TREAS"


def test_us_house_district_from_congress_heading():
    house = by_key()["IL-01"]
    assert house.key.level is Level.HOUSE
    assert house.title == "1ST CONGRESS"
    assert house.candidates[0].votes > 0


def test_state_legislature_pages():
    sd = by_key("state_senate.html")
    hd = by_key("state_house.html")
    assert sd["IL SD-02"].key.level is Level.STATE
    assert sd["IL SD-02"].title == "2ND SENATE"
    assert hd["IL HD-01"].key.office == "HD"
    assert hd["IL HD-01"].title == "1ST REPRESENTATIVE"


def test_percent_is_recomputed_not_the_primary_party_share():
    senate = by_key()["IL SEN"]
    assert senate.candidates[0].pct == round(
        100.0 * senate.candidates[0].votes / senate.total_votes, 2)
    assert sum(x.pct for x in senate.candidates) < 100.5   # only one party's field here


def test_election_label_and_category_links():
    html = load("federal_statewide.html")
    assert election_label(html) == "2026 GENERAL PRIMARY"
    urls = categories(html)
    assert len(urls) == 2                      # Senate (state Senate) + District All (state House)
    assert all("OfficeType=" in u for u in urls)


def test_race_key_mapping():
    assert format_key(race_key("UNITED STATES SENATOR")) == "IL SEN"
    assert format_key(race_key("17TH CONGRESS")) == "IL-17"
    assert format_key(race_key("59TH SENATE")) == "IL SD-59"
    assert format_key(race_key("118TH REPRESENTATIVE")) == "IL HD-118"
    assert format_key(race_key("SUPREME COURT - 1ST DISTRICT")) == \
        "IL LOCAL supreme-court-1st-district"


def test_empty_payload_is_not_an_error():
    assert parse_il("", fetched_at=AT) == []
