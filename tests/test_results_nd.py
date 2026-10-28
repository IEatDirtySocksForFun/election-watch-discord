from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.nd_sos import parse_nd, race_key

FIX = Path(__file__).parent / "fixtures" / "nd_sos" / "ND"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse(name):
    return parse_nd((FIX / name).read_text(encoding="utf-8"), fetched_at=AT)


def by_key():
    return {format_key(race_result.key): race_result for race_result in parse("2024_general_statewide.html")}


def test_senate_candidates_parties_and_winner():
    race_result = by_key()["ND SEN"]
    assert race_result.key.level is Level.SENATE
    crm, chris, writein = race_result.candidates
    assert (crm.name, crm.party, crm.votes, crm.pct) == ("Kevin Cramer", "REP", 241569, 66.31)
    assert crm.winner is True
    assert (chris.name, chris.party, chris.votes) == ("Katrina Christiansen", "DEM", 121602)
    assert chris.winner is False
    assert (writein.name, writein.party) == ("Write-in", "NPA")
    assert race_result.total_votes == 364327
    assert race_result.precincts_reporting == race_result.precincts_total == 385
    assert race_result.expected_vote_pct == 100.0


def test_at_large_house_and_governor():
    keys = by_key()
    house = keys["ND-AL"]
    assert house.key.level is Level.HOUSE and house.key.district == "AL"
    assert house.candidates[0].name == "Julie Fedorchak"
    gov = keys["ND GOV"]
    assert gov.key.level is Level.GOVERNOR
    assert gov.candidates[0].votes == 247056
    assert gov.candidates[2].party == "IND"  # "Independent Nomination"


def test_ballot_measure_is_local_with_yes_no():
    race_result = by_key()["ND LOCAL related-to-the-legalization-of-cannabis-and-related-regulation"]
    assert race_result.key.level is Level.LOCAL
    no, yes = race_result.candidates
    assert (no.name, no.votes, no.winner) == ("No", 190548, True)
    assert (yes.name, yes.votes, yes.winner) == ("Yes", 172174, False)
    assert race_result.total_votes == 362722


def test_legislative_districts_including_lettered_house_seat():
    got = {format_key(race_result.key): race_result for race_result in parse("2024_general_legislative.html")}
    senate = got["ND SD-02"]
    assert senate.key.level is Level.STATE and senate.key.office == "SD"
    assert senate.candidates[0].name == "Mark Enget"
    assert senate.precincts_reporting == senate.precincts_total == 22
    house = got["ND HD-04A"]  # "State Representative District 04a"
    assert house.key.office == "HD" and house.key.district == "04A"
    assert house.candidates[0].votes == 1627


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "ND SEN"
    assert format_key(race_key("Representative in Congress")) == "ND-AL"
    assert format_key(race_key("Governor and Lt. Governor")) == "ND GOV"
    assert format_key(race_key("Superintendent of Public Instruction")) == "ND SUPT"
    assert format_key(race_key("State Treasurer")) == "ND TREAS"
    assert format_key(race_key("Public Service Commissioner")) == "ND LOCAL public-service-commissioner"
    assert format_key(race_key("State Senator Unexpired 2-Year Term District 9")) == "ND SD-09 S"
    assert format_key(race_key("State Representative District 4B")) == "ND HD-04B"
