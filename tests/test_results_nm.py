from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.nm_sos import parse_nm, race_key

FIX = Path(__file__).parent / "fixtures" / "nm_sos" / "NM"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse(name):
    return parse_nm((FIX / name).read_text(encoding="utf-8"), fetched_at=AT)


def by_key():
    return {format_key(r.key): r for r in parse("2026_general_federal.html")}


def test_federal_contests_before_polls_close():
    got = by_key()
    assert set(got) == {"NM SEN", "NM-01", "NM-02", "NM-03"}
    senate = got["NM SEN"]
    assert senate.key.level is Level.SENATE
    assert [(c.name, c.party, c.votes) for c in senate.candidates] == [
        ("BEN R LUJAN", "DEM", 0), ("LARRY E MARKER", "REP", 0)]
    assert senate.precincts_reporting == 0          # "Precincts Fully: 0 / 2204"
    assert senate.precincts_total == 2204
    assert senate.expected_vote_pct is None
    assert senate.total_votes == 0


def test_house_districts_and_precinct_counts():
    got = by_key()
    assert got["NM-01"].key.level is Level.HOUSE
    assert got["NM-01"].candidates[0].name == "MELANIE ANN STANSBURY"
    assert got["NM-02"].precincts_total == 663
    assert got["NM-03"].candidates[0].party == "DEM"


def test_legislative_districts():
    got = {format_key(r.key): r for r in parse("2026_general_legislative.html")}
    assert set(got) == {"NM HD-01", "NM HD-02", "NM HD-03"}
    assert got["NM HD-01"].key.level is Level.STATE
    assert got["NM HD-01"].key.office == "HD"
    assert got["NM HD-01"].candidates[0].name == "ROD MONTOYA"
    assert got["NM HD-03"].precincts_total == 24


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "NM SEN"
    assert format_key(race_key("United States Representative DISTRICT 2")) == "NM-02"
    assert format_key(race_key("Governor")) == "NM GOV"
    assert format_key(race_key("Lieutenant Governor")) == "NM LTGOV"
    assert format_key(race_key("Commissioner of Public Lands")) == "NM LANDCOM"
    assert format_key(race_key("State Senator DISTRICT 5")) == "NM SD-05"
    assert format_key(race_key("State Representative DISTRICT 12")) == "NM HD-12"
    assert format_key(race_key("Public Education Commissioner DISTRICT 2")) == \
        "NM LOCAL public-education-commissioner-district-2"
