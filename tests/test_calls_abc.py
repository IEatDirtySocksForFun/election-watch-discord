import json
from pathlib import Path

import httpx
import pytest
import respx

from electionwatch.models import Level, RaceKey
from electionwatch.sources.calls.abc import (
    ABCSource,
    extract_abc_config,
    parse_abc_state_page,
)

FIX = Path(__file__).parent / "fixtures" / "abc"


def _load(name: str) -> str:
    return (FIX / name).read_text()


def test_parse_senate_call():
    calls = parse_abc_state_page(_load("2024-tx-senate.html"))
    assert len(calls) == 1
    call = calls[0]
    assert call.key == RaceKey("TX", Level.SENATE, "SEN")
    assert call.winner == "Ted Cruz"
    assert call.party == "REP"
    assert call.outlet == "ABC"
    assert call.called_at.isoformat().startswith("2025-01-14")


def test_parse_house_call():
    calls = parse_abc_state_page(_load("2024-tx-house.html"))
    assert len(calls) == 1
    call = calls[0]
    assert call.key == RaceKey("TX", Level.HOUSE, "HOUSE", "38")
    assert call.winner == "Wesley Hunt"
    assert call.party == "REP"


def test_missing_config_raises():
    with pytest.raises(ValueError):
        extract_abc_config("<html>no data</html>")


def test_extract_accepts_double_quotes():
    config = {"page": {"content": {}}}
    html = '<script>window["__abcnews__"]=' + json.dumps(config) + "</script>"
    assert extract_abc_config(html) == config


def test_metadata():
    assert ABCSource.name == "abc"
    assert ABCSource.scrape is True
    assert ABCSource.enabled_by_default is False
    assert ABCSource.env == ()


@respx.mock
async def test_fetch_calls_follows_the_widget_redirect():
    key = RaceKey("TX", Level.SENATE, "SEN")
    url = ABCSource()._url(key)
    assert url.startswith("https://abcnews.com/")  # abcnews.go.com answers 301 to here
    moved = url.replace("://abcnews.com", "://www.abcnews.com")  # in case ABC moves it again
    respx.get(url).mock(return_value=httpx.Response(301, headers={"Location": moved}))
    respx.get(moved).mock(return_value=httpx.Response(200, text=_load("2024-tx-senate.html")))
    async with httpx.AsyncClient() as client:  # no follow_redirects on the client: the adapter asks
        calls = await ABCSource().fetch_calls([key], client)
    assert [call.key for call in calls] == [key]
