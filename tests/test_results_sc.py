import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse as parse_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import sc_sec
from electionwatch.sources.results.sc_sec import parse_sc, race_key

FIX = Path(__file__).parent / "fixtures" / "sc_sec" / "SC"
BASE = "https://results.enr.clarityelections.com/SC"
AT = datetime(2026, 10, 27, 15, 30, tzinfo=timezone.utc)


def parse():
    return parse_sc(json.loads((FIX / "2024_general_summary.json").read_text()),
                    fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_federal_state_and_local_keys():
    got = by_key(parse())
    assert got["SC-01"].key.level is Level.HOUSE
    assert got["SC SD-01"].key.level is Level.STATE
    assert got["SC SD-19"].key.level is Level.STATE
    assert got["SC HD-01"].key.level is Level.STATE
    assert got["SC HD-35"].key.level is Level.STATE
    assert "SC LOCAL" in format_key(got["SC LOCAL amendment-1"].key)
    assert "SC LOCAL" in format_key(got["SC LOCAL solicitor-circuit-1"].key)
    assert "SC LOCAL" in format_key(got["SC LOCAL mayor-ware-shoals"].key)


def test_party_prefix_stripped_from_names():
    got = by_key(parse())
    house = got["SC-01"]
    assert house.candidates[0].name == "Nancy Mace"
    assert house.candidates[0].party == "REP"
    assert house.candidates[1].name == "Michael B Moore"
    assert house.candidates[1].party == "DEM"
    # NON party entries (write-ins) keep their plain name.
    assert house.candidates[-1].name == "Write-In"
    assert house.candidates[-1].party is None


def test_votes_percent_winner_and_precincts():
    p = by_key(parse())["SC LOCAL amendment-1"]
    assert p.candidates[0].name == "Yes, In Favor of the Question"
    assert p.candidates[0].votes > p.candidates[1].votes
    assert p.candidates[0].winner is True
    assert p.candidates[1].winner is False

    house = by_key(parse())["SC-01"]
    assert house.total_votes == 390777          # source T, not recomputed from the trimmed slice
    assert house.precincts_total == 6
    assert house.precincts_reporting is None    # PR == 0 -> unknown, not "0 reported"
    assert house.candidates[0].pct == pytest.approx(58.2, abs=0.2)


def test_double_spaces_in_contest_names_are_normalised():
    assert format_key(race_key("U.S.  House of Representatives, District  1")) == "SC-01"
    assert format_key(race_key("State  Senate, District  1")) == "SC SD-01"
    assert format_key(race_key("State House of Representatives, District 35")) == "SC HD-35"
    assert format_key(race_key("U.S. Senate")) == "SC SEN"
    assert format_key(race_key("President and Vice President")) == \
        "SC LOCAL president-and-vice-president"


def test_parse_tolerates_junk():
    assert parse_sc(None, fetched_at=AT) == []
    assert parse_sc({}, fetched_at=AT) == []
    assert parse_sc([{"C": ""}], fetched_at=AT) == []


def test_fetch_uses_pinned_id_version_file(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith("current_ver.txt"):
            return httpx.Response(200, text="359624\n")
        if url.endswith("359624/json/en/summary.json"):
            return httpx.Response(200, text=(FIX / "2024_general_summary.json").read_text())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sc_sec.ScSec().fetch([parse_key("SC-01"), parse_key("SC SD-19")], c)

    assert set(by_key(asyncio.run(go()))) == {"SC-01", "SC SD-19"}
    assert seen == [f"{BASE}/122436/current_ver.txt",
                    f"{BASE}/122436/359624/json/en/summary.json"]


def test_discovery_reads_election_index(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.endswith("/elections.json"):
            return httpx.Response(200, json=[
                {"ElectionId": 130000, "ElectionDate": "2026-06-09", "Name": "Primary"},
                {"ElectionId": 130912, "ElectionDate": "2026-11-03", "Name": "General"},
            ])
        if url.endswith("/130912/current_ver.txt"):
            return httpx.Response(200, text="100")
        if url.endswith("/130912/100/json/en/summary.json"):
            return httpx.Response(200, text=(FIX / "2024_general_summary.json").read_text())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sc_sec.ScSec().fetch([parse_key("SC-01")], c)

    assert set(by_key(asyncio.run(go()))) == {"SC-01"}
    assert seen[0] == f"{BASE}/elections.json"
    assert f"{BASE}/130912/current_ver.txt" in seen


def test_unavailable_when_index_has_no_entry(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sc_sec.ScSec().fetch([parse_key("SC-01")], c)

    with pytest.raises(SourceUnavailable, match="no 2026-11-03 entry"):
        asyncio.run(go())
