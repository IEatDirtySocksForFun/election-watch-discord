import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.la_sos import parse_la, race_key, split_candidate

FIX = Path(__file__).parent / "fixtures" / "la_sos" / "LA"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def parse():
    return parse_la(load("2024_general_races.json"), load("2024_general_votes.json"), fetched_at=AT)


def by_key():
    return {format_key(r.key): r for r in parse()}


def test_presidential_electors_party_and_turnout():
    r = by_key()["LA LOCAL presidential-electors"]
    assert r.key.level is Level.LOCAL
    trump = r.candidates[0]
    assert trump.name == 'Donald J. Trump, "JD" Vance'
    assert trump.party == "REP"
    assert trump.votes == 1208505
    assert trump.winner is True  # Outcome "Elected"
    assert r.total_votes == 2021164  # VoterCountVoted, not the candidate sum
    assert r.registered_voters == 3046376
    assert r.precincts_reporting == r.precincts_total == 3805
    assert r.expected_vote_pct == 100.0


def test_us_house_district_and_winner():
    r = by_key()["LA-01"]
    assert r.key.level is Level.HOUSE
    assert r.key.district == "01"
    assert r.candidates[0].name == "Steve Scalise"
    assert r.candidates[0].votes == 238842
    assert r.candidates[0].winner is True
    assert "LA-03" in by_key()


def test_local_races_keep_a_stable_slug():
    keys = set(by_key())
    assert "LA LOCAL psc-district-2" in keys
    assert "LA LOCAL mayor-town-of-delcambre" in keys
    assert "LA LOCAL district-judge-21st-judicial-district-court-division-l" in keys
    assert (
        "LA LOCAL ca-no-1-act-no-408-2024-hb-300-provides-relative-to-federal-proceeds-from-alternative-energy-production"
        in keys
    )


def test_2026_feed_maps_senate_and_legislative_districts():
    parsed = parse_la(load("2026_general_races.json"), {}, fetched_at=AT)
    keys = {format_key(r.key): r for r in parsed}
    senate = keys["LA SEN"]
    assert senate.candidates[0].name == '"Jamie" Davis'
    assert senate.candidates[0].party == "DEM"
    assert senate.total_votes == 0  # no votes counted yet
    assert "LA-01" in keys
    assert "LA HD-92" in keys


def test_race_key_mapping():
    assert format_key(race_key("U. S. Senator")) == "LA SEN"
    assert format_key(race_key("U. S. Representative -- 6th Congressional District")) == "LA-06"
    assert format_key(race_key("State Senator -- 5th Senatorial District")) == "LA SD-05"
    assert format_key(race_key("State Representative --  92nd Representative District")) == "LA HD-92"
    assert format_key(race_key("Governor")) == "LA GOV"
    assert format_key(race_key("State Treasurer")) == "LA TREAS"
    assert format_key(race_key("BESE -- District 1")) == "LA LOCAL bese-district-1"


def test_candidate_party_suffixes():
    assert split_candidate("Bill Cassidy (REP)") == ("Bill Cassidy", "REP")
    assert split_candidate('Walter "Rocky" Beach (NOPTY)') == ('Walter "Rocky" Beach', "NPA")
    assert split_candidate("Jill Stein, Ajamu Baraka Green") == ("Jill Stein, Ajamu Baraka", "GRN")


def test_empty_payload_is_not_an_error():
    assert parse_la({}, {}, fetched_at=AT) == []
