import json
from pathlib import Path

import pytest

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
