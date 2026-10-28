import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results import co_sos
from electionwatch.sources.results.co_sos import parse_co, race_key

FIX = Path(__file__).parent / "fixtures" / "co_sos" / "co"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def parsed():
    return parse_co(load("summary.json"), load("electionsettings.json"), fetched_at=AT)


def test_us_house_uses_colorado_phrasing():
    got = by_key(parsed())
    assert got["CO-01"].title == "Representative to the 119th United States Congress - District 1"
    assert got["CO-01"].candidates[0].name == "Diana DeGette"
    assert got["CO-01"].candidates[0].party == "DEM"
    assert got["CO-01"].candidates[0].votes == 264606
    assert got["CO-01"].candidates[0].pct == 76.56
    assert got["CO-01"].precincts_reporting == got["CO-01"].precincts_total == 3
    assert got["CO-01"].expected_vote_pct == 100.0
    assert got["CO-01"].key.level is Level.HOUSE


def test_expected_vote_pct_from_precincts():
    co8 = by_key(parsed())["CO-08"]
    assert (co8.precincts_reporting, co8.precincts_total) == (2, 3)
    assert co8.expected_vote_pct == 66.67
    assert co8.candidates[0].name == "Gabe Evans"


def test_state_legislature_keys():
    got = by_key(parsed())
    assert got["CO SD-02"].key.level is Level.STATE
    assert got["CO SD-02"].candidates[0].name == "Lisa Frizell"
    assert got["CO HD-01"].key.level is Level.STATE
    assert got["CO HD-01"].candidates[0].party == "DEM"


def test_colorado_specific_local_races():
    got = by_key(parsed())
    assert "CO LOCAL regent-of-the-university-of-colorado-at-large" in got
    assert "CO LOCAL district-attorney-1st-judicial-district" in got
    assert "CO LOCAL amendment-g-constitutional" in got
    assert "CO LOCAL proposition-127-statutory" in got
    assert got["CO LOCAL proposition-127-statutory"].title == "Proposition 127 (STATUTORY)"


def test_updated_at_comes_from_electionsettings():
    stamp = by_key(parsed())["CO-01"].source_updated_at
    assert stamp == datetime(2025, 11, 25, 20, 46, 27, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("Governor")) == "CO GOV"
    assert format_key(race_key("Secretary of State")) == "CO SOS"
    assert format_key(race_key("State Treasurer")) == "CO TREAS"
    assert format_key(race_key("Attorney General")) == "CO AG"
    assert format_key(race_key("Representative to the 120th United States Congress - District 3")) == "CO-03"
    assert format_key(race_key("State Senator - District 35")) == "CO SD-35"
    assert format_key(race_key("State Representative - District 65")) == "CO HD-65"
    assert format_key(race_key("Amendment 79 (CONSTITUTIONAL)")) == "CO LOCAL amendment-79-constitutional"


def test_empty_summary_is_not_an_error():
    assert parse_co([], None, fetched_at=AT) == []
    assert parse_co({"Contests": []}, None, fetched_at=AT) == []


def test_fetch_resolves_version_then_summary(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/current_ver.txt"):
            return httpx.Response(200, text="367167")
        if path.endswith("/summary.json"):
            return httpx.Response(200, json=load("summary.json"))
        if path.endswith("/electionsettings.json"):
            return httpx.Response(200, json=load("electionsettings.json"))
        raise AssertionError(path)

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await co_sos.CoSos().fetch([parse("CO GOV"), parse("CO-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"CO-01"}  # no 2024 governor race in the feed
    assert got["CO-01"].source == "co_sos"
    assert got["CO-01"].url == "https://results.enr.clarityelections.com/CO/122598/"
    assert seen[0].endswith("/CO/122598/current_ver.txt")


def test_fetch_discovers_unpinned_election_id(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2025-11-04")

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/CO/elections.json"):
            return httpx.Response(200, json=load("elections.json"))
        if path.endswith("/CO/124409/current_ver.txt"):
            return httpx.Response(200, text="367167")
        if path.endswith("/summary.json"):
            return httpx.Response(200, json=load("summary.json"))
        return httpx.Response(404, text="")

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await co_sos.CoSos().fetch([parse("CO-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"CO-01"}
    assert got["CO-01"].url == "https://results.enr.clarityelections.com/CO/124409/"
