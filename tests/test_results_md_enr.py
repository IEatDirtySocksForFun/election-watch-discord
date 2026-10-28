"""Maryland's unofficial election-night pages (the ENR side of md_sbe)."""

from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results.md_sbe import enr_page_info, index_entries, parse_md_enr, race_key

FIX = Path(__file__).parent / "fixtures" / "md_sbe" / "MD"
AT = datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc)


def html(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_index_lists_offices_and_districts():
    entries = index_entries(html("2026_general_index.html"))
    keys = [format_key(race_key(entry["office"], entry["district"])) for entry in entries]
    assert "MD GOV" in keys and "MD COMP" in keys and "MD AG" in keys
    # Districts come from the sub-anchors; the parent "Representative in Congress"
    # anchor is not a contest of its own.
    assert {"MD-01", "MD-08", "MD SD-27", "MD HD-01A"} <= set(keys)
    assert "MD-00" not in keys
    assert "MD LOCAL question-1" in keys
    sd = [entry for entry in entries if entry["office"] == "State Senator"]
    assert sd and all(entry["district"] for entry in sd)


def test_governor_page_before_counting():
    overview = enr_page_info(html("2026_general_governor.html"))
    assert overview["title"] == "Governor / Lt. Governor"
    assert (overview["precincts_reporting"], overview["precincts_total"]) == (0, 1979)
    # "Last refreshed: 10/21/2026 11:01:50 PM" Eastern = 03:01:50 UTC on the 22nd.
    assert overview["updated_at"] == datetime(2026, 10, 22, 3, 1, 50, tzinfo=timezone.utc)

    gov = parse_md_enr(html("2026_general_governor.html"), fetched_at=AT, office="Governor / Lt. Governor", url="x")
    assert format_key(gov.key) == "MD GOV"
    assert gov.key.level is Level.GOVERNOR
    assert [(candidate.name, candidate.party) for candidate in gov.candidates] == [
        ("Wes Moore and Aruna Miller", "DEM"),
        ("Dan Cox and Rob Krop", "REP"),
        ("Andy Ellis and Owen Silverman Andrews", "GRN"),
        ("Cathy White and Cathy Permut", "WCP"),
        ("Other Write-Ins", None),
    ]
    # Every cell on the page still reads "NR": recorded as 0 votes, no percentage.
    assert all(candidate.votes == 0 and candidate.pct is None and not candidate.winner for candidate in gov.candidates)
    assert gov.precincts_reporting == 0 and gov.precincts_total == 1979
    assert gov.source_updated_at == overview["updated_at"]


def test_question_row_uses_the_th_cell():
    question = parse_md_enr(html("2026_general_question1.html"), fetched_at=AT, office="Question 1")
    assert format_key(question.key) == "MD LOCAL question-1"
    assert [candidate.name for candidate in question.candidates] == [
        "For the Constitutional Amendment",
        "Against the Constitutional Amendment",
    ]
    assert all(candidate.party is None and candidate.votes == 0 for candidate in question.candidates)


def test_primary_page_merges_tables_and_keeps_winner_flags():
    # Primary results are split into one table per party; the adapter is pointed at the
    # general election, so this page is used to exercise the multi-table path and the
    # "Winner Selected" marker.
    cd1 = parse_md_enr(
        html("2026_primary_congress_district1.html"), fetched_at=AT, office="Representative in Congress", district="1"
    )
    assert format_key(cd1.key) == "MD-01"
    assert cd1.title == "Representative in Congress, District 1"
    assert (cd1.precincts_reporting, cd1.precincts_total) == (309, 309)
    by_name = {candidate.name: candidate for candidate in cd1.candidates}
    assert by_name["Andy Harris"].votes == 54122
    assert by_name["Andy Harris"].party == "REP"
    assert by_name["Andy Harris"].winner is True
    assert by_name["Dan Schwartz"].winner is True
    assert by_name["Randi White"].winner is False
    # per-party percentages are dropped when several tables are merged
    assert all(candidate.pct is not None for candidate in cd1.candidates)
    assert round(sum(candidate.pct for candidate in cd1.candidates), 0) == 100


def test_delegates_district_page():
    hd = parse_md_enr(
        html("2026_general_house_delegates_d1.html"), fetched_at=AT, office="House of Delegates", district="1"
    )
    assert format_key(hd.key) == "MD HD-01"
    assert hd.key.level is Level.STATE
    assert hd.precincts_total == 25


def test_keys_line_up_with_the_certified_csv_vocabulary():
    # The ENR office names must produce the same keys the certified CSVs produce.
    assert format_key(race_key("Governor / Lt. Governor")) == format_key(race_key("Governor"))
    assert format_key(race_key("Representative in Congress", "1")) == format_key(parse("MD-01"))
    assert format_key(race_key("State Senator", "27")) == format_key(parse("MD SD-27"))
    # Delegate districts are lettered; md_sbe.race_key zero-pads the number for both the
    # ENR pages and the certified CSVs.
    assert format_key(race_key("House of Delegates", "1A")) == "MD HD-01A"
    assert format_key(race_key("Comptroller")) == format_key(parse("MD COMP"))
