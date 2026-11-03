"""ct_ems parses the Connecticut Secretary of the State's EMS JSON files."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results.ct_ems import (
    ELECTIONS,
    CtEms,
    election_id,
    parse_ct_ems,
    race_key,
)

FIX = Path(__file__).parent / "fixtures" / "ct_ems"
AT = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)
GOV = RaceKey("CT", Level.GOVERNOR, "GOV")
CT_01 = RaceKey("CT", Level.HOUSE, "HOUSE", "01")


def _load(name: str):
    return json.loads((FIX / name).read_text())


def _parse(lookup: str, data: str):
    return parse_ct_ems(_load(lookup), _load(data), fetched_at=AT)


def _by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_metadata():
    assert CtEms.name == "ct_ems"
    assert CtEms.states == frozenset({"CT"})
    assert CtEms.scrape is True
    assert CtEms.enabled_by_default is False


def test_election_id_matches_the_tracked_date():
    assert election_id(_load("elections.json"), "2026-11-03") == "108"
    assert election_id(_load("elections.json"), "2028-11-07") is None


def test_2026_candidate_lists_before_counting():
    races = _by_key(_parse("lookupdata.json", "electiondata.json"))
    assert set(races) == {"CT GOV", "CT-01", "CT SD-01", "CT HD-01"}
    gov = races["CT GOV"]
    assert gov.title == "Governor and Lieutenant Governor"
    assert (gov.precincts_reporting, gov.precincts_total) == (0, 779)
    assert gov.total_votes == 0
    assert [candidate.name for candidate in gov.candidates] == [
        "Ned Lamont and Susan Bysiewicz",
        "Ryan Fazio and Matt Corey",
        "Zenobia Adgers and Rick Adgers",
        "Christian Noe and Michael S. Grant",
        "L. Lee Whitnum and David Spirt",
    ]
    assert [candidate.party for candidate in gov.candidates] == ["DEM", "REP", "WRI", "WRI", "WRI"]
    assert all(candidate.votes == 0 for candidate in gov.candidates)


def test_party_lines_and_votes_are_merged_per_candidate():
    races = _by_key(_parse("2024-lookupdata.json", "2024-electiondata.json"))
    senate = races["CT SEN"]
    assert (senate.precincts_reporting, senate.precincts_total) == (759, 759)
    merged = senate.candidates[0]
    assert (merged.name, merged.party, merged.votes) == ("Christopher S. Murphy", "DEM", 1000695)
    assert merged.pct == 58.58
    assert senate.total_votes == 1708259
    assert [candidate.name for candidate in senate.candidates][1] == "Matthew M. Corey"


def test_race_key_mapping():
    def office(name, kind="SW"):
        return {"ID": "1", "NM": name, "OT": kind}

    assert format_key(race_key(office("Governor and Lieutenant Governor"))) == "CT GOV"
    assert format_key(race_key(office("United States Senator"))) == "CT SEN"
    assert format_key(race_key(office("Secretary of the State"))) == "CT SOS"
    assert format_key(race_key(office("Attorney General"))) == "CT AG"
    assert format_key(race_key(office("Representative in Congress 5", "C"))) == "CT-05"
    assert format_key(race_key(office("State Senator 12", "S"))) == "CT SD-12"
    assert format_key(race_key(office("State Representative 42", "A"))) == "CT HD-42"
    probate = race_key(office("Judge of Probate 15 To Fill Vacancy", "PD"))
    assert probate.level is Level.LOCAL
    assert probate.special is True
    assert format_key(race_key(office("Presidential Electors for"))) == "CT LOCAL us-president"


def test_empty_payloads_are_empty():
    assert parse_ct_ems({}, {}, fetched_at=None) == []  # type: ignore[arg-type]
    assert parse_ct_ems({"officeList": []}, {"stateVotes": {}}, fetched_at=None) == []  # type: ignore[arg-type]


@respx.mock
async def test_fetch_reads_the_versioned_files(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    base = "https://ctemspublic.pcctg.net/ng-app/data"
    moved = f"{base}/Elections-2026.json"
    respx.get(ELECTIONS).mock(return_value=httpx.Response(301, headers={"Location": moved}))
    respx.get(moved).mock(return_value=httpx.Response(200, json=_load("elections.json")))
    respx.get(f"{base}/election/108/Version.json").mock(return_value=httpx.Response(200, json=_load("version.json")))
    respx.get(f"{base}/election/108/51795/Lookupdata.json").mock(
        return_value=httpx.Response(200, json=_load("lookupdata.json"))
    )
    respx.get(f"{base}/election/108/51795/Electiondata.json").mock(
        return_value=httpx.Response(200, json=_load("electiondata.json"))
    )
    async with httpx.AsyncClient() as client:  # no follow_redirects: the adapter asks
        results = await CtEms().fetch([GOV, CT_01], client)
    assert [format_key(race_result.key) for race_result in results] == ["CT GOV", "CT-01"]


@respx.mock
async def test_fetch_without_a_matching_election_is_unavailable(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    other = [{"ID": "106", "Name": "01/06/2026 -- 25th Assembly District Special Election"}]
    respx.get(ELECTIONS).mock(return_value=httpx.Response(200, json=other))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceUnavailable):
            await CtEms().fetch([RaceKey("CT", Level.GOVERNOR, "GOV")], client)


async def test_fetch_ignores_other_states():
    async with httpx.AsyncClient() as client:
        assert await CtEms().fetch([RaceKey("TX", Level.SENATE, "SEN")], client) == []
