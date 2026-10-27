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


async def test_url_is_built_not_discovered_so_no_directory_index_request(monkeypatch):
    # reports/ answers 403 to automated GETs; the adapter must go straight to the CSV
    # (2024 worked that way, and discovery broke in 2026).
    import httpx
    import pytest

    from electionwatch.models import RaceKey
    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.results import de_doe

    assert de_doe.csv_url("2026-11-03") == "https://elections.delaware.gov/reports/GE2026.csv"
    assert de_doe.csv_url("2024-11-05") == "https://elections.delaware.gov/reports/GE2024.csv"

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(403)

    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable):
            await de_doe.DeDoe().fetch([RaceKey("DE", Level.SENATE, "SEN")], client)
    assert seen == ["https://elections.delaware.gov/reports/GE2026.csv"]


async def test_unpublished_report_404_is_source_unavailable(monkeypatch):
    import httpx
    import pytest

    from electionwatch.models import RaceKey
    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.results import de_doe

    def handler(request):
        return httpx.Response(404)

    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceUnavailable) as excinfo:
            await de_doe.DeDoe().fetch([RaceKey("DE", Level.SENATE, "SEN")], client)
    assert "not published yet" in str(excinfo.value)
    assert "GE2026.csv" in str(excinfo.value)


async def test_other_http_errors_are_not_reported_as_unpublished(monkeypatch):
    import httpx
    import pytest

    from electionwatch.models import RaceKey
    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.results import de_doe

    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceUnavailable) as excinfo:
            await de_doe.DeDoe().fetch([RaceKey("DE", Level.SENATE, "SEN")], client)
    assert "not published" not in str(excinfo.value)


async def test_fetch_parses_the_year_file(monkeypatch):
    import httpx

    from electionwatch.models import RaceKey
    from electionwatch.racekeys import format_key
    from electionwatch.sources.results import de_doe

    served = (FIX / "GE2024.csv").read_text(encoding="utf-8-sig")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=served))
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    async with httpx.AsyncClient(transport=transport) as client:
        results = await de_doe.DeDoe().fetch([RaceKey("DE", Level.SENATE, "SEN")], client)
    assert [format_key(r.key) for r in results] == ["DE SEN"]
