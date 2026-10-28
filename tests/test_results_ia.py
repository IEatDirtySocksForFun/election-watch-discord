import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results import ia_sos
from electionwatch.sources.results.ia_sos import parse_ia, race_key

FIX = Path(__file__).parent / "fixtures" / "ia_sos" / "ia"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(r.key): r for r in results}


def parsed():
    return parse_ia(load("summary.json"), load("electionsettings.json"), fetched_at=AT)


def test_us_house_and_winner_flag():
    ia1 = by_key(parsed())["IA-01"]
    assert ia1.key.level is Level.HOUSE
    assert ia1.title == "United States Representative District 1"
    assert ia1.candidates[0].name == "Mariannette Miller-Meeks"
    assert ia1.candidates[0].party == "REP"
    assert ia1.candidates[0].votes == 206955
    assert ia1.candidates[0].pct == 49.98
    assert ia1.candidates[0].winner is True  # Clarity's W flag
    assert ia1.candidates[1].winner is False
    assert ia1.total_votes == 414078
    assert (ia1.precincts_reporting, ia1.precincts_total) == (20, 20)
    assert ia1.expected_vote_pct == 100.0


def test_state_legislature_keys():
    got = by_key(parsed())
    assert got["IA SD-02"].key.level is Level.STATE
    assert got["IA SD-02"].candidates[0].name == "Jeff Taylor"
    assert got["IA HD-01"].key.level is Level.STATE
    assert got["IA HD-01"].candidates[0].party == "DEM"


def test_local_races_keep_stable_slugs():
    got = by_key(parsed())
    assert "IA LOCAL constitutional-amendment-1" in got
    assert "IA LOCAL supreme-court-justice-david-may" in got
    assert "IA LOCAL agwsr-school-public-measure-iq" in got
    assert got["IA LOCAL constitutional-amendment-1"].candidates[0].name == "Yes"


def test_updated_at_comes_from_electionsettings():
    assert by_key(parsed())["IA-01"].source_updated_at == datetime(2024, 12, 16, 14, 19, 48, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "IA SEN"
    assert format_key(race_key("Governor")) == "IA GOV"
    assert format_key(race_key("Secretary of Agriculture")) == "IA AGCOM"
    assert format_key(race_key("Auditor of State")) == "IA AUD"
    assert format_key(race_key("United States Representative District 4")) == "IA-04"
    assert format_key(race_key("State Senator District 30")) == "IA SD-30"
    assert format_key(race_key("State Representative District 100")) == "IA HD-100"
    assert format_key(race_key("Constitutional Amendment 2")) == "IA LOCAL constitutional-amendment-2"


def test_empty_summary_is_not_an_error():
    assert parse_ia([], None, fetched_at=AT) == []
    assert parse_ia({"Contests": []}, None, fetched_at=AT) == []


def test_registered_in_the_results_registry():
    import electionwatch.sources.results  # noqa: F401  (import for side effects)

    assert ia_sos.IaSos.states == frozenset({"IA"})
    assert ia_sos.IaSos.scrape is False


def test_fetch_resolves_version_then_summary(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        path = request.url.path
        if path.endswith("/current_ver.txt"):
            return httpx.Response(200, text="356043")
        if path.endswith("/summary.json"):
            return httpx.Response(200, json=load("summary.json"))
        if path.endswith("/electionsettings.json"):
            return httpx.Response(200, json=load("electionsettings.json"))
        raise AssertionError(path)

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ia_sos.IaSos().fetch([parse("IA-01"), parse("IA GOV")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"IA-01"}
    assert got["IA-01"].source == "ia_sos"
    assert got["IA-01"].url == "https://results.enr.clarityelections.com/IA/122322/"


def test_fetch_discovers_unpinned_election_id(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2025-11-04")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path.endswith("/IA/elections.json"):
            return httpx.Response(200, json=load("elections.json"))
        if path.endswith("/IA/124196/current_ver.txt"):
            return httpx.Response(200, text="356043")
        if path.endswith("/summary.json"):
            return httpx.Response(200, json=load("summary.json"))
        return httpx.Response(404, text="")

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ia_sos.IaSos().fetch([parse("IA-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"IA-01"}
    assert got["IA-01"].url == "https://results.enr.clarityelections.com/IA/124196/"
    assert seen[0].endswith("/IA/elections.json")
