import csv
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.de_doe import parse_de, race_key

FIX = Path(__file__).parent / "fixtures" / "de_doe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load():
    with (FIX / "GE2024.csv").open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_federal_and_statewide_keys():
    keys = by_key(parse_de(load(), fetched_at=AT))
    assert "DE SEN" in keys
    assert "DE-AL" in keys
    assert "DE GOV" in keys
    assert "DE LTGOV" in keys
    assert "DE INSCOM" in keys
    assert "DE LOCAL us-president" in keys


def test_state_legislature_keys():
    keys = by_key(parse_de(load(), fetched_at=AT))
    assert "DE SD-02" in keys
    assert "DE SD-03" in keys
    assert "DE HD-10" in keys
    assert "DE HD-11" in keys


def test_local_keys():
    keys = by_key(parse_de(load(), fetched_at=AT))
    assert "DE LOCAL new-castle-county-clerk-of-the-peace" in keys
    assert "DE LOCAL city-of-wilmington-mayor" in keys


def test_senate_votes_and_party():
    r = by_key(parse_de(load(), fetched_at=AT))["DE SEN"]
    assert r.key.level is Level.SENATE
    assert r.candidates[0].name == "Lisa Blunt Rochester"
    assert r.candidates[0].party == "DEM"
    assert r.candidates[0].votes == 283298
    assert r.total_votes == sum(c.votes for c in r.candidates)


def test_source_updated_at_from_csv():
    r = by_key(parse_de(load(), fetched_at=AT))["DE GOV"]
    assert r.source_updated_at == datetime(2024, 11, 12, 12, 59, 37, tzinfo=timezone.utc)


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senator")) == "DE SEN"
    assert format_key(race_key("U.S. Representative In Congress")) == "DE-AL"
    assert format_key(race_key("Governor")) == "DE GOV"
    assert format_key(race_key("State Senator District 2")) == "DE SD-02"
    assert format_key(race_key("State Representative District 10")) == "DE HD-10"
    assert format_key(race_key("New Castle County Clerk of the Peace")) == \
        "DE LOCAL new-castle-county-clerk-of-the-peace"


async def test_discovery_never_serves_an_older_year_as_this_election():
    import httpx
    import pytest

    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.results import de_doe

    page = '<a href="GE2022.csv">2022</a> <a href="GE2024.csv">2024</a>'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceUnavailable):
            await de_doe._csv_url(client, "2030-11-05")
        page += ' <a href="GE2030.csv">2030</a>'
        assert (await de_doe._csv_url(client, "2030-11-05")).endswith("/GE2030.csv")
