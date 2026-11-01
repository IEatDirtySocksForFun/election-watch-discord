import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.de_doe import parse_de, parse_de_enr, race_key

FIX = Path(__file__).parent / "fixtures" / "de_doe"
AT = datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc)


def load():
    with (FIX / "GE2024.csv").open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_enr():
    return json.loads((FIX / "Election_StatewideResults_ID_GE2026.json").read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


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
    race_result = by_key(parse_de(load(), fetched_at=AT))["DE SEN"]
    assert race_result.key.level is Level.SENATE
    assert race_result.candidates[0].name == "Lisa Blunt Rochester"
    assert race_result.candidates[0].party == "DEM"
    assert race_result.candidates[0].votes == 283298
    assert race_result.total_votes == sum(candidate.votes for candidate in race_result.candidates)


def test_source_updated_at_from_csv():
    race_result = by_key(parse_de(load(), fetched_at=AT))["DE GOV"]
    assert race_result.source_updated_at == datetime(2024, 11, 12, 12, 59, 37, tzinfo=timezone.utc)
    # The CSV has no precinct columns.
    assert race_result.precincts_total is None


def test_enr_json_precincts_are_per_contest():
    keys = by_key(parse_de_enr(load_enr(), fetched_at=AT))
    assert (keys["DE SEN"].precincts_reporting, keys["DE SEN"].precincts_total) == (0, 530)
    assert (keys["DE SD-01"].precincts_reporting, keys["DE SD-01"].precincts_total) == (0, 37)


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senator")) == "DE SEN"
    assert format_key(race_key("U.S. Representative In Congress")) == "DE-AL"
    assert format_key(race_key("Representative In Congress")) == "DE-AL"
    assert format_key(race_key("Governor")) == "DE GOV"
    assert format_key(race_key("State Senator District 2")) == "DE SD-02"
    assert format_key(race_key("State Representative District 10")) == "DE HD-10"
    assert (
        format_key(race_key("New Castle County Clerk of the Peace")) == "DE LOCAL new-castle-county-clerk-of-the-peace"
    )


def test_enr_json_keys_and_candidates():
    # 2026 general statewide ENR JSON, fetched 2026-10-31 (pre-election, every vote 0).
    keys = by_key(parse_de_enr(load_enr(), fetched_at=AT))
    assert {"DE SEN", "DE-AL", "DE AG", "DE AUD", "DE TREAS", "DE SD-01", "DE HD-10"} <= set(keys)
    assert "DE LOCAL new-castle-county-sheriff" in keys

    senate = keys["DE SEN"]
    assert senate.key.level is Level.SENATE
    assert senate.title == "U.S. Senator"
    assert [candidate.name for candidate in senate.candidates] == [
        "Chris Coons",
        'Michael "Dr. Mike" Katz',
    ]
    assert [candidate.party for candidate in senate.candidates] == ["DEM", "REP"]
    assert senate.total_votes == 0


def test_enr_json_url_and_timestamp():
    keys = by_key(parse_de_enr(load_enr(), fetched_at=AT))
    assert keys["DE SEN"].url == "https://elections.delaware.gov/results/enr/GE2026.html"
    assert keys["DE SEN"].source_updated_at == datetime(2026, 9, 29, 13, 35, 45, 646000, tzinfo=timezone.utc)
    assert keys["DE SEN"].source == "de_doe"


def test_enr_json_counts_votes_and_percentages():
    # The saved live file is pre-election, so these two rows exercise the arithmetic.
    rows = [
        {
            "Election Id": "GE2026",
            "Contest Title": "Attorney General",
            "Candidate Name": "KATHY JENNINGS",
            "Party Name": "Democratic Party",
            "Pos": 2,
            "Total Votes": "1,204",
            "Percentage": 52.5,
            "ReportTime": "2026-11-03T22:10:00-05:00",
        },
        {
            "Election Id": "GE2026",
            "Contest Title": "Attorney General",
            "Candidate Name": "JULIANNE MURRAY",
            "Party Name": "Republican Party",
            "Pos": 1,
            "Total Votes": "1,090",
            "Percentage": 47.5,
            "ReportTime": "2026-11-03T22:10:00-05:00",
        },
    ]
    race_result = by_key(parse_de_enr(rows, fetched_at=AT))["DE AG"]
    assert race_result.total_votes == 2294
    # finalize() orders candidates by votes, so the percentages stay attached to the right name.
    assert [candidate.name for candidate in race_result.candidates] == ["Kathy Jennings", "Julianne Murray"]
    assert [candidate.pct for candidate in race_result.candidates] == [52.5, 47.5]
    assert race_result.source_updated_at == datetime(2026, 11, 4, 3, 10, tzinfo=timezone.utc)


async def test_url_is_built_not_discovered_so_no_directory_index_request(monkeypatch):
    # reports/ answers 403 to automated GETs; the adapter must go straight to a built
    # feed URL (2024 worked that way, and discovery broke in 2026).
    import httpx
    import pytest

    from electionwatch.models import RaceKey
    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.results import de_doe

    assert (
        de_doe.enr_url("2026-11-03")
        == "https://elections.delaware.gov/results/enr/Election_StatewideResults_ID_GE2026.json"
    )
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
    # A block is reported, not routed around: no fallback request, no directory listing.
    assert seen == [de_doe.enr_url("2026-11-03")]


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


async def test_fetch_parses_the_year_csv_when_the_enr_json_is_absent(monkeypatch):
    import httpx

    from electionwatch.models import RaceKey
    from electionwatch.racekeys import format_key
    from electionwatch.sources.results import de_doe

    served = (FIX / "GE2024.csv").read_text(encoding="utf-8-sig")

    def handler(request):
        # 2024 predates the ENR JSON: the archive 404s and the reports CSV answers.
        if request.url.path.endswith(".json"):
            return httpx.Response(404, text="")
        return httpx.Response(200, text=served)

    transport = httpx.MockTransport(handler)
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    async with httpx.AsyncClient(transport=transport) as client:
        results = await de_doe.DeDoe().fetch([RaceKey("DE", Level.SENATE, "SEN")], client)
    assert [format_key(race_result.key) for race_result in results] == ["DE SEN"]


async def test_fetch_prefers_the_enr_json(monkeypatch):
    import httpx

    from electionwatch.models import RaceKey
    from electionwatch.racekeys import format_key
    from electionwatch.sources.results import de_doe

    served = load_enr()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=served)

    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await de_doe.DeDoe().fetch(
            [RaceKey("DE", Level.SENATE, "SEN"), RaceKey("DE", Level.HOUSE, "HOUSE", "AL")], client
        )
    assert seen == [de_doe.enr_url("2026-11-03")]
    assert sorted(format_key(race_result.key) for race_result in results) == ["DE SEN", "DE-AL"]
