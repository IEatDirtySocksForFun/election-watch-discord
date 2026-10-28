"""Shared poll-parsing helpers (dates, samples, percentages, party markers)."""

from datetime import date

from selectolax.parser import HTMLParser

from electionwatch.sources.polls._common import (
    clean_name,
    node_text,
    parse_date_range,
    parse_percent,
    parse_sample,
    split_party_marker,
    state_code,
)


def test_parse_date_range_month_day_range():
    assert parse_date_range("February 22\u201323, 2026") == (date(2026, 2, 22), date(2026, 2, 23))
    assert parse_date_range("October 20\u201321, 2026") == (date(2026, 10, 20), date(2026, 10, 21))


def test_parse_date_range_across_months_and_years():
    assert parse_date_range("April 22 \u2013 May 6, 2026") == (date(2026, 4, 22), date(2026, 5, 6))
    assert parse_date_range("September 19 - October 1, 2025") == (date(2025, 9, 19), date(2025, 10, 1))
    assert parse_date_range("December 28 \u2013 January 3, 2026") == (date(2025, 12, 28), date(2026, 1, 3))
    assert parse_date_range("Sept 28 \u2013 Oct 2, 2026") == (date(2026, 9, 28), date(2026, 10, 2))


def test_parse_date_range_single_day_and_numeric():
    assert parse_date_range("October 6, 2026") == (date(2026, 10, 6), date(2026, 10, 6))
    assert parse_date_range("10/27/2026") == (date(2026, 10, 27), date(2026, 10, 27))
    assert parse_date_range("2026-10-27") == (date(2026, 10, 27), date(2026, 10, 27))
    assert parse_date_range("through October 21, 2026") == (date(2026, 10, 21), date(2026, 10, 21))


def test_parse_date_range_rejects_junk():
    assert parse_date_range("") == (None, None)
    assert parse_date_range("fieldwork pending") == (None, None)
    assert parse_date_range("February 30, 2026") == (None, None)


def test_parse_sample():
    assert parse_sample("600 (LV)") == (600, "LV")
    assert parse_sample("1,449 LV \u00b7 Margin of error \u00b16.6%") == (1449, "LV")
    assert parse_sample("1,032 (RV)") == (1032, "RV")
    assert parse_sample("1,189 (V)") == (1189, None)  # no LV/RV/A claim in the source
    assert parse_sample("\u2013 (LV)") == (None, "LV")
    assert parse_sample("") == (None, None)


def test_parse_percent():
    assert parse_percent("38%") == 38.0
    assert parse_percent("'''38%'''") == 38.0
    assert parse_percent("44.3") == 44.3
    assert parse_percent("\u2013") is None
    assert parse_percent("<1%") is None  # no honest number to show
    assert parse_percent("138%") is None
    assert parse_percent("") is None


def test_split_party_marker():
    assert split_party_marker("Harper Polling (R)") == ("Harper Polling", "REP")
    assert split_party_marker("Change Research (D)[3]") == ("Change Research", "DEM")
    assert split_party_marker("Ken Paxton (R)") == ("Ken Paxton", "REP")
    assert split_party_marker("Don Brown (author, born 1960)") == (
        "Don Brown (author, born 1960)",
        None,
    )
    assert split_party_marker("SurveyUSA") == ("SurveyUSA", None)


def test_state_code_and_clean_name():
    assert state_code("North Carolina") == "NC"
    assert state_code("north carolina") == "NC"
    assert state_code("Narnia") is None
    assert clean_name("Collins *") == "Collins"
    assert clean_name("Don Brown[125]") == "Don Brown"


def test_node_text_keeps_spaces_across_br():
    tree = HTMLParser("<table><tr><th>Ken<br>Paxton (R)</th></tr></table>")
    assert node_text(tree.css_first("th")) == "Ken Paxton (R)"


def test_registry_flags_match_the_scraper_rules():
    from electionwatch.sources.base import REGISTRY
    from electionwatch.sources.polls import two70win, votehub, wikipedia  # noqa: F401

    polls = REGISTRY["polls"]
    assert polls["votehub"].enabled_by_default is True and polls["votehub"].scrape is False
    for name in ("270towin", "wikipedia"):
        assert polls[name].scrape is True
        assert polls[name].enabled_by_default is False
    assert all(cls.kind == "polls" for cls in polls.values())
