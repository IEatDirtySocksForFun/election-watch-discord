import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import mi_county
from electionwatch.sources.results.clarity import parse_clarity
from electionwatch.sources.results.mi_county import (
    COUNTIES,
    feeds,
    iso_date,
    merge_counties,
    partial_title,
    pick_election,
    race_key,
    rekey,
)

FIX = Path(__file__).parent / "fixtures" / "mi_county"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load() -> list[dict]:
    return json.loads((FIX / "ottawa_elections.json").read_text())


def summary(rows) -> list[dict]:
    """Clarity summary.json shape (the same one tests/fixtures/clarity/ holds)."""
    return rows


def test_election_id_is_discovered_by_date():
    rows = load()
    assert pick_election(rows, "2026-11-03") == "127391"
    assert pick_election(rows, "2026-11-04") is None
    assert pick_election([], "2026-11-03") is None
    assert pick_election(None, "2026-11-03") is None


def test_iso_date_parses_the_vendor_stamp():
    assert iso_date("11/3/2026 12:00:00 AM") == "2026-11-03"
    assert iso_date("11/03/2026") == "2026-11-03"
    assert iso_date("") is None
    assert iso_date("2026-11-03") is None


def test_race_key_mapping():
    assert format_key(race_key("GOVERNOR AND LIEUTENANT GOVERNOR")) == "MI GOV"
    assert format_key(race_key("Governor / Lieutenant Governor")) == "MI GOV"
    assert format_key(race_key("Governor and  Lieutenant Governor")) == "MI GOV"
    assert format_key(race_key("UNITED STATES SENATOR")) == "MI SEN"
    assert format_key(race_key("United States Senator")) == "MI SEN"
    assert format_key(race_key("Secretary of State")) == "MI SOS"
    assert format_key(race_key("Representative in Congress 2nd District")) == "MI-02"
    assert format_key(race_key("REPRESENTATIVE IN CONGRESS DISTRICT 11")) == "MI-11"
    assert format_key(race_key("State Senator 30th District")) == "MI SD-30"
    assert format_key(race_key("Representative in State Legislature 43rd District")) == "MI HD-43"
    assert format_key(race_key("COUNTY CLERK")) == "MI LOCAL county-clerk"


def test_real_ottawa_summary_maps_to_canonical_races():
    summary = json.loads((FIX / "ottawa_2026_general_summary.json").read_text())
    settings = json.loads((FIX / "ottawa_2026_general_settings.json").read_text())
    results = rekey(parse_clarity(summary, settings, state="MI", fetched_at=AT))
    keys = {format_key(r.key): r for r in results}
    assert set(keys) == {"MI SEN", "MI-02", "MI-03", "MI-04"}
    assert keys["MI SEN"].candidates[0].name == "Abdul El-Sayed"
    assert keys["MI SEN"].candidates[0].party == "DEM"
    assert keys["MI SEN"].precincts_total == 127
    assert keys["MI-02"].precincts_total == 4
    assert keys["MI SEN"].source_updated_at is not None


def test_rekey_moves_the_governor_contest_off_local():
    parsed = parse_clarity(
        [
            {
                "C": "GOVERNOR / LIEUTENANT GOVERNOR",
                "CH": ["A. Person"],
                "P": ["DEM"],
                "V": [10],
                "PCT": [100.0],
                "TP": 4,
                "PR": 4,
            }
        ],
        state="MI",
        fetched_at=AT,
    )
    assert parsed[0].key.level is Level.LOCAL  # shared parser does not know this name
    assert rekey(parsed)[0].key.level is Level.GOVERNOR  # this adapter does
    assert format_key(rekey(parsed)[0].key) == "MI GOV"


def test_partial_title():
    assert partial_title("GOVERNOR", 1) == "GOVERNOR (partial: 1 county)"
    assert partial_title("GOVERNOR", 4) == "GOVERNOR (partial: 4 counties)"


def test_merge_sums_counties_and_recomputes_percentages():
    def one(county_votes):
        return parse_clarity(
            [
                {
                    "C": "GOVERNOR AND LIEUTENANT GOVERNOR",
                    "CH": ["A. Person", "B. Person"],
                    "P": ["DEM", "REP"],
                    "V": county_votes,
                    "PCT": [50.0, 50.0],
                    "TP": 10,
                    "PR": 5,
                }
            ],
            state="MI",
            fetched_at=AT,
        )

    by_county = {"Oakland": rekey(one([1000, 500])), "Ottawa": rekey(one([100, 400]))}
    merged = merge_counties(by_county, fetched_at=AT, source="mi_county")
    (gov,) = merged
    assert format_key(gov.key) == "MI GOV"
    assert gov.title == "GOVERNOR AND LIEUTENANT GOVERNOR (partial: 2 counties)"
    assert gov.source == "mi_county"
    assert {c.name: c.votes for c in gov.candidates} == {"A. Person": 1100, "B. Person": 900}
    assert {c.name: c.pct for c in gov.candidates} == {"A. Person": 55.0, "B. Person": 45.0}
    assert (gov.precincts_reporting, gov.precincts_total) == (10, 20)
    assert gov.expected_vote_pct == 50.0
    assert gov.url == "https://www.michigan.gov/sos/elections/election-results-and-data"


def test_merge_with_nothing_published_returns_empty():
    assert merge_counties({"Oakland": [], "Ottawa": []}, fetched_at=AT, source="mi_county") == []


def test_feed_configuration(monkeypatch):
    monkeypatch.delenv("MI_CLARITY_FEEDS", raising=False)
    assert [f["county"] for f in feeds("2026-11-03")] == list(COUNTIES)
    monkeypatch.setenv(
        "MI_CLARITY_FEEDS",
        json.dumps(
            [
                {"county": "Ottawa", "election_id": "127391"},
                {"county": "Kent", "election_id": "1", "date": "2024-11-05"},
            ]
        ),
    )
    assert feeds("2026-11-03") == [{"county": "Ottawa", "election_id": "127391"}]
    monkeypatch.setenv("MI_CLARITY_FEEDS", "{nope")
    with pytest.raises(SourceUnavailable):
        feeds("2026-11-03")


def test_fetch_merges_the_county_feeds(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.setenv("MI_CLARITY_FEEDS", json.dumps([{"county": "Ottawa"}, {"county": "Oakland"}]))

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("elections.json"):
            return httpx.Response(200, json=load())
        if path.endswith("current_ver.txt"):
            return httpx.Response(200, text="445566")
        if path.endswith("summary.json"):
            share = 1000 if "/Ottawa/" in path else 500
            return httpx.Response(
                200,
                json=summary(
                    [
                        {
                            "C": "UNITED STATES SENATOR",
                            "CH": ["A. Person", "B. Person"],
                            "P": ["DEM", "REP"],
                            "V": [share, share // 2],
                            "PCT": [66.7, 33.3],
                            "TP": 20,
                            "PR": 10,
                        },
                    ]
                ),
            )
        if path.endswith("electionsettings.json"):
            return httpx.Response(200, json={"websiteupdatedat": "10/26/2026 11:42:44 AM EDT"})
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await mi_county.MiCounty().fetch([parse("MI SEN"), parse("MI-07")], c)

    got = {format_key(r.key): r for r in asyncio.run(go())}
    assert set(got) == {"MI SEN"}
    sen = got["MI SEN"]
    assert sen.title == "UNITED STATES SENATOR (partial: 2 counties)"
    assert {c.name: c.votes for c in sen.candidates} == {"A. Person": 1500, "B. Person": 750}
    assert (sen.precincts_reporting, sen.precincts_total) == (20, 40)
    assert sen.source_updated_at == datetime(2026, 10, 26, 15, 42, 44, tzinfo=timezone.utc)


def test_fetch_is_unavailable_before_any_county_publishes(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
            return await mi_county.MiCounty().fetch([parse("MI SEN")], c)

    with pytest.raises(SourceUnavailable, match="no county feed published"):
        asyncio.run(go())


def test_other_states_are_ignored():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
            return await mi_county.MiCounty().fetch([parse("MN SEN")], c)

    assert asyncio.run(go()) == []
