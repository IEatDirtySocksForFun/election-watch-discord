"""tn_counties: Shelby County's Enhanced Voting export, keyed to Tennessee races."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, _ev_county, tn_counties

FIX = Path(__file__).parent / "fixtures" / "tn_counties"
AT = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def shelby_2024():
    return by_key(tn_counties.parse_tn_county(load("shelby_2024_export.json"), county="Shelby", fetched_at=AT))


def test_president_and_senate_are_tennessee_races():
    got = shelby_2024()
    pres = got["TN LOCAL president-vice-president"]
    assert pres.title == "United States President and Vice President"
    assert pres.total_votes == 328159
    assert pres.precincts_reporting == 142 and pres.precincts_total == 142
    cands = {candidate.name: candidate for candidate in pres.candidates}
    assert cands["Kamala D. Harris/Tim Walz"].votes == 201759
    assert cands["Donald J. Trump/JD Vance"].votes == 118917
    assert cands["Donald J. Trump/JD Vance"].party == "REP"

    sen = got["TN SEN"]
    assert sen.key.level is Level.SENATE
    assert sen.total_votes == 319479
    assert {candidate.name: candidate.votes for candidate in sen.candidates}["Marsha Blackburn"] == 120568


def test_repeated_party_prefix_is_stripped():
    names = [candidate.name for candidate in shelby_2024()["TN SEN"].candidates]
    assert "Marsha Blackburn" in names
    assert not any(name.startswith(("REP ", "DEM ", "IND ")) for name in names)


def test_congressional_and_state_legislative_districts():
    got = shelby_2024()
    assert got["TN-08"].key.district == "08"  # Memphis splits 8 and 9
    assert got["TN-09"].candidates[0].name == "Steve Cohen"
    assert got["TN SD-30"].key.district == "30"
    assert got["TN HD-83"].key.district == "83"
    assert got["TN-09"].title == "US House of Representatives District 9"


def test_municipal_contests_are_namespaced_and_deduplicated():
    got = shelby_2024()
    # "Bartlett Alderman - Position 4 Bartlett": the trailing repeat is dropped.
    assert "TN LOCAL shelby-bartlett-alderman-position-4" in got
    assert "TN LOCAL shelby-collierville-mayor" in got
    mayor = got["TN LOCAL shelby-collierville-mayor"]
    assert mayor.title == "Collierville Mayor Collierville"  # title kept verbatim
    assert [candidate.party for candidate in mayor.candidates] == ["NPA", "NPA", "NPA"]


def test_unrecognised_names_still_get_a_key():
    key = tn_counties.race_key("shelby", "Memphis City Court Clerk")
    assert format_key(key) == "TN LOCAL shelby-memphis-city-court-clerk"
    assert tn_counties.race_key("shelby", "GOVERNOR").office == "GOV"
    assert tn_counties.race_key("shelby", "Tennessee House of Representatives District 96").district == "96"


def test_parse_drops_empty_and_malformed_documents():
    assert (
        _ev_county.parse_items(
            {"results": {"ballotItems": []}},
            key_fn=lambda name: tn_counties.race_key("shelby", name),
            source="tn_counties",
            url="u",
            fetched_at=AT,
        )
        == []
    )
    assert (
        _ev_county.parse_items(
            {"results": {"ballotItems": [{"name": "United States Senate", "ballotOptions": []}]}},
            key_fn=lambda name: tn_counties.race_key("shelby", name),
            source="tn_counties",
            url="u",
            fetched_at=AT,
        )
        == []
    )
    assert tn_counties.parse_tn_county(None, county="Shelby", fetched_at=AT) == []


def test_feed_table_and_override(monkeypatch):
    monkeypatch.delenv("TN_COUNTY_FEEDS", raising=False)
    assert tn_counties.feeds("2024-11-05") == [{"county": "Shelby", "slug": "General11052024"}]
    monkeypatch.setenv(
        "TN_COUNTY_FEEDS",
        json.dumps(
            [
                {"county": "Shelby", "slug": "General11032026", "date": "2026-11-03"},
                {"county": "Davidson", "slug": "General11052024", "date": "2024-11-05"},
            ]
        ),
    )
    assert tn_counties.feeds("2026-11-03") == [{"county": "Shelby", "slug": "General11032026"}]
    with pytest.raises(SourceUnavailable):
        monkeypatch.setenv("TN_COUNTY_FEEDS", "{oops")
        tn_counties.feeds("2026-11-03")
    with pytest.raises(SourceUnavailable):
        monkeypatch.setenv("TN_COUNTY_FEEDS", json.dumps([{"county": "Shelby"}]))
        tn_counties.feeds("2026-11-03")


def test_fetch_reads_the_county_export_and_marks_it_partial(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    monkeypatch.delenv("TN_COUNTY_FEEDS", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ("/cdn/results/shelby-county-tn/export-General11052024.json")
        return httpx.Response(200, json=load("shelby_2024_export.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await tn_counties.TnCounties().fetch([parse("TN SEN"), parse("TN-09"), parse("TN GOV")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"TN SEN", "TN-09"}
    assert got["TN SEN"].source == "tn_counties"
    assert got["TN SEN"].title == "United States Senate (partial: 1 county)"
    assert got["TN SEN"].total_votes == 319479


def test_fetch_raises_when_the_export_is_not_published(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.delenv("TN_COUNTY_FEEDS", raising=False)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
            await tn_counties.TnCounties().fetch([parse("TN SEN")], client)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


def test_other_states_are_ignored():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            return await tn_counties.TnCounties().fetch([parse("TX SEN")], client)

    assert asyncio.run(go()) == []
    assert tn_counties.TnCounties().states == frozenset({"TN"})


def test_merge_helper_is_the_shared_county_one():
    # The raw `_county.merge` (shared with tx/nj/mi) always pluralises ...
    merged = _county.merge(
        tn_counties.parse_tn_county(load("shelby_2024_export.json"), county="Shelby", fetched_at=AT),
        source="tn_counties",
        url=tn_counties.HUMAN,
    )
    titles = [race_result.title for race_result in merged if race_result.key.office == "SEN"]
    assert titles == ["United States Senate (partial: 1 counties)"]
    # ... and the adapter rewrites it for its single configured county (see tests above).
