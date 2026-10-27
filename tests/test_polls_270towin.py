"""270toWin poll-list adapter, tested against trimmed real fragments."""
import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.polls import two70win

FIX = Path(__file__).parent / "fixtures" / "two70win"


def _fixture(name: str) -> str:
    return (FIX / name).read_text()


def test_senate_rows_are_mapped_to_races():
    polls = two70win.parse_270towin_polls(
        _fixture("senate-polls.html"), Level.SENATE, [parse("ME SEN"), parse("TX SEN"), parse("MI SEN")]
    )
    assert len(polls) == 4  # the fixture also holds a North Carolina row we did not ask for
    assert all(p.key.level is Level.SENATE and p.source == "270towin" for p in polls)
    # newest first
    assert [p.end_date for p in polls] == sorted((p.end_date for p in polls), reverse=True)

    marist = next(p for p in polls if p.pollster == "Marist Poll")
    assert marist.key == parse("ME SEN")
    assert marist.end_date == date(2026, 10, 27)
    assert marist.start_date is None  # one date column -> no invented field period
    assert (marist.sample_size, marist.population) == (1449, "LV")
    assert marist.url.startswith("https://maristpoll.marist.edu/")
    assert [(r.name, r.party, r.pct) for r in marist.results] == [
        ("Jackson", "DEM", 49.0),
        ("Collins", "REP", 47.0),  # "(incumbent)" star stripped from the surname
    ]
    assert marist.partisan is None and marist.sponsor is None


def test_house_districts_and_at_large():
    polls = two70win.parse_270towin_polls(
        _fixture("house-polls.html"), Level.HOUSE,
        [parse("CA-34"), parse("AK-AL"), parse("MI-07"), parse("TX-15")],
    )
    by_key = {str(p.key): p for p in polls}
    assert set(by_key) == {"CA-34", "AK-AL", "MI-07", "TX-15"}
    assert by_key["AK-AL"].key.district == "AL"
    assert by_key["TX-15"].key.district == "15"
    assert by_key["TX-15"].sample_size is None  # site lists no sample for that row
    assert by_key["TX-15"].population == "LV"
    ak = by_key["AK-AL"]
    assert [(r.name, r.party) for r in ak.results] == [
        ("Begich", "REP"), ("Hill", "IND"), ("Hafner", "DEM"), ("McDermott", "LIB"),
    ]


def test_unrequested_races_are_dropped():
    polls = two70win.parse_270towin_polls(
        _fixture("senate-polls.html"), Level.SENATE, [parse("GA SEN")]
    )
    assert polls == []


def test_non_general_rows_are_skipped():
    html = _fixture("senate-polls.html").replace("General Election", "Republican Primary")
    polls = two70win.parse_270towin_polls(html, Level.SENATE, [parse("ME SEN")])
    assert polls == []


def test_wrong_level_keys_are_ignored():
    polls = two70win.parse_270towin_polls(
        _fixture("senate-polls.html"), Level.GOVERNOR, [parse("ME GOV")]
    )
    assert polls == []


def test_fetch_asks_once_per_office_with_the_documented_parameters():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=_fixture("senate-polls.html"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await two70win.Two70toWinPollSource().fetch_polls(
                [parse("TX SEN"), parse("MI GOV")], client
            )

    polls = asyncio.run(go())
    assert len(seen) == 2  # one senate list, one governor list
    assert seen[0]["election_year"] == "2026"
    assert seen[0]["limit"] == "40"
    assert seen[0]["election_types[]"] == "S"
    assert seen[1]["election_types[]"] == "G"
    assert {str(p.key) for p in polls} == {"TX SEN"}


def test_fetch_raises_when_every_list_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await two70win.Two70toWinPollSource().fetch_polls([parse("TX SEN")], client)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())
