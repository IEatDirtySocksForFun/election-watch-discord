"""Wikipedia polling-table adapter, tested against a trimmed real article."""

import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from electionwatch.racekeys import parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.polls import wikipedia

FIX = Path(__file__).parent / "fixtures" / "wikipedia"


def _article():
    html = (FIX / "north-carolina-senate.html").read_text()
    wikitext = (FIX / "north-carolina-senate.wikitext").read_text()
    return html, wikitext


def _polls(key="NC SEN"):
    html, wikitext = _article()
    return wikipedia.parse_wikipedia_polls(html, wikitext, parse(key), "https://en.wikipedia.org/wiki/2026_Test")


def test_candidate_party_map_comes_from_the_primary_sections():
    _, wikitext = _article()
    by_name, by_last = wikipedia.candidate_parties(wikitext)
    assert by_name["michael whatley"] == "REP"
    assert by_name["don brown"] == "REP"
    assert by_name["roy cooper"] == "DEM"
    assert by_last["cooper"] == ("DEM", 1)


def test_parses_general_election_tables_only():
    polls = _polls()
    assert len(polls) > 20
    assert all(str(poll.key) == "NC SEN" for poll in polls)
    assert all(poll.source == "wikipedia" and poll.url for poll in polls)
    assert all(len(poll.results) >= 2 and poll.end_date for poll in polls)
    # aggregate ("Source of poll aggregation") and predictions tables are not polls
    pollsters = {poll.pollster for poll in polls}
    assert "270toWin" not in pollsters
    assert "Decision Desk HQ" not in pollsters
    # primary-runoff tables live outside the General election section
    assert all(poll.end_date >= date(2025, 7, 1) for poll in polls)


def test_a_partisan_pollster_is_flagged():
    polls = _polls()
    harper = next(poll for poll in polls if poll.pollster == "Harper Polling" and poll.end_date == date(2026, 10, 20))
    assert harper.partisan == "REP" and harper.internal is True
    assert harper.start_date == date(2026, 10, 18)
    assert (harper.sample_size, harper.population) == (600, "LV")
    assert [(poll_result.name, poll_result.party, poll_result.pct) for poll_result in harper.results] == [
        ("Michael Whatley", "REP", 39.0),
        ("Roy Cooper", "DEM", 49.0),
    ]


def test_a_bipartisan_poll_pair_is_not_flagged():
    polls = _polls()
    pair = next(poll for poll in polls if poll.pollster.startswith("Beacon Research"))
    assert pair.partisan is None
    assert pair.pollster == "Beacon Research/ Shaw & Co. Research"
    assert (pair.sample_size, pair.population) == (1005, "RV")


def test_neutral_pollsters_are_not_flagged():
    polls = _polls()
    acti = next(poll for poll in polls if poll.pollster == "ActiVote")
    assert acti.partisan is None
    assert [(poll_result.name, poll_result.pct) for poll_result in acti.results] == [
        ("Michael Whatley", 43.0),
        ("Roy Cooper", 57.0),
    ]


def test_candidate_parties_come_from_the_column_headers():
    polls = _polls()
    assert all(poll_result.party in {"REP", "DEM"} for poll in polls for poll_result in poll.results)


def test_newest_first():
    polls = _polls()
    assert [poll.end_date for poll in polls] == sorted((poll.end_date for poll in polls), reverse=True)
    assert polls[0].pollster == "Quantus Insights"
    assert polls[0].end_date == date(2026, 10, 25)


@pytest.mark.parametrize(
    "race,first",
    [
        ("TX SEN", "2026 United States Senate election in Texas"),
        ("MI GOV", "2026 Michigan gubernatorial election"),
    ],
)
def test_article_titles(race, first):
    assert wikipedia.article_titles(parse(race))[0] == first


def test_special_senate_race_falls_back_to_the_regular_article():
    titles = wikipedia.article_titles(parse("FL SEN S"))
    assert titles[0].endswith("special election in Florida")
    assert titles[1].endswith("election in Florida")
    assert parse("FL SEN S").special is True


def test_unsupported_levels_have_no_article():
    assert wikipedia.article_titles(parse("TX-07")) == []
    assert wikipedia.article_titles(parse("TX AG")) == []


def test_fetch_uses_the_mediawiki_parse_api_and_keeps_the_resolved_title():
    html, wikitext = _article()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "parse": {
                    "title": "2026 United States Senate election in North Carolina",
                    "text": html,
                    "wikitext": wikitext,
                }
            },
        )

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await wikipedia.WikipediaPollSource().fetch_polls([parse("NC SEN")], client)

    polls = asyncio.run(go())
    assert len(seen) == 1
    assert seen[0]["action"] == "parse"
    assert seen[0]["page"] == "2026 United States Senate election in North Carolina"
    assert seen[0]["prop"] == "text|wikitext"
    assert polls and polls[0].url == (
        "https://en.wikipedia.org/wiki/2026_United_States_Senate_election_in_North_Carolina"
    )


def test_fetch_skips_a_missing_article():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": "missingtitle"}})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await wikipedia.WikipediaPollSource().fetch_polls([parse("NC SEN")], client)

    assert asyncio.run(go()) == []


def test_fetch_raises_when_every_request_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await wikipedia.WikipediaPollSource().fetch_polls([parse("NC SEN")], client)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())
