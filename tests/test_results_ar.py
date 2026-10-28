import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results.ar_sos import (
    election_links,
    find_election,
    parse_ar,
    race_key,
)

FIX = Path(__file__).parent / "fixtures" / "ar_sos"
AT = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def data(name="2024_general.json"):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def by_key(name="2024_general.json"):
    return {format_key(r.key): r for r in parse_ar(data(name), fetched_at=AT)}


def test_statewide_and_house_races():
    keys = by_key()
    pres = keys["AR LOCAL us-president"]
    assert pres.key.level is Level.LOCAL
    tops = {c.name: c for c in pres.candidates}
    assert tops["Donald J. Trump/JD Vance"].votes == 760581
    assert tops["Donald J. Trump/JD Vance"].party == "REP"
    assert tops["Kamala D. Harris/Tim Walz"].party == "DEM"
    assert tops["Jill Stein/Rudolph Ware"].party == "GRN"  # "Nevada Green Party"
    assert pres.total_votes == 1184575
    assert pres.turnout_pct == 65.1
    assert pres.registered_voters == 1828129
    assert pres.expected_vote_pct == 100.0

    house = keys["AR-01"]
    assert house.key.level is Level.HOUSE
    assert house.title == "U.S. Congress District 01"
    assert {c.name for c in house.candidates} >= {"Congressman Rick Crawford"}
    assert house.candidates[0].pct is not None


def test_state_legislature_and_locals():
    keys = by_key()
    assert keys["AR SD-01"].key.level is Level.STATE
    assert keys["AR HD-01"].title == "State Representative District 01"
    local = [k for k in keys if k.startswith("AR LOCAL j")]
    assert local and keys[local[0]].key.office == "LOCAL"


def test_unopposed_candidate_party_is_blank():
    keys = by_key()
    blank = [c for r in keys.values() for c in r.candidates if c.party is None]
    assert blank, "expected at least one unlabelled candidate"


def test_new_uuid_format_is_reported_not_guessed():
    with pytest.raises(SourceUnavailable) as exc:
        parse_ar(data("2026_uuid_format.json"), fetched_at=AT)
    assert "no contest names" in str(exc.value)


def test_discovery_from_the_sos_page():
    page = (FIX / "sos_results.html").read_text(encoding="utf-8")
    links = dict((label, eid) for label, eid in election_links(page))
    assert any(label.startswith("2024 General Election") for label in links)
    assert find_election(page, "2024-11-05") == "1846"
    with pytest.raises(SourceUnavailable) as exc:
        find_election(page, "2026-11-03")  # not on the page yet
    assert "does not list" in str(exc.value)
    with pytest.raises(SourceUnavailable):
        find_election(page, "2027-11-02")  # no label configured


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senate")) == "AR SEN"
    assert format_key(race_key("Governor")) == "AR GOV"
    assert format_key(race_key("Lieutenant Governor")) == "AR LTGOV"
    assert format_key(race_key("Secretary of State")) == "AR SOS"
    assert format_key(race_key("Commissioner of State Lands")) == "AR LANDCOM"
    assert format_key(race_key("U.S. Congress District 2")) == "AR-02"
    assert format_key(race_key("State Senate District 8")) == "AR SD-08"
    assert format_key(race_key("State Representative District 100")) == "AR HD-100"
    assert (
        format_key(race_key("Court of Appeals Associate Judge District 06, Position 01"))
        == "AR LOCAL court-of-appeals-associate-judge-district-06-position-01"
    )
