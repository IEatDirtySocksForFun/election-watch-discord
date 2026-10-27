"""nj_counties: New Jersey county Clarity ENR feeds merged into partial statewide results."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.racekeys import parse as race_key_parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, nj_counties

FIX = Path(__file__).parent / "fixtures" / "nj_counties"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def by_key(results):
    return {format_key(r.key): r for r in results}


def parsed(name, county, settings=None):
    return nj_counties.parse_nj_county(
        load(name), load(settings) if settings else None, county=county, fetched_at=AT)


def merged_2024():
    return _county.merge(
        parsed("mercer_2024_summary.json", "Mercer", "mercer_2024_settings.json")
        + parsed("cumberland_2024_summary.json", "Cumberland"),
        source="nj_counties", url=nj_counties.HUMAN)


def test_2024_senate_is_one_race_across_counties():
    got = by_key(merged_2024())
    assert set(got) >= {"NJ SEN", "NJ-02", "NJ-03", "NJ-12"}
    sen = got["NJ SEN"]
    assert sen.key.level is Level.SENATE
    assert sen.title == "U.S. Senate (partial: 2 counties)"
    cands = {c.name: c.votes for c in sen.candidates}
    assert cands["Andy Kim"] == 105685 + 25186
    assert cands["Curtis Bashaw"] == 46932 + 25441  # first county seen wins the display name
    assert sen.total_votes == 156581 + 52226


def test_account_senate_name_variant_maps_to_the_same_key():
    # Cumberland 2024 calls it "UNITED STATES SENATOR"; Mercer calls it "U.S. Senate".
    cumberland = by_key(parsed("cumberland_2024_summary.json", "Cumberland"))
    assert "NJ SEN" in cumberland
    assert cumberland["NJ SEN"].candidates[0].votes == 25441


def test_house_districts_are_parsed_from_three_naming_styles():
    got = by_key(merged_2024())
    assert got["NJ-12"].title == (
        "U.S. House of Representatives 12th Congressional District (partial: 1 counties)")
    assert got["NJ-03"].candidates[0].name == "Herb Conaway"
    # "HOUSE OF REPRESENTATIVES - 2ND CONGRESSIONAL" (Cumberland)
    assert got["NJ-02"].key.level is Level.HOUSE
    assert got["NJ-02"].candidates[0].name == "Jeff VAN DREW"


def test_president_is_a_shared_local_race_not_a_county_one():
    got = by_key(merged_2024())
    assert "NJ LOCAL president-vice-president" in got
    assert not any(k.startswith("NJ LOCAL mercer-president") for k in got)


def test_county_local_races_are_namespaced():
    got = by_key(merged_2024())
    assert "NJ LOCAL mercer-county-clerk" not in got  # Clerk is a Cumberland office
    assert got["NJ LOCAL cumberland-county-clerk"].candidates[0].votes == 27024
    assert "NJ LOCAL mercer-council-ewing-township" in got
    assert "NJ LOCAL mercer-board-of-education-ewing-township" in got


def test_2026_general_file_uses_the_county_listing_names():
    results = by_key(_county.merge(parsed("cumberland_2026_summary.json", "Cumberland",
                                        "cumberland_2026_settings.json"),
                                  source="nj_counties", url=nj_counties.HUMAN))
    assert results["NJ SEN"].candidates[0].name == "Cory BOOKER"
    assert results["NJ SEN"].total_votes == 0
    assert results["NJ SEN"].source_updated_at == datetime(2026, 10, 21, 17, 53, 6,
                                                          tzinfo=timezone.utc)
    # "United States House of Representatives" carries no district -> county-local, not NJ-xx.
    assert "NJ LOCAL cumberland-united-states-house-of-representatives" in results
    assert results["NJ LOCAL cumberland-sheriff"].key.level is Level.LOCAL
    assert results["NJ LOCAL cumberland-bridgeton-mayor"].title == "Bridgeton Mayor (partial: 1 counties)"


def test_placeholder_candidates_are_dropped():
    results = nj_counties.parse_nj_county([
        {"C": "United States Senator", "CH": ["Jane DOE", "NO NOMINATION MADE"],
         "P": ["DEM", ""], "V": [10, 0], "T": [10], "TP": 5, "PR": 1},
    ], None, county="Cumberland", fetched_at=AT)
    assert [c.name for c in results[0].candidates] == ["Jane DOE"]


def test_listing_finds_the_2026_general_and_skips_the_special_school_election():
    entries = _county.parse_elections(load("cumberland_elections.json"))
    assert len(entries) == 5
    assert _county.find_election(entries, "2026-11-03") == "127562"
    assert _county.find_election(entries, "2024-11-05") == "122757"
    assert _county.find_election(entries, "2025-12-09") == "125661"


def test_race_key_mapping_table():
    key = lambda name: format_key(nj_counties.race_key("essex", name))  # noqa: E731
    assert key("United States Senator") == "NJ SEN"
    assert key("House of Representatives CD 11") == "NJ-11"
    assert key("Governor") == "NJ GOV"
    assert key("State Senate District 3") == "NJ SD-03"
    assert key("General Assembly District 27") == "NJ HD-27"
    assert key("Sheriff") == "NJ LOCAL essex-sheriff"
    assert key("Newark Mayor") == "NJ LOCAL essex-newark-mayor"


def test_fetch_merges_pinned_counties_and_skips_a_dead_one(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    bodies = {
        ("Mercer", "122845"): "mercer_2024_summary.json",
        ("Cumberland", "122757"): "cumberland_2024_summary.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        key = (parts[1], parts[2])
        if key not in bodies:
            return httpx.Response(503)                       # the other counties are "down"
        if request.url.path.endswith("current_ver.txt"):
            return httpx.Response(200, text="355689")
        if request.url.path.endswith("summary.json"):
            return httpx.Response(200, json=json.loads((FIX / bodies[key]).read_text()))
        return httpx.Response(404)                           # no electionsettings.json

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await nj_counties.NjCounties().fetch(
                [race_key_parse("NJ SEN"), race_key_parse("NJ-02"), race_key_parse("NJ LOCAL cumberland-county-clerk")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"NJ SEN", "NJ-02", "NJ LOCAL cumberland-county-clerk"}
    assert got["NJ LOCAL cumberland-county-clerk"].source == "nj_counties"


def test_fetch_explains_missing_elections(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2022-11-08")
    monkeypatch.setenv("NJ_COUNTY_FEEDS", json.dumps([
        {"county": "Cumberland", "election_id": "", "date": "2022-11-08"}]))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])                  # county lists no elections

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await nj_counties.NjCounties().fetch([race_key_parse("NJ SEN")], c)

    with pytest.raises(SourceUnavailable) as err:
        asyncio.run(go())
    assert "NJ_COUNTY_FEEDS" in str(err.value)
