import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.racekeys import parse as parse_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import ri_boe
from electionwatch.sources.results.ri_boe import parse_ri, race_key

FIX = Path(__file__).parent / "fixtures" / "ri_boe" / "RI"
BASE = "https://rigov.s3.amazonaws.com/election/results"
AT = datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc)


def docs():
    return [
        json.loads((FIX / fh).read_text()) for fh in ("statewide.json", "ga_senator.json", "ga_representative.json")
    ]


def parse():
    return parse_ri(docs(), fetched_at=AT)


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_keys_cover_all_levels():
    got = by_key(parse())
    assert got["RI SEN"].key.level is Level.SENATE
    assert got["RI-01"].key.level is Level.HOUSE
    assert got["RI-02"].key.level is Level.HOUSE
    assert got["RI SD-01"].key.level is Level.STATE
    assert got["RI HD-01"].key.level is Level.STATE
    assert "RI LOCAL" in format_key(got["RI LOCAL us-president"].key)


def test_race_key_mapping():
    assert format_key(race_key("Presidential Electors For:")) == "RI LOCAL us-president"
    assert format_key(race_key("Senator in Congress")) == "RI SEN"
    assert format_key(race_key("Representative in Congress District 2")) == "RI-02"
    assert format_key(race_key("Governor")) == "RI GOV"
    assert format_key(race_key("Lieutenant Governor")) == "RI LTGOV"
    assert format_key(race_key("General Treasurer")) == "RI TREAS"
    assert format_key(race_key("Senator in General Assembly District 4")) == "RI SD-04"
    assert format_key(race_key("Representative in General Assembly District 12")) == "RI HD-12"
    assert (
        format_key(race_key("3. HOUSING AND COMMUNITY OPPORTUNITY - $120,000,000"))
        == "RI LOCAL ballot-question-3-housing-and-community-opportunity-120-000-000"
    )


def test_party_prefix_only_stripped_when_it_matches():
    got = by_key(parse())
    senate = got["RI SEN"]
    whitehouse = next(candidate for candidate in senate.candidates if candidate.name == "Sheldon Whitehouse")
    assert whitehouse.party == "DEM"
    assert whitehouse.votes == 294665
    # General-assembly files repeat the party code in the name.
    sd1 = got["RI SD-01"]
    assert sd1.candidates[0].name == "Jacob Bissaillon"
    assert sd1.candidates[0].party == "DEM"
    assert sd1.candidates[-1].name == "Write-in"


def test_votes_percent_precincts_and_timestamp():
    got = by_key(parse())
    house = got["RI-01"]
    assert house.total_votes == 221118  # source total_votes field
    assert house.precincts_total == 195
    assert house.precincts_reporting is not None
    assert house.expected_vote_pct == pytest.approx(round(100.0 * house.precincts_reporting / house.precincts_total, 2))
    # Percentages are computed from the vote totals.
    assert house.candidates[0].pct == pytest.approx(
        round(100.0 * house.candidates[0].votes / house.total_votes, 2), abs=0.02
    )
    assert house.source_updated_at == datetime(2024, 11, 22, 22, 0, tzinfo=timezone.utc)


def test_parse_tolerates_junk():
    assert parse_ri(None, fetched_at=AT) == []
    assert parse_ri({}, fetched_at=AT) == []
    assert parse_ri([{"contests": [{"name": ""}]}], fetched_at=AT) == []


def _handler(bodies, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        name = url.rsplit("/", 1)[-1]
        if name in bodies:
            return httpx.Response(200, text=bodies[name])
        return httpx.Response(403)  # S3 answers 403 for a missing key

    return handler


def test_fetch_reads_three_documents(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []
    bodies = {fh: (FIX / fh).read_text() for fh in ("statewide.json", "ga_senator.json", "ga_representative.json")}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(bodies, seen))) as client:
            return await ri_boe.RiBoe().fetch([parse_key("RI SEN"), parse_key("RI HD-03")], client)

    assert set(by_key(asyncio.run(go()))) == {"RI SEN", "RI HD-03"}
    assert seen == [f"{BASE}/2024/general_election/{fh}" for fh in ri_boe.FILES]


def test_general_assembly_files_are_optional(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []
    bodies = {"statewide.json": (FIX / "statewide.json").read_text()}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(bodies, seen))) as client:
            return await ri_boe.RiBoe().fetch([parse_key("RI SEN")], client)

    assert set(by_key(asyncio.run(go()))) == {"RI SEN"}
    assert len(seen) == 3


def test_unavailable_before_first_publish(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler({}, seen))) as client:
            return await ri_boe.RiBoe().fetch([parse_key("RI SEN")], client)

    with pytest.raises(SourceUnavailable, match="not published yet"):
        asyncio.run(go())
    assert seen == [f"{BASE}/2026/general_election/statewide.json"]


def test_list_races_returns_empty_when_unpublished(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler({}, seen))) as client:
            return await ri_boe.RiBoe().list_races("RI", client)

    assert asyncio.run(go()) == []
