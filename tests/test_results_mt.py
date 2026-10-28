from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.mt_sos import discover_eid, page_info, parse_mt, race_key

FIX = Path(__file__).parent / "fixtures" / "mt_sos" / "MT"
AT = datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc)


def parse(name):
    return parse_mt((FIX / name).read_text(encoding="utf-8"), fetched_at=AT)


def by_key(name):
    return {format_key(r.key): r for r in parse(name)}


def test_header_carries_date_turnout_and_timestamp():
    info = page_info((FIX / "2024_general_fed.html").read_text(encoding="utf-8"))
    assert info["date"] == "2024-11-05"
    assert info["ballots_cast"] == 612423
    assert info["registered_voters"] == 799849
    # "Results last updated: 12/19/2024 1:25:20 PM MT" -> 20:25:20 UTC
    assert info["updated_at"] == datetime(2024, 12, 19, 20, 25, 20, tzinfo=timezone.utc)


def test_senate_and_congressional_districts():
    got = by_key("2024_general_fed.html")
    assert set(got) == {"MT SEN", "MT-01", "MT-02"}
    senate = got["MT SEN"]
    assert senate.key.level is Level.SENATE
    assert [(c.name, c.party, c.votes) for c in senate.candidates] == [
        ("Tim Sheehy", "REP", 319682),
        ("Jon Tester", "DEM", 276305),
        ("Sid Daoud", "LIB", 7272),
        ("Robert Barb", "GRN", 4003),
    ]
    assert senate.precincts_reporting == 727 and senate.precincts_total == 727
    assert senate.turnout_pct == 76.57
    assert senate.source_updated_at == datetime(2024, 12, 19, 20, 25, 20, tzinfo=timezone.utc)
    assert senate.candidates[0].pct == 52.64
    assert got["MT-01"].key.level is Level.HOUSE
    assert got["MT-01"].precincts_total == 345
    assert got["MT-02"].candidates[0].name == "Troy Downing"


def test_statewide_executive_offices():
    got = by_key("2024_general_state.html")
    assert got["MT GOV"].candidates[0].name == "Greg Gianforte"
    assert got["MT SOS"].key.level is Level.GOVERNOR
    assert got["MT AG"].candidates[0].votes == 352682
    assert got["MT AUD"].candidates[0].name == "James Brown"
    assert got["MT SUPT"].key.office == "SUPT"
    # No shared code for the clerk of the supreme court: it stays an identifiable LOCAL race.
    assert "MT LOCAL clerk-of-the-supreme-court" in got


def test_legislative_districts():
    senate = by_key("2024_general_senate.html")
    assert set(senate) == {"MT SD-02", "MT SD-03"}
    assert senate["MT SD-02"].key.level is Level.STATE
    assert senate["MT SD-02"].candidates[0].name == "Dave Fern"
    assert senate["MT SD-03"].precincts_total == 11
    house = by_key("2024_general_house.html")
    assert set(house) == {"MT HD-01", "MT HD-02"}
    assert house["MT HD-01"].key.office == "HD"
    assert house["MT HD-01"].candidates[0].votes == 5541


def test_column_header_row_is_skipped():
    # Every contest starts with an empty header row; no candidate may be named "".
    for name in ("2024_general_fed.html", "2024_general_state.html", "2024_general_house.html"):
        for result in parse(name):
            assert all(c.name.strip() for c in result.candidates)
            assert result.candidates, result.key


def test_non_partisan_judges_and_retention_questions():
    scj = by_key("2024_general_supreme_court.html")
    judge = scj["MT LOCAL supreme-court-chief-justice"]
    assert judge.candidates[0].party == "NP"  # page prints "Non-Partisan"
    assert judge.precincts_reporting == 726  # 726 fully + 0 partially of 727

    court = by_key("2024_general_district_court.html")
    retention = court["MT LOCAL retain-judge-mike-menahan"]
    assert [c.name for c in retention.candidates] == ["Yes", "No"]
    assert retention.candidates[0].party is None
    assert "MT LOCAL district-court-judge-district-2-dept-1" in court


def test_ballot_issue_gets_compact_slug():
    got = by_key("2024_general_ballot_issues.html")
    ci = got["MT LOCAL ci-126"]
    assert [c.name for c in ci.candidates] == ["No", "Yes"]
    assert ci.title.startswith("CI-126 A CONSTITUTIONAL AMENDMENT")


def test_public_service_commission_districts_stay_distinct():
    got = by_key("2024_general_psc.html")
    assert "MT LOCAL public-service-commissioner-district-2" in got


def test_2026_general_shell_has_no_contests_yet():
    html = (FIX / "2026_general_shell.html").read_text(encoding="utf-8")
    info = page_info(html)
    assert info["date"] == "2026-11-03"
    assert info["ballots_cast"] == 0
    assert parse_mt(html, fetched_at=AT) == []


def test_eid_discovery_from_candidate_filing():
    html = (FIX / "2026_candidate_filing_elections.html").read_text(encoding="utf-8")
    assert discover_eid(html, "2026-11-03") == "450002987"
    assert discover_eid(html, "2026-06-02") == "450002928"
    assert discover_eid(html, "2024-11-05") is None


def test_race_key_mapping():
    assert format_key(race_key("PRESIDENT & VICE PRESIDENT")) == "MT LOCAL us-president"
    assert format_key(race_key("UNITED STATES SENATOR")) == "MT SEN"
    assert format_key(race_key("UNITED STATES REPRESENTATIVE 2ND CONGRESSIONAL")) == "MT-02"
    assert format_key(race_key("GOVERNOR & LT. GOVERNOR")) == "MT GOV"
    assert format_key(race_key("SECRETARY OF STATE")) == "MT SOS"
    assert format_key(race_key("STATE SUPERINTENDENT OF PUBLIC INSTRUCTION")) == "MT SUPT"
    assert format_key(race_key("STATE SENATOR DISTRICT 7")) == "MT SD-07"
    assert format_key(race_key("STATE REPRESENTATIVE DISTRICT 12")) == "MT HD-12"
    assert format_key(race_key("CI-126 A CONSTITUTIONAL AMENDMENT")) == "MT LOCAL ci-126"
    assert format_key(race_key("SUPREME COURT JUSTICE #3")) == "MT LOCAL supreme-court-justice-3"
