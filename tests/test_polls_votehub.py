"""VoteHub polling-API adapter, tested against a trimmed real response."""
import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.polls import votehub

FIX = Path(__file__).parent / "fixtures" / "votehub_polls"


def _payload():
    return json.loads((FIX / "polls.json").read_text())


def _polls(*keys):
    return votehub.parse_votehub_polls(_payload(), [parse(k) for k in keys])


def test_maps_a_partisan_poll_to_the_race():
    polls = _polls("MI SEN")
    assert len(polls) == 1
    poll = polls[0]
    assert str(poll.key) == "MI SEN"
    assert poll.source == "votehub"
    assert poll.pollster == "The Tyson Group"
    assert poll.end_date == date(2026, 9, 27)
    assert poll.start_date == date(2026, 9, 24)
    assert poll.sample_size == 600
    assert poll.population == "LV"
    assert poll.partisan == "REP" and poll.internal is True
    assert poll.sponsor == "Alliance for a Better Future"
    assert poll.url.startswith("https://www.breitbart.com/")
    assert [(r.name, r.party, r.pct) for r in poll.results] == [
        ("Abdul El-Sayed", None, 44.0),
        ("Mike Rogers", None, 43.0),
    ]


def test_four_way_poll_keeps_every_candidate():
    poll = _polls("OH SEN")[0]
    assert poll.partisan is None and poll.internal is False
    assert [r.name for r in poll.results] == [
        "Sherrod Brown", "Jon Husted", "Bill Redpath", "Greg Levy",
    ]


def test_house_district_key():
    poll = _polls("IL-04")[0]
    assert poll.key == parse("IL-04")
    assert poll.key.level is Level.HOUSE and poll.key.district == "04"
    assert poll.sponsor == "The Independent Center"
    assert poll.pollster == "The Bullfinch Group"


def test_governor_key():
    poll = _polls("IA GOV")[0]
    assert str(poll.key) == "IA GOV"
    assert (poll.start_date, poll.end_date) == (date(2026, 10, 6), date(2026, 10, 12))
    assert poll.sample_size == 2245


def test_only_requested_races_are_returned():
    assert {str(p.key) for p in _polls("TX SEN")} == {"TX SEN"}
    assert {str(p.key) for p in _polls("TX SEN", "NC SEN")} == {"TX SEN", "NC SEN"}


def test_non_race_polls_are_ignored():
    """Generic ballot ("2026") and approval polls ("JD Vance") are not races."""
    polls = votehub.parse_votehub_polls(_payload(), [parse("TX SEN")])
    assert all(p.key.state == "TX" for p in polls)
    assert len(polls) == 1


def test_newest_first():
    polls = _polls("TX SEN", "NC SEN", "MI SEN", "IA GOV", "IL-04", "OH SEN")
    end_dates = [p.end_date for p in polls]
    assert end_dates == sorted(end_dates, reverse=True)
    assert len(polls) == 6


def test_party_word_answers_get_a_normalised_party():
    assert votehub._result("Dem", 54.0).party == "DEM"
    assert votehub._result("Rep", 45.0).party == "REP"
    assert votehub._result("Undecided", 3.0).party is None
    assert votehub._result("Abdul El-Sayed", 44.0).party is None


@pytest.mark.parametrize("subject,expected", [
    ("2026 Michigan", ("MI", None)),
    ("2026 North Carolina", ("NC", None)),
    ("2026 IL-04", ("IL", "04")),
    ("2026 AK-01", ("AK", "AL")),   # single-district state -> at-large race key
    ("2026-10-27", None),
    ("2026", None),
    ("JD Vance", None),
])
def test_parse_subject(subject, expected):
    assert votehub.parse_subject(subject) == expected


def test_fetch_hits_the_api_once_and_parses():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_payload())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            src = votehub.VoteHubPollSource()
            first = await src.fetch_polls([parse("TX SEN")], client)
            second = await src.fetch_polls([parse("MI SEN")], client)  # cached payload
            return first, second

    first, second = asyncio.run(go())
    assert calls == ["https://api.votehub.com/polls"]
    assert {str(p.key) for p in first} == {"TX SEN"}
    assert {str(p.key) for p in second} == {"MI SEN"}


def test_fetch_raises_when_the_endpoint_is_dead():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await votehub.VoteHubPollSource().fetch_polls([parse("TX SEN")], client)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


def test_fetch_without_keys_does_not_call_the_api():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await votehub.VoteHubPollSource().fetch_polls([], client)

    assert asyncio.run(go()) == []


async def test_payload_cache_expires_and_html_is_source_unavailable(monkeypatch):
    import httpx
    import pytest

    from electionwatch.sources.base import SourceUnavailable
    from electionwatch.sources.polls import votehub

    hits = []

    def handler(request):
        hits.append(1)
        return httpx.Response(200, json=[]) if len(hits) < 3 else httpx.Response(200, text="<html>")

    clock = [1000.0]
    monkeypatch.setattr(votehub.time, "monotonic", lambda: clock[0])
    source = votehub.VoteHubPollSource()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await source._payload(client)
        await source._payload(client)
        assert len(hits) == 1
        clock[0] += source.CACHE_SECONDS
        await source._payload(client)
        assert len(hits) == 2
        clock[0] += source.CACHE_SECONDS
        with pytest.raises(SourceUnavailable):
            await source._payload(client)
