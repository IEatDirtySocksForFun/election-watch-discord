import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.racekeys import parse as parse_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import ma_pd43
from electionwatch.sources.results.ma_pd43 import parse_ma, race_key

FIX = Path(__file__).parent / "fixtures" / "ma_pd43"
AT = datetime(2026, 11, 22, 5, 30, tzinfo=timezone.utc)
URL = "https://electionstats.state.ma.us/elections/search/date:2026-11-03"


def load() -> str:
    return (FIX / "2026_general_search.html").read_text(encoding="utf-8")


def parse():
    return parse_ma(load(), fetched_at=AT)


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_keys_cover_statewide_federal_legislature_and_local():
    got = by_key(parse())
    assert set(got) == {
        "MA SEN",
        "MA GOV",
        "MA AG",
        "MA-01",
        "MA-09",
        "MA SD-berkshire-hampden-franklin-hampshire",
        "MA SD-cape-and-islands",
        "MA HD-1st-barnstable",
        "MA HD-barnstable-dukes-and-nantucket",
        "MA LOCAL governors-council-1st",
        "MA LOCAL district-attorney-suffolk",
        "MA LOCAL register-of-probate-middlesex-county",
        "MA LOCAL county-commissioner-barnstable-county",
        "MA LOCAL sheriff-franklin-county",
        "MA LOCAL county-charter-commission-barnstable",
    }
    assert got["MA SEN"].key.level is Level.SENATE
    assert got["MA GOV"].key.level is Level.GOVERNOR
    assert got["MA-01"].key.level is Level.HOUSE
    assert got["MA SD-cape-and-islands"].key.level is Level.STATE
    assert got["MA LOCAL district-attorney-suffolk"].key.level is Level.LOCAL


def test_senate_counts_and_certified_winner():
    senate = by_key(parse())["MA SEN"]
    assert senate.url == ma_pd43.HUMAN
    markey = senate.candidates[0]
    assert (markey.name, markey.party, markey.votes) == ("Edward J. Markey", "DEM", 1718276)
    assert markey.winner is True
    assert markey.pct == 63.77  # share of the candidates, not of the ballots cast
    deaton = senate.candidates[1]
    assert (deaton.party, deaton.winner) == ("REP", False)
    assert senate.total_votes == 2749609  # the page's "Total Votes Cast"


def test_ballot_statistics_are_not_candidates():
    senate = by_key(parse())["MA SEN"]
    names = [candidate.name for candidate in senate.candidates]
    assert names == ["Edward J. Markey", "John Deaton", "Shiva Ayyadurai", "Joe Tache"]
    assert "All Others" not in names
    assert "Blanks" not in names
    assert "Total Votes Cast" not in names


def test_joint_ticket_names_and_house_districts():
    got = by_key(parse())
    assert got["MA GOV"].candidates[0].name == "Healey and Driscoll"
    assert got["MA GOV"].candidates[0].party == "DEM"
    assert got["MA-01"].candidates[0].votes == 180528
    assert got["MA-09"].candidates[0].votes == 218204


def test_named_legislative_districts_become_slugs():
    assert format_key(race_key("State Senate", "Cape and Islands")) == "MA SD-cape-and-islands"
    assert format_key(race_key("State Representative", "1st Barnstable")) == "MA HD-1st-barnstable"
    assert (
        format_key(race_key("State Senate", "Berkshire, Hampden, Franklin & Hampshire"))
        == "MA SD-berkshire-hampden-franklin-hampshire"
    )


def test_other_offices_become_local_races():
    assert format_key(race_key("Governor's Council", "1st")) == "MA LOCAL governors-council-1st"
    assert format_key(race_key("Sheriff", "Franklin County")) == "MA LOCAL sheriff-franklin-county"
    assert parse_key("MA LOCAL sheriff-franklin-county").district == "sheriff-franklin-county"


def test_party_labels_are_canonical():
    sheriff = by_key(parse())["MA LOCAL sheriff-franklin-county"]
    parties = {candidate.name: candidate.party for candidate in sheriff.candidates}
    assert parties["Lori M. Streeter"] == "DEM"
    assert parties["Jon J. Hall"] == "NP"  # "Unenrolled" on the ballot
    assert parties["Gary Edward Gagnon"] == "IND"


def test_titles_name_the_district():
    got = by_key(parse())
    assert got["MA SEN"].title == "U.S. Senate"
    assert got["MA SD-cape-and-islands"].title == "State Senate - Cape and Islands"
    assert got["MA-01"].title == "U.S. House - 1st Congressional"


def test_empty_or_unrelated_payload_is_not_an_error():
    assert parse_ma("", fetched_at=AT) == []
    assert parse_ma("<html><body><p>no results</p></body></html>", fetched_at=AT) == []


def _client(html: str, seen: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=html)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_fetch_requests_the_tracked_date_and_selects_ma_keys(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen: list[str] = []

    async def go():
        async with _client(load(), seen) as client:
            return await ma_pd43.MaPd43().fetch([parse_key("MA SEN"), parse_key("TX SEN")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"MA SEN"}
    assert seen == [URL]


def test_fetch_skips_the_request_for_other_states(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen: list[str] = []

    async def go():
        async with _client(load(), seen) as client:
            return await ma_pd43.MaPd43().fetch([parse_key("TX SEN")], client)

    assert asyncio.run(go()) == []
    assert seen == []


def test_list_races_lists_every_contest(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen: list[str] = []

    async def go():
        async with _client(load(), seen) as client:
            return await ma_pd43.MaPd43().list_races("MA", client)

    races = asyncio.run(go())
    assert len(races) == 15
    assert (parse_key("MA SEN"), "U.S. Senate") in races
    assert seen == [URL]


def test_page_without_contests_is_unavailable(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-04")
    seen: list[str] = []

    async def go():
        async with _client('<html><body><table id="search_results_table"></table></body></html>', seen) as client:
            return await ma_pd43.MaPd43().fetch([parse_key("MA SEN")], client)

    with pytest.raises(SourceUnavailable, match="no contests for 2026-11-04"):
        asyncio.run(go())
    assert seen == ["https://electionstats.state.ma.us/elections/search/date:2026-11-04"]
