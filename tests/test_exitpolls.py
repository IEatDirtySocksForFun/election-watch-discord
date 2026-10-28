import json
from pathlib import Path

import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.sources.exitpolls._common import condense_exit_poll, exit_poll_url
from electionwatch.sources.exitpolls.cbs import CBSExitPollSource
from electionwatch.sources.exitpolls.cnn import CNNExitPollSource
from electionwatch.sources.exitpolls.fox import FOXExitPollSource
from electionwatch.sources.exitpolls.nbc import NBCExitPollSource

FIX = Path(__file__).parent / "fixtures" / "exitpolls"


def test_exit_poll_urls():
    tx_sen = RaceKey("TX", Level.SENATE, "SEN")
    ca_gov = RaceKey("CA", Level.GOVERNOR, "GOV")
    assert exit_poll_url("CNN", tx_sen) == ("https://www.cnn.com/election/2026/exit-polls/tx/senate")
    # NBC and Fox only have a national voter-analysis page.
    assert exit_poll_url("NBC", ca_gov) == ("https://www.nbcnews.com/politics/2026-elections/exit-polls")
    assert exit_poll_url("CBS", ca_gov) == ("https://www.cbsnews.com/elections/2026/ca/governor/exit-poll/")
    assert exit_poll_url("FOX", tx_sen) == ("https://www.foxnews.com/elections/2026/general-results/voter-analysis")


def test_exit_poll_url_none_for_unsupported_race():
    assert exit_poll_url("CNN", RaceKey("TX", Level.STATE, "STATE")) is None
    assert exit_poll_url("UNKNOWN", RaceKey("TX", Level.SENATE, "SEN")) is None


def test_condense_exit_poll_demographics():
    payload = json.loads((FIX / "cnn.json").read_text())
    summary = condense_exit_poll(payload)
    assert summary is not None
    assert "Gender: Men 55% Rep, Women 52% Dem" in summary
    assert "Age" in summary
    assert "Education" in summary
    # Only top-3 demographics are included.
    assert "Race" not in summary


def test_condense_unknown_format_returns_none():
    assert condense_exit_poll({"foo": "bar"}) is None


@pytest.mark.parametrize("source", [CNNExitPollSource, NBCExitPollSource, CBSExitPollSource, FOXExitPollSource])
def test_exit_poll_source_metadata(source):
    assert source.scrape is True
    assert source.enabled_by_default is False


@pytest.mark.asyncio
async def test_fetch_exit_polls_returns_a_link_per_race_on_election_night(monkeypatch):
    import httpx

    from electionwatch.sources.exitpolls import _common

    monkeypatch.setattr(
        _common,
        "_utcnow",
        lambda: __import__("datetime").datetime(2026, 11, 4, 1, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        polls = await CNNExitPollSource().fetch_exit_polls([RaceKey("TX", Level.SENATE, "SEN")], client)
    assert [(p.outlet, p.summary) for p in polls] == [("CNN", None)]
    assert polls[0].url.startswith("https://www.cnn.com/election/2026/exit-polls/")


@pytest.mark.asyncio
async def test_no_exit_poll_links_before_election_day(monkeypatch):
    import httpx

    from electionwatch.sources.exitpolls import _common

    monkeypatch.setattr(
        _common,
        "_utcnow",
        lambda: __import__("datetime").datetime(2026, 10, 27, 12, 0, tzinfo=__import__("datetime").timezone.utc),
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        polls = await CNNExitPollSource().fetch_exit_polls([RaceKey("TX", Level.SENATE, "SEN")], client)
    assert polls == []
