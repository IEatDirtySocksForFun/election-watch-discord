from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results.fl_sos import FlSos, parse_fl, race_key

FIX = Path(__file__).parent / "fixtures" / "fl_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load():
    return (FIX / "results-2024.tsv").read_text(encoding="latin-1")


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_race_key_mapping():
    assert format_key(race_key("USS", "United States Senator", "")) == "FL SEN"
    assert format_key(race_key("USR", "United States Representative", "003")) == "FL-03"
    assert format_key(race_key("STS", "State Senator", "012")) == "FL SD-12"
    assert format_key(race_key("STR", "State Representative", "010")) == "FL HD-10"
    assert (
        format_key(race_key("PRE", "President of the United States", "")) == "FL LOCAL president-of-the-united-states"
    )
    assert (
        format_key(race_key("A01", "Partisan Election of Members of District School Boards", ""))
        == "FL LOCAL partisan-election-of-members-of-district-school-boards"
    )


def test_parse_aggregates_counties():
    results = by_key(parse_fl(load(), fetched_at=AT))
    sen = results["FL SEN"]
    assert sen.key.level is Level.SENATE
    assert sen.candidates[0].name == "Rick Scott"
    assert sen.candidates[0].party == "REP"
    # Votes are summed across the five kept counties.
    assert sen.total_votes == sum(c.votes for c in sen.candidates)
    assert sen.precincts_total == sen.precincts_reporting > 0


def test_parse_house_and_state_legislature():
    results = by_key(parse_fl(load(), fetched_at=AT))
    assert results["FL-03"].key.level is Level.HOUSE
    assert results["FL SD-19"].key.level is Level.STATE
    assert results["FL HD-10"].key.district == "10"


def test_parse_amendment_is_local():
    results = by_key(parse_fl(load(), fetched_at=AT))
    amend = results["FL LOCAL partisan-election-of-members-of-district-school-boards"]
    assert amend.key.level is Level.LOCAL
    assert {c.name for c in amend.candidates} == {"Yes For Approval", "No For Rejection"}


def test_fetch_posts_extract_form(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ResultsExtract.Asp")
        assert b"ElectionDate=11%2F5%2F2024" in request.content
        return httpx.Response(200, text=load())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await FlSos().fetch([parse("FL SEN")], client)

    got = by_key(__import__("asyncio").run(go()))
    assert set(got) == {"FL SEN"}
    assert got["FL SEN"].source == "fl_sos"


def test_empty_text_is_not_an_error():
    assert parse_fl("", fetched_at=AT) == []


def test_precincts_are_counted_once_per_county_not_once_per_candidate():
    import csv as _csv

    rows = list(_csv.DictReader(load().splitlines(), delimiter="\t"))
    expected = sum({r["CountyCode"]: int(r["Precincts"]) for r in rows if r["RaceCode"] == "USS"}.values())
    sen = by_key(parse_fl(load(), fetched_at=AT))["FL SEN"]
    assert sen.precincts_total == expected
