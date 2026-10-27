import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results.clarity import (
    ELECTIONS, feed_base, feeds, parse_clarity, parse_settings, race_key,
)

FIX = Path(__file__).parent / "fixtures" / "clarity"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_wv_primary_party_contests_are_skipped_and_nonpartisan_kept():
    results = parse_clarity(load("wv_2026_primary_summary.json"),
                            load("wv_2026_primary_settings.json"),
                            state="WV", fetched_at=AT)
    keys = by_key(results)
    assert not any(r.key.level in (Level.SENATE, Level.HOUSE, Level.STATE) for r in results)
    assert "WV LOCAL non-partisan-election-of-judge-of-the-intermediate-court-of-appeals" in keys
    park = keys["WV LOCAL greater-huntington-park-and-recreation-district-supervisors-district-1"]
    assert park.candidates[0].name == "GORDON D. JONES"
    assert park.candidates[0].votes == 8816
    assert park.total_votes == 8816


def test_wv_settings_gives_updated_at_and_precincts():
    results = by_key(parse_clarity(load("wv_2026_primary_summary.json"),
                                   load("wv_2026_primary_settings.json"),
                                   state="WV", fetched_at=AT))
    sup = results["WV LOCAL non-partisan-election-of-justice-of-the-supreme-court-of-appeals-division-1-unexpired-6-year-term"]
    assert (sup.precincts_reporting, sup.precincts_total) == (51, 55)
    assert sup.expected_vote_pct == 92.73
    assert sup.source_updated_at == datetime(2026, 6, 15, 14, 43, 55, tzinfo=timezone.utc)
    assert sup.candidates[0].name == "H.L. 'KIRK' KIRKPATRICK"
    assert sup.candidates[0].pct == 31.04


def test_illinois_general_names_map_to_canonical_races():
    keys = by_key(parse_clarity(load("il_mclean_2026_general_summary.json"),
                                state="IL", fetched_at=AT))
    assert keys["IL SEN"].title == "U.S. SENATOR"
    assert keys["IL-16"].key.level is Level.HOUSE
    assert keys["IL GOV"].title == "GOV. AND LT. GOV"
    assert keys["IL AG"].title == "ATT. GEN"
    assert keys["IL SOS"].title == "SEC. OF STATE"
    assert keys["IL COMP"].title == "COMPTROLLER"
    assert keys["IL TREAS"].title == "TREASURER"
    assert keys["IL SD-44"].key.level is Level.STATE
    assert keys["IL HD-87"].key.level is Level.STATE
    assert keys["IL LOCAL county-clerk"].key.level is Level.LOCAL
    assert "IL LOCAL retention-james-a-knecht" in keys
    # Sample upload has zero votes; percentages are 0 rather than missing.
    assert keys["IL SEN"].candidates[0].pct == 0.0


def test_dict_wrapped_summary_and_winner_flag():
    summary = {"Contests": [{
        "C": "U.S. SENATOR", "CH": ["A. Person", "B. Person"], "P": ["DEM", "REP"],
        "V": [120, 80], "PCT": [60.0, 40.0], "W": [1, 0], "TP": 10, "PR": 5,
    }]}
    r = parse_clarity(summary, state="NC", fetched_at=AT)[0]
    assert r.key.level is Level.SENATE
    assert [c.winner for c in r.candidates] == [True, False]
    assert (r.precincts_reporting, r.precincts_total) == (5, 10)


def test_primary_contests_are_not_merged():
    summary = [{"C": "GOVERNOR - REP", "CH": ["R1"], "V": [10], "P": ["REP"]},
               {"C": "GOVERNOR - DEM", "CH": ["D1"], "V": [20], "P": ["DEM"]}]
    assert parse_clarity(summary, state="WV", fetched_at=AT) == []


def test_race_key_mapping():
    assert format_key(race_key("WV", "U.S. SENATOR")) == "WV SEN"
    assert format_key(race_key("WV", "U.S. HOUSE OF REPRESENTATIVES, 1st Congressional District")) == "WV-01"
    assert format_key(race_key("WV", "REP. IN CONGRESS D16")) == "WV-16"
    assert format_key(race_key("IL", "GOVERNOR")) == "IL GOV"
    assert format_key(race_key("IL", "ATTORNEY GENERAL")) == "IL AG"
    assert format_key(race_key("IL", "STATE SENATOR, 1st Senatorial District")) == "IL SD-01"
    assert format_key(race_key("IL", "STATE SENATOR D44")) == "IL SD-44"
    assert format_key(race_key("IL", "REP. IN THE G.A. D87")) == "IL HD-87"
    assert format_key(race_key("IL", "HOUSE OF DELEGATES, 1st District")) == "IL HD-01"
    assert format_key(race_key("IL", "HEYWORTH SCHOOL DIST 4 - BOARD OF ED. ELECTIONS")) == \
        "IL LOCAL heyworth-school-dist-4-board-of-ed-elections"


def test_settings_timestamp_ignores_zone_suffix():
    # 10:43:55 EDT == 14:43:55 UTC
    assert parse_settings({"websiteupdatedat": "6/15/2026 10:43:55 AM EDT"}) == \
        datetime(2026, 6, 15, 14, 43, 55, tzinfo=timezone.utc)
    assert parse_settings({}) is None
    assert parse_settings(None) is None


def test_feed_configuration(monkeypatch):
    monkeypatch.delenv("CLARITY_FEEDS", raising=False)
    assert feeds("2026-11-03") == ELECTIONS["2026-11-03"]
    assert feed_base(feeds("2026-11-03")[0]) == \
        "https://results.enr.clarityelections.com/IL/McLean/127349"
    assert feed_base({"state": "WV", "election_id": "126209"}) == \
        "https://results.enr.clarityelections.com/WV/126209"

    monkeypatch.setenv("CLARITY_FEEDS", json.dumps([
        {"state": "WV", "election_id": "126209", "date": "2026-11-03"},
        {"state": "IA", "election_id": "1", "date": "2024-11-05"},
    ]))
    assert [f["state"] for f in feeds("2026-11-03")] == ["WV"]
    with pytest.raises(SourceUnavailable):
        monkeypatch.setenv("CLARITY_FEEDS", "{not json")
        feeds("2026-11-03")
    with pytest.raises(SourceUnavailable):
        monkeypatch.setenv("CLARITY_FEEDS", '[{"state": "WV"}]')
        feeds("2026-11-03")
