from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.md_sbe import parse_md, race_key

FIX = Path(__file__).parent / "fixtures" / "md_sbe" / "MD"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    text = (FIX / "2024_general_congressional_breakdown.csv").read_text(encoding="utf-8-sig")
    return parse_md(text, None, fetched_at=AT)


def by_key():
    return {format_key(race_result.key): race_result for race_result in parse()}


def test_statewide_totals_are_the_sum_of_the_district_columns():
    president = by_key()["MD LOCAL us-president"]
    assert president.key.level is Level.LOCAL
    harris = president.candidates[0]
    assert (harris.name, harris.party, harris.votes) == ("Kamala D. Harris and Tim Walz", "DEM", 1902577)
    assert harris.winner is True
    assert president.total_votes == 3018951
    assert president.candidates[1].votes == 1035550  # Trump / Vance


def test_senate_race():
    race_result = by_key()["MD SEN"]
    assert race_result.key.level is Level.SENATE
    alsobrooks, hogan = race_result.candidates[0], race_result.candidates[1]
    assert (alsobrooks.name, alsobrooks.votes, alsobrooks.winner) == ("Angela Alsobrooks", 1650912, True)
    assert (hogan.name, hogan.party, hogan.votes) == ("Larry Hogan", "REP", 1294344)
    assert race_result.total_votes == 3015623


def test_house_district_reads_only_its_own_column():
    keys = by_key()
    md01 = keys["MD-01"]
    assert md01.key.level is Level.HOUSE and md01.key.district == "01"
    assert [(candidate.name, candidate.votes, candidate.winner) for candidate in md01.candidates] == [
        ("Andy Harris", 246356, True),
        ("Blane H. Miller, III", 154985, False),
        ("Joshua W. O'Brien", 12664, False),
    ]
    assert md01.total_votes == 414005
    md08 = keys["MD-08"]
    assert md08.candidates[0].name == "Jamie Raskin"
    assert md08.total_votes == 379534


def test_other_party_label():
    race_result = by_key()["MD SEN"]
    assert [candidate.party for candidate in race_result.candidates][:4] == ["DEM", "REP", "LIB", "OTH"]  # "OTC" -> OTH


def test_both_files_together_do_not_double_count():
    """The two CSVs share the same state rows; the legislative pass must not add them again."""
    text = (FIX / "2024_general_congressional_breakdown.csv").read_text(encoding="utf-8-sig")
    single = {
        format_key(race_result.key): race_result.total_votes for race_result in parse_md(text, None, fetched_at=AT)
    }
    both = {format_key(race_result.key): race_result.total_votes for race_result in parse_md(text, text, fetched_at=AT)}
    assert both == single


def test_race_key_mapping():
    assert format_key(race_key("President - Vice Pres")) == "MD LOCAL us-president"
    assert format_key(race_key("U.S. Senator")) == "MD SEN"
    assert format_key(race_key("U.S. Congress", "01")) == "MD-01"
    assert format_key(race_key("Governor / Lt. Governor")) == "MD GOV"
    assert format_key(race_key("Attorney General")) == "MD AG"
    assert format_key(race_key("Comptroller")) == "MD COMP"
    assert format_key(race_key("State Senator", "01")) == "MD SD-01"
    assert format_key(race_key("House of Delegates", "1A")) == "MD HD-01A"
    assert format_key(race_key("MD Supreme Court")) == "MD LOCAL md-supreme-court"
