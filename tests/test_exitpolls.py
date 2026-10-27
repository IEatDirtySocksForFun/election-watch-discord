import json
from pathlib import Path

import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.sources.exitpolls.cbs import CBSExitPollSource
from electionwatch.sources.exitpolls.cnn import CNNExitPollSource
from electionwatch.sources.exitpolls._common import condense_exit_poll, exit_poll_url
from electionwatch.sources.exitpolls.fox import FOXExitPollSource
from electionwatch.sources.exitpolls.nbc import NBCExitPollSource

FIX = Path(__file__).parent / "fixtures" / "exitpolls"


def test_exit_poll_urls():
    tx_sen = RaceKey("TX", Level.SENATE, "SEN")
    ca_gov = RaceKey("CA", Level.GOVERNOR, "GOV")
    assert "cnn.com/election/2026/exit-polls/tx/senate" in exit_poll_url("CNN", tx_sen)
    assert "nbcnews.com/politics/2026-elections/ca-exit-polls" in exit_poll_url("NBC", ca_gov)
    assert "cbsnews.com/election/2026/exit-polls/ca" in exit_poll_url("CBS", ca_gov)
    assert "foxnews.com/elections/2026/general-results/voter-analysis/tx" in exit_poll_url("FOX", tx_sen)


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
