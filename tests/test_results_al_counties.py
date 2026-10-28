"""al_counties: Madison County, Alabama (Clarity ENR) as a partial Alabama feed."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, al_counties

FIX = Path(__file__).parent / "fixtures" / "al_counties"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def parsed():
    return {
        format_key(r.key): r
        for r in al_counties.parse_al_county(
            load("madison_2022_summary.json"),
            load("madison_2022_electionsettings.json"),
            county="Madison",
            fetched_at=AT,
        )
    }


def test_keys_for_federal_statewide_and_legislative_contests():
    got = parsed()
    assert {"AL SEN", "AL-05", "AL GOV", "AL LTGOV", "AL SOS", "AL SD-02", "AL HD-10"} <= set(got)
    assert "AL LOCAL madison-madison-county-sheriff" in got
    assert "AL LOCAL madison-statewide-amendment-1" in got
    # Straight-party voting is not a race.
    assert not any("straight" in k.lower() for k in got)


def test_truncated_house_name_and_totals():
    got = parsed()
    sen = got["AL SEN"]
    assert sen.total_votes == 120500 - 345  # the aggregated WRITE-IN row is dropped
    assert {c.name.upper() for c in sen.candidates} >= {"KATIE BRITT", "WILL BOYD"}
    assert (sen.precincts_reporting, sen.precincts_total) == (76, 76)
    assert sen.source_updated_at is not None
    assert got["AL-05"].total_votes == 120275 - 246  # minus its WRITE-IN tally


def test_write_in_rows_are_dropped():
    ltgov = parsed()["AL LTGOV"]
    assert all(c.name.upper() != "WRITE-IN" for c in ltgov.candidates)
    assert ltgov.total_votes == sum(c.votes for c in ltgov.candidates)


def test_elections_listing_has_no_2026_general_yet():
    entries = _county.parse_elections(load("madison_elections.json"))
    assert _county.find_election(entries, "2022-11-08") == "116245"
    assert _county.find_election(entries, "2026-11-03") is None


def test_fetch_with_pinned_id_titles_one_county(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2022-11-08")
    monkeypatch.delenv("AL_COUNTY_FEEDS", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/AL/Madison/116245/")
        if request.url.path.endswith("current_ver.txt"):
            return httpx.Response(200, text="313343")
        if request.url.path.endswith("summary.json"):
            return httpx.Response(200, json=load("madison_2022_summary.json"))
        return httpx.Response(200, json=load("madison_2022_electionsettings.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await al_counties.AlCounties().fetch([parse("AL SEN"), parse("AL-05")], c)

    got = {format_key(r.key): r for r in asyncio.run(go())}
    assert set(got) == {"AL SEN", "AL-05"}
    assert got["AL SEN"].title == "UNITED STATES SENATOR (partial: 1 county)"


def test_unlisted_2026_general_is_unavailable(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.delenv("AL_COUNTY_FEEDS", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/AL/Madison/elections.json"
        return httpx.Response(200, json=load("madison_elections.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await al_counties.AlCounties().fetch([parse("AL SEN")], c)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


def test_override_from_env(monkeypatch):
    monkeypatch.setenv(
        "AL_COUNTY_FEEDS", json.dumps([{"county": "Madison", "election_id": "127999", "date": "2026-11-03"}])
    )
    assert al_counties.counties("2026-11-03") == [{"county": "Madison", "election_id": "127999"}]
    assert al_counties.counties("2024-11-05") == []
