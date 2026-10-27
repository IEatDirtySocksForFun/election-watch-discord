import json
from pathlib import Path

import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.sources.calls.abc import ABCSource, parse_abc_calls
from electionwatch.sources.calls.cbs import CBSSource, parse_cbs_calls
from electionwatch.sources.calls.ddhq import DDHQSource, parse_ddhq_calls
from electionwatch.sources.calls.fox import FOXSource, parse_fox_calls
from electionwatch.sources.calls.nbc import NBCSource, parse_nbc_calls
from electionwatch.sources.calls.nyt import NYTSource, parse_nyt_calls
from electionwatch.sources.calls.votehub import VoteHubSource, parse_votehub_calls

CASES = [
    ("ddhq", DDHQSource, parse_ddhq_calls),
    ("votehub", VoteHubSource, parse_votehub_calls),
    ("cbs", CBSSource, parse_cbs_calls),
    ("fox", FOXSource, parse_fox_calls),
    ("nbc", NBCSource, parse_nbc_calls),
    ("abc", ABCSource, parse_abc_calls),
    ("nyt", NYTSource, parse_nyt_calls),
]


def _load(outlet: str):
    return json.loads(
        (Path(__file__).parent / "fixtures" / outlet / "calls.json").read_text()
    )


@pytest.mark.parametrize("outlet,source,parse", CASES)
def test_parse_skeleton_fixture(outlet, source, parse):
    payload = _load(outlet)
    calls = parse(payload)
    assert len(calls) == 2
    tx_sen = next(c for c in calls if c.key == RaceKey("TX", Level.SENATE, "SEN"))
    ca_house = next(c for c in calls if c.key == RaceKey("CA", Level.HOUSE, "HOUSE", "12"))
    assert tx_sen.outlet == source.outlet
    assert tx_sen.winner == "Ted Cruz"
    assert tx_sen.party == "REP"
    assert ca_house.winner == "Lateefah Simon"
    assert ca_house.party == "DEM"


@pytest.mark.parametrize("outlet,source,parse", CASES)
def test_scrape_flags(outlet, source, parse):
    if outlet == "ddhq":
        assert source.scrape is False
        assert source.enabled_by_default is True
        assert source.env == ("DDHQ_API_KEY",)
    else:
        assert source.scrape is True
        assert source.enabled_by_default is False
        assert source.env == ()
