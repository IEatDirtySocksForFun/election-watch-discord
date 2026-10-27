import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse as parse_key
from electionwatch.sources.results import clarity
from electionwatch.sources.results.clarity import parse_clarity

FIX = Path(__file__).parent / "fixtures" / "clarity"
BASE = "https://results.enr.clarityelections.com/WV/122766"
AT = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)


def summary():
    return json.loads((FIX / "wv_2024_general_summary.json").read_text())


def parsed():
    return parse_clarity(summary(), state="WV", fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_wv_2024_feed_is_configured():
    assert {"state": "WV", "election_id": "122766"} in clarity.ELECTIONS["2024-11-05"]
    assert "WV" in clarity.Clarity.states


def test_keys_federal_statewide_legislature():
    got = by_key(parsed())
    assert got["WV SEN"].key.level is Level.SENATE
    assert got["WV-01"].key.level is Level.HOUSE
    assert got["WV GOV"].key.level is Level.GOVERNOR
    assert got["WV AUD"].key.level is Level.GOVERNOR
    assert got["WV TREAS"].key.level is Level.GOVERNOR
    assert got["WV AG"].key.level is Level.GOVERNOR
    assert got["WV SD-01"].key.level is Level.STATE
    assert got["WV SD-17"].key.level is Level.STATE
    assert got["WV HD-01"].key.level is Level.STATE
    assert got["WV HD-100"].key.level is Level.STATE
    assert "WV LOCAL" in format_key(got["WV LOCAL amendment-no-1-protection-of-persons-"
                                     "against-medically-assisted-suicide"].key)


def test_votes_parties_winner_and_precincts():
    got = by_key(parsed())
    senate = got["WV SEN"]
    assert senate.total_votes == 747702
    assert senate.precincts_total == 55
    assert senate.precincts_reporting == 53
    justice = senate.candidates[0]
    assert (justice.name, justice.party, justice.votes) == ("JIM JUSTICE", "REP", 514079)
    assert justice.pct == pytest.approx(68.75, abs=0.2)

    hd100 = got["WV HD-100"]
    assert hd100.precincts_total == 1 and hd100.precincts_reporting == 1
    assert [c.party for c in hd100.candidates] == ["REP", "DEM"]


def test_ballot_measure_and_nonpartisan_judge_are_local():
    got = by_key(parsed())
    amendment = next(r for k, r in got.items() if "amendment-no-1" in k)
    assert [c.name for c in amendment.candidates] == ["FOR", "AGAINST"]
    assert all(c.party is None for c in amendment.candidates)
    judge = next(r for k, r in got.items() if "circuit-court-judge" in k)
    assert judge.candidates[0].name == "MICHAEL E. FROBLE"
    assert judge.precincts_reporting is None      # PR == 0 in the source


def test_fetch_uses_configured_wv_feed(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    monkeypatch.delenv("CLARITY_FEEDS", raising=False)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url == f"{BASE}/current_ver.txt":
            return httpx.Response(200, text="356048")
        if url.endswith("/356048/json/en/summary.json"):
            return httpx.Response(200, text=(FIX / "wv_2024_general_summary.json").read_text())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await clarity.Clarity().fetch(
                [parse_key("WV GOV"), parse_key("WV HD-100")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"WV GOV", "WV HD-100"}
    assert seen[0] == f"{BASE}/current_ver.txt"


def test_2026_general_feed_is_not_published_yet(monkeypatch):
    """The 2026 primary feed (id 126209) is live; the general has no Clarity id yet."""
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.delenv("CLARITY_FEEDS", raising=False)
    assert all(f["state"] != "WV" for f in clarity.feeds("2026-11-03"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(404))) as c:
            return await clarity.Clarity().fetch([parse_key("WV GOV")], c)

    with pytest.raises(Exception, match="no feed configured"):
        asyncio.run(go())
