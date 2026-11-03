import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results.id_results import (
    DATA,
    IdResults,
    data_url,
    election_slug,
    parse_id_data,
    race_key,
)

FIX = Path(__file__).parent / "fixtures" / "id_results"
AT = datetime(2026, 11, 3, 21, 50, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_election_slug_and_urls():
    assert election_slug("2026-11-03") == "nov2026"
    assert election_slug("2026-05-19") == "may2026"
    assert data_url("2026-11-03").endswith("/api/elections/id/nov2026/data")
    assert data_url("2026-11-03").startswith("https://results.voteidaho.gov/results/public/")


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "ID SEN"
    assert format_key(race_key("United States Representative District 1")) == "ID-01"
    assert format_key(race_key("Governor")) == "ID GOV"
    assert format_key(race_key("State Senator District 1")) == "ID SD-01"
    assert format_key(race_key("State Representative District 1 Seat A")) == "ID HD-01A"
    assert format_key(race_key("Proposition 1: Reproductive Freedom & Privacy Act")) == (
        "ID LOCAL proposition-1-reproductive-freedom-privacy-act"
    )


def test_live_general_feed_before_polls_close():
    races = {
        format_key(race_result.key): race_result
        for race_result in parse_id_data(load("2026_general.json"), fetched_at=AT)
    }
    assert set(races) == {
        "ID SEN",
        "ID-01",
        "ID GOV",
        "ID SD-01",
        "ID HD-01A",
        "ID LOCAL proposition-1-reproductive-freedom-privacy-act",
    }
    senate = races["ID SEN"]
    assert senate.title == "United States Senator"
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in senate.candidates] == [
        ("Natalie M Fleming", "IND", 0),
        ("Todd Achilles", "IND", 0),
        ("Matt Loesby", "LIB", 0),
        ("Jim Risch", "REP", 0),
    ]
    assert senate.total_votes == 0
    assert senate.precincts_reporting == 0 and senate.precincts_total == 44
    assert senate.expected_vote_pct is None  # zero precincts is unknown, not 0%
    assert senate.source_updated_at == datetime(2026, 11, 2, 22, 0, 3, 339693, tzinfo=timezone.utc)
    assert senate.url == "https://results.voteidaho.gov/results/public/id/elections/nov2026"
    # A declared write-in candidate is a real option, not a ballot-statistics row.
    governor = {candidate.name: candidate for candidate in races["ID GOV"].candidates}
    assert governor["Jacob Burnett"].votes == 0
    assert governor["Jacob Burnett"].party == "IND"
    assert races["ID HD-01A"].key.level is Level.STATE
    measure = races["ID LOCAL proposition-1-reproductive-freedom-privacy-act"]
    assert [(candidate.name, candidate.votes) for candidate in measure.candidates] == [
        ("Yes", 0),
        ("No", 0),
    ]


def test_live_feed_with_votes_and_winners():
    races = {
        format_key(race_result.key): race_result
        for race_result in parse_id_data(load("2025_general.json"), fetched_at=AT)
    }
    mayor = races["ID LOCAL burley-city-mayor"]
    assert mayor.total_votes == 640
    assert mayor.precincts_reporting == 2 and mayor.precincts_total == 2
    assert mayor.expected_vote_pct == 100.0
    assert mayor.candidates[0].name == "Steve Ormond"
    assert mayor.candidates[0].party == "NPA"
    assert mayor.candidates[0].pct == 100.0
    assert mayor.candidates[0].winner is True
    trustee = races["ID LOCAL bruneau-grand-view-school-district-trustee-zone-3"]
    assert [(candidate.name, candidate.votes) for candidate in trustee.candidates] == [
        ("Raelynn Mathews", 70),
        ("Jeremy Pineda", 67),
    ]
    assert trustee.candidates[0].pct == 51.09
    assert trustee.candidates[0].winner is True
    question = races["ID LOCAL mountain-community-center-district-creation-question"]
    assert question.total_votes == 2768
    assert [(candidate.name, candidate.votes) for candidate in question.candidates] == [
        ("Yes", 1760),
        ("No", 1008),
    ]
    assert question.source_updated_at == datetime(2026, 5, 27, 16, 8, 29, 558308, tzinfo=timezone.utc)


def test_adapter_is_opt_in():
    assert IdResults.name == "id_results"
    assert IdResults.states == frozenset({"ID"})
    assert IdResults.scrape is True
    assert IdResults.enabled_by_default is False


@respx.mock
async def test_fetch_selects_only_idaho_keys(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    respx.get(DATA.format(slug="nov2026")).respond(json=load("2026_general.json"))
    keys = [parse("ID SEN"), parse("ID-01"), parse("NH SEN")]
    async with httpx.AsyncClient() as client:
        results = await IdResults().fetch(keys, client)
    assert {format_key(race_result.key) for race_result in results} == {"ID SEN", "ID-01"}


@respx.mock
async def test_list_races_uses_the_same_document(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    respx.get(DATA.format(slug="nov2026")).respond(json=load("2026_general.json"))
    async with httpx.AsyncClient() as client:
        assert await IdResults().list_races("NH", client) == []
        races = await IdResults().list_races("ID", client)
    assert len(races) == 6
    assert races[0][0] == parse("ID SEN")
