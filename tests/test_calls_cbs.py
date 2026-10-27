import json
from pathlib import Path

from electionwatch.models import Level, RaceKey
from electionwatch.sources.calls.cbs import CBSSource, parse_cbs_races

FIX = Path(__file__).parent / "fixtures" / "cbs"


def _load():
    return json.loads((FIX / "races2-2024-tx.json").read_text())


def test_parse_senate_and_house_calls():
    calls = parse_cbs_races(_load())
    by_key = {c.key: c for c in calls}

    tx_sen = by_key[RaceKey("TX", Level.SENATE, "SEN")]
    assert tx_sen.winner == "Ted Cruz"
    assert tx_sen.party == "REP"
    assert tx_sen.outlet == "CBS"
    assert tx_sen.called_at.isoformat().startswith("2025-01-14")

    tx_38 = by_key[RaceKey("TX", Level.HOUSE, "HOUSE", "38")]
    assert tx_38.winner == "Wesley Hunt"


def test_non_list_payload_returns_empty():
    assert parse_cbs_races({"races": []}) == []


def test_unknown_office_is_ignored():
    calls = parse_cbs_races(
        [
            {
                "stateCode": "TX",
                "office": "P",
                "candidates": [{"fullName": "X", "party": "REP", "isWinner": True}],
            }
        ]
    )
    assert calls == []


def test_metadata():
    assert CBSSource.name == "cbs"
    assert CBSSource.scrape is True
    assert CBSSource.enabled_by_default is False
    assert CBSSource.env == ()
