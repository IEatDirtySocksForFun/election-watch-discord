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
FIX26 = Path(__file__).parent / "fixtures" / "ia_sos" / "ia2026"
FIX26LIVE = Path(__file__).parent / "fixtures" / "ia_sos" / "ia2026-live"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)
AT26 = datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def load26(name):
    return json.loads((FIX26 / name).read_text(encoding="utf-8"))


def load26live(name):
    return json.loads((FIX26LIVE / name).read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


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


def test_2026_general_parses_the_live_summary():
    # 2026 general (id 127077), fetched 2026-10-31: published pre-election with every
    # vote at 0, same contest layout as the 2024 count.
    got = by_key(parse_ia(load26("summary.json"), load26("electionsettings.json"), fetched_at=AT26))
    assert {"IA SEN", "IA GOV", "IA-01", "IA-04", "IA SOS", "IA SD-01", "IA HD-01"} <= set(got)
    assert got["IA GOV"].key.level is Level.GOVERNOR
    assert got["IA GOV"].title == "Governor and Lt. Governor"
    assert [candidate.name for candidate in got["IA GOV"].candidates][:2] == [
        "Zach Lahn and Derek Wulf",
        "Rob Sand and Dave Muhlbauer",
    ]
    assert got["IA SEN"].total_votes == 0
    assert (got["IA SEN"].precincts_reporting, got["IA SEN"].precincts_total) == (2, 99)


def test_2026_general_local_slugs_and_timestamp():
    got = by_key(parse_ia(load26("summary.json"), load26("electionsettings.json"), fetched_at=AT26))
    assert "IA LOCAL constitutional-amendment-1" in got
    assert "IA LOCAL agwsr-school-public-measure-ir" in got
    assert "IA LOCAL court-of-appeals-judge-sharon-soorholtz-greer" in got
    assert got["IA SEN"].source_updated_at == datetime(2026, 10, 30, 20, 7, 48, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "IA SEN"
    assert format_key(race_key("Governor")) == "IA GOV"
    assert format_key(race_key("Governor and Lt. Governor")) == "IA GOV"
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


def test_fetch_discovers_and_parses_the_2026_general(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request):
        path = request.url.path
        seen.append(path)
        if path.endswith("/IA/elections.json"):
            return httpx.Response(200, json=load("elections.json"))
        if path.endswith("/IA/127077/current_ver.txt"):
            return httpx.Response(200, text="382865")
        if path.endswith("/summary.json"):
            return httpx.Response(200, json=load26("summary.json"))
        if path.endswith("/electionsettings.json"):
            return httpx.Response(200, json=load26("electionsettings.json"))
        return httpx.Response(404, text="")

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await ia_sos.IaSos().fetch([parse("IA SEN"), parse("IA GOV"), parse("IA-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"IA SEN", "IA GOV", "IA-01"}
    assert got["IA SEN"].url == "https://results.enr.clarityelections.com/IA/127077/"
    assert seen[0].endswith("/IA/elections.json")


def test_2026_general_live_count_parses_votes():
    # ia2026-live, captured 2026-11-04 UTC while the count was under way.
    got = by_key(parse_ia(load26live("summary.json"), load26live("electionsettings.json"), fetched_at=AT26))
    sen = got["IA SEN"]
    assert [(candidate.name, candidate.party) for candidate in sen.candidates][:2] == [
        ("Josh Turek", "DEM"),
        ("Ashley Hinson", "REP"),
    ]
    assert sen.candidates[0].votes == 546784
    assert sen.total_votes == 1075662
    assert (sen.precincts_reporting, sen.precincts_total) == (70, 99)
    assert sen.source_updated_at == datetime(2026, 11, 4, 4, 7, 42, tzinfo=timezone.utc)
    assert got["IA GOV"].candidates[0].name == "Rob Sand and Dave Muhlbauer"
    assert got["IA-01"].candidates[0].votes == 139483
