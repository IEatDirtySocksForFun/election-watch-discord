import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import nd_enr

FIX = Path(__file__).parent / "fixtures" / "nd_enr"
AT = datetime(2026, 11, 2, 21, 30, tzinfo=timezone.utc)
UPDATED = datetime(2026, 10, 22, 19, 14, 18, 201367, tzinfo=timezone.utc)
HUMAN = "https://resultsnd.sos.nd.gov/#election=348"
MEASURE = "ND LOCAL constitutional-measure-no-1-relating-to-the-threshold-for-approving-a-constitutional-amendment-from-a-simple-majority-to-sixty-percent"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def general():
    return nd_enr.parse_nd_enr(
        load("2026_general_contests.json"),
        [load("2026_general_results_sw.json"), load("2026_general_results_leg.json")],
        fetched_at=AT,
        url=HUMAN,
    )


def primary():
    return nd_enr.parse_nd_enr(
        load("2026_primary_contests.json"), [load("2026_primary_results_sw.json")], fetched_at=AT
    )


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_general_contests_before_polls_close():
    got = by_key(general())
    assert set(got) == {"ND-AL", "ND SOS", "ND SD-01", "ND HD-01", "ND HD-20 S", MEASURE}
    house = got["ND-AL"]
    assert house.key.level is Level.HOUSE and house.key.district == "AL"
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in house.candidates] == [
        ("Julie Fedorchak", "REP", 0),
        ("Trygve Hammer", "DEM", 0),
        ("Charles Tuttle", "IND", 0),
        ("Helene Neville", "IND", 0),
        ("Write-in", "NPA", 0),
    ]
    assert house.precincts_reporting == 0 and house.precincts_total == 358
    assert house.expected_vote_pct is None
    assert house.total_votes == 0
    assert house.source_updated_at == UPDATED
    assert house.url == HUMAN
    assert house.title == "Representative in Congress"


def test_race_offices_and_write_in_label():
    got = by_key(general())
    secretary = got["ND SOS"]
    assert secretary.key.level is Level.GOVERNOR and secretary.key.office == "SOS"
    assert [candidate.name for candidate in secretary.candidates] == [
        "Michael Howe",
        "Ryan Braunberger",
        "Write-in",
    ]
    assert secretary.candidates[-1].party == "NPA"


def test_measure_choices_carry_no_party():
    measure = by_key(general())[MEASURE]
    assert measure.key.level is Level.LOCAL
    assert [candidate.name for candidate in measure.candidates] == ["Yes", "No"]
    assert [candidate.party for candidate in measure.candidates] == [None, None]
    assert measure.precincts_total == 358


def test_legislative_districts_and_unexpired_seat():
    got = by_key(general())
    senate = got["ND SD-01"]
    assert senate.key.level is Level.STATE and senate.key.office == "SD"
    assert senate.key.district == "01"
    assert senate.precincts_total == 7
    house = got["ND HD-01"]  # two seats, five names on the ballot
    assert house.key.office == "HD"
    assert len(house.candidates) == 5
    unexpired = got["ND HD-20 S"]
    assert unexpired.key.special is True
    assert unexpired.key.district == "20"


def test_race_key_mapping():
    assert format_key(nd_enr.race_key("Representative in Congress")) == "ND-AL"
    assert format_key(nd_enr.race_key("Secretary of State")) == "ND SOS"
    assert format_key(nd_enr.race_key("State Senator District 01")) == "ND SD-01"
    assert format_key(nd_enr.race_key("State Representative District 01")) == "ND HD-01"
    assert format_key(nd_enr.race_key("State Representative Unexpired 2-Year Term District 20")) == "ND HD-20 S"


def test_primary_votes_percentages_and_winners():
    got = by_key(primary())
    measure = next(result for result in primary() if "single-subject" in str(result.key))
    yes, no = measure.candidates
    assert (yes.name, yes.votes, yes.pct, yes.winner) == ("Yes", 77141, 66.42, True)
    assert (no.name, no.votes, no.pct, no.winner) == ("No", 38995, 33.58, False)
    assert measure.total_votes == 116136
    assert measure.precincts_reporting == measure.precincts_total == 358
    assert measure.expected_vote_pct == 100.0
    court = got["ND LOCAL justice-of-the-supreme-court"]
    assert [candidate.winner for candidate in court.candidates] == [True, True, False]  # two seats
    assert court.candidates[2].name == "Write-in" and court.candidates[2].votes == 403


def test_pick_election_from_the_live_list():
    listed = load("2026_elections.json")
    assert nd_enr.pick_election(listed, "2026-11-03") == "348"
    assert nd_enr.pick_election(listed, "2026-06-09") == "346"
    assert nd_enr.pick_election(listed, "2028-11-07") is None
    assert nd_enr.pick_election({}, "2026-11-03") is None


@pytest.mark.parametrize("payload", [None, {}, [], "not json", {"response": {"contests": []}}])
def test_bad_contest_list_returns_nothing(payload):
    assert nd_enr.parse_nd_enr(payload, [], fetched_at=AT) == []


def test_results_without_a_described_contest_are_skipped():
    contests = load("2026_general_contests.json")
    results = load("2026_general_results_sw.json")
    unknown = dict(results["response"]["contests"]["22056"])
    unknown["contestID"] = "99999"
    unknown["choices"] = [dict(choice, choiceID="1", totalVotes=7) for choice in unknown["choices"]]
    results["response"]["contests"]["99999"] = unknown
    parsed = nd_enr.parse_nd_enr(contests, [results], fetched_at=AT)
    assert {format_key(result.key) for result in parsed} == {"ND-AL", "ND SOS", MEASURE}
    assert by_key(parsed)["ND SOS"].candidates[0].votes == 0


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_reads_contest_list_then_each_type(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.resultsnd.sos.nd.gov"
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("GetContestSearchList"):
            return httpx.Response(200, json=load("2026_general_contests.json"))
        contest_type = request.url.params["contestType"]
        return httpx.Response(200, json=load(f"2026_general_results_{contest_type.lower()}.json"))

    async def go():
        async with client(handler) as http_client:
            return await nd_enr.NdEnr().fetch([parse("ND SEN"), parse("ND-AL")], http_client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"ND-AL"}
    assert got["ND-AL"].source == "nd_enr"
    assert seen[0][0].endswith("/Contest/GetContestSearchList")
    assert seen[0][1]["electionID"] == "348" and seen[0][1]["cid"] == "north-dakota"
    assert {params["contestType"] for _, params in seen[1:]} == {"SW", "LEG"}


def test_fetch_keeps_answering_when_one_type_fails(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("GetContestSearchList"):
            return httpx.Response(200, json=load("2026_general_contests.json"))
        if request.url.params["contestType"] == "LEG":
            return httpx.Response(500, text="down")
        return httpx.Response(200, json=load("2026_general_results_sw.json"))

    async def go():
        async with client(handler) as http_client:
            return await nd_enr.NdEnr().fetch([parse("ND-AL"), parse("ND SD-01")], http_client)

    assert {format_key(result.key) for result in asyncio.run(go())} == {"ND-AL"}


def test_fetch_reports_a_dead_feed(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async def go():
        async with client(handler) as http_client:
            await nd_enr.NdEnr().fetch([parse("ND-AL")], http_client)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


def test_list_races_reads_the_contest_list_only(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("GetContestSearchList")
        return httpx.Response(200, json=load("2026_general_contests.json"))

    async def go():
        async with client(handler) as http_client:
            return await nd_enr.NdEnr().list_races("ND", http_client)

    rows = asyncio.run(go())
    assert {format_key(key) for key, _ in rows} == {"ND-AL", "ND SOS", "ND SD-01", "ND HD-01", "ND HD-20 S", MEASURE}
    assert dict((format_key(key), title) for key, title in rows)["ND SD-01"] == "State Senator District 01"


def test_fetch_for_another_state_is_a_no_op(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("no request expected")

    async def go():
        async with client(handler) as http_client:
            return await nd_enr.NdEnr().fetch([parse("SD SEN")], http_client)

    assert asyncio.run(go()) == []
