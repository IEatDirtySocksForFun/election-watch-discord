import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ga_sos import export_url, parse_ga, race_key

FIX = Path(__file__).parent / "fixtures" / "ga_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    doc = json.loads((FIX / "export-2020NovGen.json").read_text())
    return {format_key(r.key): r for r in parse_ga(doc, fetched_at=AT)}


def test_senate_and_special_are_distinct_races():
    keys = parse()
    assert keys["GA SEN"].title == "US Senate (Perdue)"
    assert keys["GA SEN"].key.special is False
    special = keys["GA SEN S"]
    assert special.key.special is True
    assert special.title == "US Senate (Loeffler) - Special"
    assert special.key.level is Level.SENATE


def test_candidate_names_drop_party_and_incumbent_suffixes():
    r = parse()["GA SEN"]
    perdue = next(c for c in r.candidates if c.name == "David A. Perdue")
    assert perdue.incumbent is True
    assert perdue.party == "REP"
    ossoff = next(c for c in r.candidates if c.name == "Jon Ossoff")
    assert ossoff.incumbent is False
    assert ossoff.party == "DEM"
    # Percentages are computed from the vote counts, since the export has none.
    assert ossoff.pct == round(100.0 * ossoff.votes / r.total_votes, 2)
    assert r.candidates == sorted(r.candidates, key=lambda c: c.votes, reverse=True)
    assert all(c.winner is False for c in r.candidates)


def test_house_and_state_legislature():
    keys = parse()
    assert keys["GA-01"].key.level is Level.HOUSE
    assert keys["GA SD-01"].key.level is Level.STATE
    assert keys["GA SD-05"].key.district == "05"  # bilingual "State Senate Dist 5/..."
    assert keys["GA HD-01"].key.level is Level.STATE


def test_ballot_measure_and_psc_become_local_races():
    keys = parse()
    amendment = keys["GA LOCAL constitutional-amendment-1"]
    assert amendment.key.level is Level.LOCAL
    assert {c.name for c in amendment.candidates} == {"Yes", "No"}
    assert {c.party for c in amendment.candidates} == {"NPA"}
    assert "GA LOCAL public-service-commission-district-4" in keys
    assert "GA LOCAL president-of-the-united-states" in keys


def test_export_url_uses_the_general_slug():
    assert export_url("2024-11-05").endswith("/export-2024NovGen.json")
    assert export_url("2026-11-03").endswith("/export-2026NovGen.json")
    assert export_url("2028-11-07").endswith("/export-2028NovGen.json")


def test_race_key_mapping():
    assert format_key(race_key("US Senate")) == "GA SEN"
    assert format_key(race_key("US Senate (Ossoff) - Special")) == "GA SEN S"
    assert format_key(race_key("US House District 14")) == "GA-14"
    assert format_key(race_key("US House of Representatives District 7")) == "GA-07"
    assert format_key(race_key("Governor")) == "GA GOV"
    assert format_key(race_key("Lieutenant Governor")) == "GA LTGOV"
    assert format_key(race_key("Attorney General")) == "GA AG"
    assert format_key(race_key("Commissioner of Labor")) == "GA LABCOM"
    assert format_key(race_key("State Senate District 56")) == "GA SD-56"
    assert format_key(race_key("State House District 121")) == "GA HD-121"
    assert format_key(race_key("State Court Judge - Cobb")) == "GA LOCAL state-court-judge-cobb"


def test_empty_payload_is_not_an_error():
    assert parse_ga({}, fetched_at=AT) == []
    assert parse_ga({"results": {}}, fetched_at=AT) == []
