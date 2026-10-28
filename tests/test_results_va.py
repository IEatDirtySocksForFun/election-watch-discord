import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results import va_elect
from electionwatch.sources.results.va_elect import parse_va, race_key

FIX = Path(__file__).parent / "fixtures" / "va_elect" / "va"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def parsed(name="2025_general.json"):
    return parse_va(load(name), fetched_at=AT)


def test_governor_statewide_totals():
    gov = by_key(parsed())["VA GOV"]
    assert gov.key.level is Level.GOVERNOR
    assert gov.title == "Governor"
    assert gov.source == "va_elect"
    assert gov.candidates[0].name == "Abigail D. Spanberger"
    assert gov.candidates[0].party == "DEM"
    assert gov.candidates[0].votes == 1976857
    assert gov.candidates[0].pct == 57.58
    assert gov.candidates[0].winner is True  # source sets isWinner
    assert gov.total_votes == 3433340  # voteTotal, write-ins included
    assert gov.precincts_reporting == gov.precincts_total == 133
    assert gov.expected_vote_pct == 100.0
    assert gov.source_updated_at == datetime(2025, 12, 1, 18, 15, 8, tzinfo=timezone.utc)


def test_statewide_executives_and_write_in():
    got = by_key(parsed())
    assert got["VA LTGOV"].candidates[0].name == "Ghazala F. Hashmi"
    assert got["VA AG"].candidates[0].party == "DEM"
    write_in = got["VA GOV"].candidates[-1]
    assert write_in.party == "WRI" and write_in.votes == 6897


def test_federal_state_and_local_keys():
    keys = set(by_key(parsed()))
    assert {"VA GOV", "VA LTGOV", "VA AG"} <= keys
    assert "VA HD-01" in keys
    assert "VA HD-100" in keys  # three-digit delegate district
    assert "VA LOCAL sheriff-lexington-city-rockbridge-county" in keys
    assert "VA LOCAL mayor-town-of-brodnax" in keys


def test_2026_general_federal_races():
    got = by_key(parsed("2026_general.json"))
    assert got["VA SEN"].candidates[0].name == "Mark R. Warner"
    assert got["VA-01"].title == "Member, House of Representatives (1st District)"
    assert got["VA-01"].precincts_total == 18  # precincts inside CD-01
    assert got["VA-11"].candidates[0].party == "DEM"
    assert got["VA HD-20"].key.level is Level.STATE  # 2026 special election
    assert "VA LOCAL proposed-constitutional-amendment-question-1" in got


def test_zero_votes_before_election_day():
    senate = by_key(parsed("2026_general.json"))["VA SEN"]
    assert senate.total_votes == 0
    assert senate.precincts_reporting is None  # 0 reporting -> None
    assert senate.expected_vote_pct is None
    assert senate.source_updated_at == datetime(2026, 10, 26, 22, 53, 33, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("Member, United States Senate")) == "VA SEN"
    assert format_key(race_key("Governor")) == "VA GOV"
    assert format_key(race_key("Lieutenant Governor")) == "VA LTGOV"
    assert format_key(race_key("Attorney General")) == "VA AG"
    assert format_key(race_key("Member, House of Representatives (7th District)")) == "VA-07"
    assert format_key(race_key("Member, House of Delegates (100th District)")) == "VA HD-100"
    assert (
        format_key(race_key("Proposed Constitutional Amendment Question 2"))
        == "VA LOCAL proposed-constitutional-amendment-question-2"
    )


def test_titles_keep_the_english_label():
    for race_result in parsed():
        assert race_result.title and all(ord(ch) < 0x2500 for ch in race_result.title)


def test_empty_payload_is_not_an_error():
    assert parse_va({}, fetched_at=AT) == []
    assert parse_va({"ballotItems": []}, fetched_at=AT) == []


def test_fetch_discovers_irregular_election_slug(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")  # not pinned in ELECTIONS
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/elections/virginia"):
            return httpx.Response(200, json=load("elections.json"))
        assert request.url.path.endswith("/2024NovemberGeneral/data")
        return httpx.Response(200, json=load("2025_general.json"))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await va_elect.VaElect().fetch([parse("VA GOV"), parse("VA-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"VA GOV"}
    assert seen[0].endswith("/elections/virginia")
