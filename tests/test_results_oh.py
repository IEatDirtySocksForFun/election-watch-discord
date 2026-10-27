import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results import oh_sos
from electionwatch.sources.results.oh_sos import parse_oh, race_key

FIX = Path(__file__).parent / "fixtures" / "oh_sos" / "oh"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)

DETAILS = [
    "governor.json", "attorney_general.json", "supreme_court.json", "us_senator_special.json",
    "congress_01.json", "congress_02.json", "state_senate_01.json", "state_senate_03.json",
    "state_rep_01.json", "state_rep_02.json", "issue_3.json",
]


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def by_key(results):
    return {format_key(r.key): r for r in results}


def parsed():
    return parse_oh(load("contest_list.json"), [load(n) for n in DETAILS], fetched_at=AT)


def test_statewide_offices():
    got = by_key(parsed())
    assert got["OH GOV"].candidates[0].party == "DEM"
    assert "Amy Acton" in {c.name for c in got["OH GOV"].candidates}
    assert got["OH AG"].key.level is Level.GOVERNOR
    assert {c.name for c in got["OH AG"].candidates} == {"Keith Faber", "John  J. Kulewicz"}


def test_senate_unexpired_term_is_special():
    senate = by_key(parsed())["OH SEN S"]
    assert senate.key.special is True
    assert senate.title == "U.S. Senator - Unexpired Term Ending 01/03/2029"
    assert senate.candidates[0].name == "Sherrod Brown"
    assert senate.candidates[0].votes == 0


def test_county_rows_are_summed_into_one_race():
    cd1 = by_key(parsed())["OH-01"]
    # Three county rows (Clinton, Hamilton, Warren), three candidates each: not nine.
    assert {c.name for c in cd1.candidates} == {"Eric Conroy", "Greg Landsman", "Nathan Weise"}
    assert cd1.precincts_total == 36 + 563 + 185
    assert cd1.total_votes == 0
    assert cd1.title == "Representative to Congress - District 01"


def test_votes_sum_across_county_rows():
    detail = load("congress_01.json")
    per_row = {"Clinton": 10, "Hamilton": 100, "Warren": 1000}
    for row in detail["contestList"]:
        for cand in row["candidatesRunning"]:
            cand["voteCount"] = str(per_row[row["name"]])
    got = by_key(parse_oh(load("contest_list.json"), [detail], fetched_at=AT))["OH-01"]
    assert {c.name: c.votes for c in got.candidates} == {
        "Eric Conroy": 1110, "Greg Landsman": 1110, "Nathan Weise": 1110,
    }
    assert got.total_votes == 3330
    assert got.candidates[0].pct == 33.33


def test_state_legislature_keys():
    got = by_key(parsed())
    assert got["OH SD-01"].key.level is Level.STATE
    assert got["OH HD-02"].candidates[0].name == "Charity Martin King"
    assert got["OH HD-01"].precincts_total == 889


def test_issue_and_judicial_are_local():
    got = by_key(parsed())
    issue = got["OH LOCAL state-issue-3-to-require-voters-to-present-photo-identification-in-order-to-vote"]
    assert issue.title.startswith("State Issue 3")
    assert [c.name for c in issue.candidates] == ["YES", "NO"]
    assert issue.candidates[0].party is None
    assert "OH LOCAL justice-of-the-supreme-court-term-commencing-01-01-2027" in got


def test_party_codes_are_normalised():
    parties = {c.party for r in parsed() for c in r.candidates}
    # "OP" is Ohio's own "other party" code; unknown codes pass through unchanged.
    assert parties <= {"DEM", "REP", "WRI", "LIB", "GRN", "CON", "IND", "OP", None}


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senator - Unexpired Term Ending 01/03/2029")) == "OH SEN S"
    assert format_key(race_key("U.S. Senator")) == "OH SEN"
    assert format_key(race_key("Governor and Lieutenant Governor")) == "OH GOV"
    assert format_key(race_key("Auditor of State")) == "OH AUD"
    assert format_key(race_key("Representative to Congress - District 11")) == "OH-11"
    assert format_key(race_key("State Senator - District 07")) == "OH SD-07"
    assert format_key(race_key("State Representative - District 99")) == "OH HD-99"
    assert format_key(race_key("State Issue 3 To require photo ID")) == \
        "OH LOCAL state-issue-3-to-require-photo-id"


def test_empty_payloads_are_not_errors():
    assert parse_oh([], [], fetched_at=AT) == []
    assert parse_oh(load("contest_list.json"), [], fetched_at=AT) == []


def test_fetch_requests_only_wanted_contests(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/getContestList"):
            return httpx.Response(200, json=load("contest_list.json"))
        assert request.url.path.endswith("/GetContestDetailsObject")
        name = request.url.params["contestName"]
        wanted = {
            "Governor and Lieutenant Governor": "governor.json",
            "Representative to Congress": "congress_01.json",
        }[name]
        return httpx.Response(200, json=load(wanted))

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await oh_sos.OhSos().fetch([parse("OH GOV"), parse("OH-01")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"OH GOV", "OH-01"}
    assert seen[0].endswith("/getContestList")
    assert len(seen) == 3            # list + two detail calls, nothing else
