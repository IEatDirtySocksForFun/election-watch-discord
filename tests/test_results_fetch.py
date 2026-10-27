"""Offline end-to-end fetch() tests: adapters + httpx.MockTransport, no network."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import clarity, ga_sos, nc_sbe, pa_dos, tx_sos

FIX = Path(__file__).parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_tx_fetch_uses_pinned_election(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    files = {
        "Home.json": "home.json",
        "Federal.json": "federal.json",
        "StateWide.json": "statewide.json",
        "Districted.json": "districted.json",
        "StateWideQ.json": "statewide_questions.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        path = FIX / "tx_sos" / files[name]
        return httpx.Response(200, json=json.loads(path.read_text()))

    async def go():
        async with client(handler) as c:
            return await tx_sos.TxSos().fetch([parse("TX SEN"), parse("TX-01")], c)

    got = by_key(run(go()))
    assert set(got) == {"TX SEN", "TX-01"}
    assert got["TX SEN"].source == "tx_sos"
    assert got["TX SEN"].precincts_total == 9885


def test_nc_fetch_reads_enr_files(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    files = {"contest.txt": "contest.txt", "results_0.txt": "results_0.txt",
             "county.txt": "county.txt"}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        path = FIX / "nc_sbe" / files[name]
        return httpx.Response(200, text=path.read_text(encoding="utf-8"))

    async def go():
        async with client(handler) as c:
            return await nc_sbe.NcSbe().fetch([parse("NC GOV")], c)

    got = by_key(run(go()))
    assert set(got) == {"NC GOV"}
    assert got["NC GOV"].candidates[0].name == "Josh Stein"


def test_ga_fetch_parses_export(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/export-2024NovGen.json")
        return httpx.Response(
            200, json=json.loads((FIX / "ga_sos" / "export-2020NovGen.json").read_text()))

    async def go():
        async with client(handler) as c:
            return await ga_sos.GaSos().fetch([parse("GA SEN"), parse("GA-01")], c)

    got = by_key(run(go()))
    assert set(got) == {"GA SEN", "GA-01"}
    assert got["GA SEN"].candidates[0].party == "REP"


def test_pa_fetch_queries_every_office(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    payloads = {
        2: (FIX / "pa_dos" / "office_us_senate.json").read_text(),
        11: (FIX / "pa_dos" / "office_us_house.json").read_text(),
        12: (FIX / "pa_dos" / "office_state_senate.json").read_text(),
        13: (FIX / "pa_dos" / "office_state_house.json").read_text(),
    }
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        office = int(request.url.params["officeId"])
        seen.append(office)
        if office not in payloads:
            return httpx.Response(200, text='""')
        return httpx.Response(200, text=payloads[office])

    async def go():
        async with client(handler) as c:
            return await pa_dos.PaDos().fetch([parse("PA SEN"), parse("PA-01")], c)

    got = by_key(run(go()))
    assert set(got) == {"PA SEN", "PA-01"}
    # Empty offices are retried with isactive=0, so each id appears once or twice.
    assert set(seen) == set(pa_dos.OFFICES.values())
    assert max(seen.count(i) for i in set(seen)) <= 2


def test_clarity_fetch_resolves_version_then_summary(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.setenv("CLARITY_FEEDS", json.dumps(
        [{"state": "IL", "jurisdiction": "McLean", "election_id": "127349"}]))
    summary = (FIX / "clarity" / "il_mclean_2026_general_summary.json").read_text()
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("current_ver.txt"):
            return httpx.Response(200, text="382192\n")
        if request.url.path.endswith("summary.json"):
            return httpx.Response(200, json=json.loads(summary))
        if request.url.path.endswith("electionsettings.json"):
            return httpx.Response(200, json={"websiteupdatedat": "10/26/2026 11:42:44 AM CDT"})
        return httpx.Response(404)

    async def go():
        async with client(handler) as c:
            return await clarity.Clarity().fetch([parse("IL SEN")], c)

    got = by_key(run(go()))
    assert set(got) == {"IL SEN"}
    assert got["IL SEN"].source == "clarity"
    assert got["IL SEN"].source_updated_at == datetime(
        2026, 10, 26, 16, 42, 44, tzinfo=timezone.utc)
    assert any(p.endswith("/127349/current_ver.txt") for p in paths)


def test_clarity_without_feed_is_unavailable(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.delenv("CLARITY_FEEDS", raising=False)

    async def go():
        async with client(lambda r: httpx.Response(404)) as c:
            return await clarity.Clarity().fetch([parse("NV SEN")], c)

    with pytest.raises(SourceUnavailable, match="no feed configured"):
        run(go())
