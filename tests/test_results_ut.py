import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse as parse_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import ut_lgov
from electionwatch.sources.results.ut_lgov import export_url, parse_ut, race_key

FIX = Path(__file__).parent / "fixtures" / "ut_lgov" / "UT"
AT = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def payload():
    return json.loads((FIX / "export-general11052024.json").read_text())


def parse():
    return parse_ut(payload(), fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_keys_cover_federal_statewide_legislature():
    got = by_key(parse())
    assert got["UT SEN"].key.level is Level.SENATE
    assert got["UT-01"].key.level is Level.HOUSE
    assert got["UT GOV"].key.level is Level.GOVERNOR
    assert got["UT AG"].key.level is Level.GOVERNOR
    assert got["UT AUD"].key.level is Level.GOVERNOR
    assert got["UT SD-02"].key.level is Level.STATE
    assert got["UT HD-59"].key.level is Level.STATE
    # Non-statewide contests stay LOCAL with a stable slug.
    assert "UT LOCAL" in format_key(got["UT LOCAL constitutional-amendment-b"].key)


def test_party_names_normalised():
    got = by_key(parse())
    parties = {c.party for c in got["UT SEN"].candidates}
    assert {"REP", "DEM", "IAP"} <= parties
    # Measure choices have no party.
    assert all(c.party is None for c in got["UT LOCAL constitutional-amendment-b"].candidates)


def test_votes_and_titles():
    sen = by_key(parse())["UT SEN"]
    assert sen.title == "U.S. Senate"
    curtis = next(c for c in sen.candidates if c.name == "JOHN CURTIS")
    assert curtis.votes == 914700
    assert curtis.party == "REP"
    assert sen.total_votes == sum(c.votes for c in sen.candidates)
    gov = by_key(parse())["UT GOV"]
    assert gov.candidates[0].name.startswith("SPENCER J. COX")


def test_district_markers_are_dropped():
    assert format_key(race_key("State Senate 2 (Multi-County)")) == "UT SD-02"
    assert format_key(race_key("State House 59  (Multi-County)")) == "UT HD-59"
    assert format_key(race_key("U.S. House 3")) == "UT-03"
    assert format_key(race_key("U.S. President")) == "UT LOCAL us-president"
    assert format_key(race_key("State School Board 3 (Multi-County)")) == \
        "UT LOCAL state-school-board-3"


def test_export_slug_and_url():
    assert export_url("2024-11-05").endswith("/export-general11052024.json")
    assert export_url("2026-11-03").endswith("/export-general11032026.json")
    assert export_url("2022-11-08").endswith("/export-general11082022.json")


def test_parse_tolerates_junk():
    assert parse_ut(None, fetched_at=AT) == []
    assert parse_ut({}, fetched_at=AT) == []
    assert parse_ut({"results": {"ballotItems": [{"name": " "}]}}, fetched_at=AT) == []


def test_fetch_uses_configured_slug(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=(FIX / "export-general11052024.json").read_text())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await ut_lgov.UtLgov().fetch([parse_key("UT SEN"), parse_key("UT SD-02")], c)

    assert set(by_key(asyncio.run(go()))) == {"UT SEN", "UT SD-02"}
    assert seen == ["https://app.enhancedvoting.com/cdn/results/Utah/export-general11052024.json"]


def test_fetch_unavailable_before_export(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await ut_lgov.UtLgov().fetch([parse_key("UT SEN")], c)

    with pytest.raises(SourceUnavailable, match="not published yet"):
        asyncio.run(go())
