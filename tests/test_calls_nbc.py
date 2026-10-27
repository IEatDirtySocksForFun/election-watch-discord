import json
from pathlib import Path

from electionwatch.models import Level, RaceKey
from electionwatch.sources.calls.nbc import NBCSource, parse_nbc_results

FIX = Path(__file__).parent / "fixtures" / "nbc"


def _load():
    return json.loads((FIX / "2024-results.json").read_text())


def test_parse_state_and_district_calls():
    calls = parse_nbc_results(_load())
    by_key = {c.key: c for c in calls}

    tx_sen = by_key[RaceKey("TX", Level.SENATE, "SEN")]
    assert tx_sen.winner == "Ted Cruz"
    assert tx_sen.party == "REP"
    assert tx_sen.outlet == "NBC"

    az_sen = by_key[RaceKey("AZ", Level.SENATE, "SEN")]
    assert az_sen.winner == "Ruben Gallego"
    assert az_sen.party == "DEM"

    tx_38 = by_key[RaceKey("TX", Level.HOUSE, "HOUSE", "38")]
    assert tx_38.winner == "Wesley Hunt"
    assert tx_38.party == "REP"

    de_gov = by_key[RaceKey("DE", Level.GOVERNOR, "GOV")]
    assert de_gov.winner == "Matt Meyer"


def test_uncalled_entries_are_skipped():
    payload = _load()
    payload["mapData"]["TX"]["tooltip"]["candidates"] = [
        {"name": "Ted Cruz", "party": "Rep", "isWinner": False}
    ]
    calls = parse_nbc_results(payload)
    assert all(c.key != RaceKey("TX", Level.SENATE, "SEN") for c in calls)


def test_unknown_office_is_ignored():
    payload = {
        "mapData": {
            "TX": {
                "tooltip": {
                    "officeCode": "P",
                    "raceName": "Texas",
                    "candidates": [{"name": "X", "party": "Rep", "isWinner": True}],
                }
            }
        }
    }
    assert parse_nbc_results(payload) == []


def test_metadata():
    assert NBCSource.name == "nbc"
    assert NBCSource.scrape is True
    assert NBCSource.enabled_by_default is False
    assert NBCSource.env == ()
