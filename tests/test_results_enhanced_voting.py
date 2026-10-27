import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import enhanced_voting
from electionwatch.sources.results.enhanced_voting import (
    ELECTIONS, EnhancedVoting, export_url, feeds, race_key,
)
from electionwatch.sources.results.ga_sos import parse_ga

FIX = Path(__file__).parent / "fixtures" / "enhanced_voting"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_parse_maps_state_aware_races():
    results = by_key(
        parse_ga(load("export-2024NovGen.json"), fetched_at=AT)
    )
    assert results["GA-01"].key.level is Level.HOUSE
    assert results["GA-01"].title == "US House of Representatives - District 1"
    assert results["GA HD-01"].key.level is Level.STATE
    assert results["GA LOCAL president-of-the-us"].key.level is Level.LOCAL
    amend = results["GA LOCAL proposed-constitutional-amendment-1"]
    assert {c.name for c in amend.candidates} == {"Yes", "No"}


def test_race_key_rewrites_state():
    key = race_key("AZ", "US House of Representatives - District 3")
    assert key.state == "AZ"
    assert key.level is Level.HOUSE
    assert key.district == "03"
    assert format_key(key) == "AZ-03"


def test_feed_configuration(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    assert feeds("2026-11-03", "AZ") == ELECTIONS["2026-11-03"]
    assert export_url("AZ", "2026NovGen") == \
        "https://app.enhancedvoting.com/cdn/results/AZ/export-2026NovGen.json"


def test_fetch_uses_cdn_export(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/export-2024NovGen.json")
        return httpx.Response(200, json=load("export-2024NovGen.json"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await EnhancedVoting().fetch([parse("GA-01")], client)

    got = by_key(__import__("asyncio").run(go()))
    assert set(got) == {"GA-01"}
    assert got["GA-01"].source == "enhanced_voting"


def test_fetch_is_unavailable_when_export_404(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404)
        )) as client:
            return await EnhancedVoting().fetch([parse("AZ SEN")], client)

    with pytest.raises(SourceUnavailable, match="not published yet"):
        __import__("asyncio").run(go())
