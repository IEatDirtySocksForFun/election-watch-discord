from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.sd_sos import media_info, parse_sd, race_key

FIX = Path(__file__).parent / "fixtures" / "sd_sos" / "SD"
AT = datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc)
CSVS = (
    "2026_general_swr.csv",
    "2026_general_bq.csv",
    "2026_general_leg.csv",
    "2026_general_cty.csv",
    "2026_general_mun.csv",
)


def texts():
    return [(FIX / name).read_text(encoding="utf-8") for name in CSVS]


def media():
    return (FIX / "2026_general_media.html").read_text(encoding="utf-8")


def by_key():
    return {format_key(r.key): r for r in parse_sd(texts(), media(), fetched_at=AT)}


def test_media_page_header():
    info = media_info(media())
    assert info["date"] == "2026-11-03"
    assert info["election_type"] == "General"
    assert info["ballots_cast"] == 6441
    assert info["registered_voters"] == 622188
    # "10/27/2026 10:53 AM CT" -> 15:53 UTC
    assert info["updated_at"] == datetime(2026, 10, 27, 15, 53, tzinfo=timezone.utc)


def test_senate_and_at_large_house():
    got = by_key()
    senate = got["SD SEN"]
    assert senate.key.level is Level.SENATE
    assert [(c.name, c.party, c.votes) for c in senate.candidates] == [
        ("Mike Rounds", "REP", 2132),
        ("Brian L Bengs", "IND", 2099),
    ]
    assert senate.candidates[0].pct == 50.39  # 0-1 fraction in the feed
    assert senate.precincts_reporting == 122
    assert senate.precincts_total == 671
    assert senate.expected_vote_pct == 18.18
    assert senate.source_updated_at == datetime(2026, 10, 27, 15, 53, tzinfo=timezone.utc)
    assert senate.turnout_pct == 1.04  # 6441 / 622188

    house = got["SD-AL"]
    assert house.key.level is Level.HOUSE
    assert house.key.district == "AL"
    assert [c.name for c in house.candidates] == ["Marty Jackley", "Nicole 'Nikki' Gronli"]


def test_statewide_executive_offices():
    got = by_key()
    assert got["SD GOV"].title == "Governor and Lieutenant Governor"
    assert [c.name for c in got["SD GOV"].candidates] == [
        "Larry Rhoden and Tony Venhuizen",
        "Dan Ahlers and Steven McCleerey",
    ]
    assert [c.party for c in got["SD SOS"].candidates] == ["DEM", "LIB", "REP"]
    assert got["SD AG"].candidates[0].pct == 100.0
    assert got["SD AUD"].key.level is Level.GOVERNOR
    assert got["SD TREAS"].candidates[0].name == "Melissa Hull"
    assert got["SD LANDCOM"].key.office == "LANDCOM"
    # No shared code exists for the utilities commission: it stays an identifiable LOCAL race.
    assert "SD LOCAL public-utilities-commissioner" in got


def test_legislative_races_and_two_seat_house_districts():
    got = by_key()
    assert got["SD SD-01"].key.level is Level.STATE
    assert got["SD SD-01"].candidates[0].name == "Michael H. Rohl"
    assert got["SD SD-01"].precincts_reporting == 16
    assert got["SD SD-01"].precincts_total == 43
    # District 02 has reported nothing yet: no precinct figure is published as zero.
    assert got["SD SD-02"].precincts_reporting is None
    assert got["SD SD-02"].precincts_total == 12

    hd = got["SD HD-01"]
    assert hd.key.office == "HD"
    assert [c.name for c in hd.candidates] == ["Daniel Kjos", "Nick Fosness", "Courtney Gale Enfield"]
    assert hd.title == "State Representative (District 01)"


def test_ballot_measures_get_compact_slugs():
    got = by_key()
    i = got["SD LOCAL constitutional-amendment-i"]
    assert [c.name for c in i.candidates] == ["No", "Yes"]
    assert i.candidates[0].votes == 1669
    assert i.candidates[0].party is None
    assert got["SD LOCAL constitutional-amendment-l"].candidates[0].votes == 1706


def test_county_and_city_races_stay_distinct():
    got = by_key()
    codington = got["SD LOCAL county-auditor-codington"]
    grant = got["SD LOCAL county-auditor-grant"]
    assert codington.candidates[0].name == "Brenda Hanten"
    assert grant.candidates[0].votes == 15
    assert grant.precincts_reporting == 4 and grant.precincts_total == 17
    assert "SD LOCAL referendum-83" in got

    council = got["SD LOCAL city-council-member-brookings"]
    assert council.title == "City Council Member (Brookings)"
    assert council.candidates[0].name == "Lisa Hager"
    assert council.candidates[0].party == "NPA"


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "SD SEN"
    assert format_key(race_key("United States Representative")) == "SD-AL"
    assert format_key(race_key("Governor and Lieutenant Governor")) == "SD GOV"
    assert format_key(race_key("Secretary of State")) == "SD SOS"
    assert format_key(race_key("Attorney General")) == "SD AG"
    assert format_key(race_key("Commissioner of School and Public Lands")) == "SD LANDCOM"
    assert format_key(race_key("State Senator", "District 7")) == "SD SD-07"
    assert format_key(race_key("State Representative", "District 12")) == "SD HD-12"
    assert format_key(race_key("City Council Member", "Brookings")) == "SD LOCAL city-council-member-brookings"


def test_partial_feed_does_not_crash():
    # Only one group fetched: the parser reports what it has.
    got = parse_sd((FIX / "2026_general_swr.csv").read_text(encoding="utf-8"), fetched_at=AT)
    assert {format_key(r.key) for r in got} >= {"SD SEN", "SD-AL", "SD GOV"}
    assert got[0].source_updated_at is None  # no media page -> no timestamp
