from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.hi_soe import _parse_name, _rows, parse_hi, race_key

FIX = Path(__file__).parent / "fixtures" / "hi_soe" / "2024-general.txt"
AT = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def load():
    with FIX.open(encoding="utf-8") as fh:
        return fh.read()


def by_key(text):
    return {format_key(race_result.key): race_result for race_result in parse_hi(_rows(text), fetched_at=AT)}


def test_race_keys():
    assert format_key(race_key("President and Vice President")) == "HI LOCAL us-president"
    assert format_key(race_key("U.S.\nSenator")) == "HI SEN"
    assert format_key(race_key("U.S. Representative, Dist I")) == "HI-01"
    assert format_key(race_key("U.S. Representative, Dist II")) == "HI-02"
    assert format_key(race_key("State Senator, Dist 3")) == "HI SD-03"
    assert format_key(race_key("State Representative, Dist 50")) == "HI HD-50"
    assert format_key(race_key("Question #1")) == "HI LOCAL question-1"


def test_parse_name():
    assert _parse_name("(D) HIRONO, Mazie K.") == ("HIRONO, Mazie K.", "DEM")
    assert _parse_name("(R) TRUMP, Donald J.\nFor PRESIDENT\nVANCE, JD")[0] == "TRUMP, Donald J."
    assert _parse_name("(W) ULUFANUA, Christian P.") == ("ULUFANUA, Christian P.", "WRI")


def test_federal_results():
    keys = by_key(load())
    assert "HI LOCAL us-president" in keys
    assert "HI SEN" in keys
    assert "HI-01" in keys
    assert "HI-02" in keys
    pres = keys["HI LOCAL us-president"]
    assert pres.candidates[0].name == "Kamala D. Harris"
    assert pres.candidates[0].party == "DEM"
    assert pres.candidates[0].votes == 313044
    assert pres.total_votes == sum(candidate.votes for candidate in pres.candidates)


def test_state_legislature_results():
    keys = by_key(load())
    assert "HI SD-03" in keys
    assert "HI HD-50" in keys
    sen = keys["HI SD-03"]
    assert sen.key.level is Level.STATE
    assert sen.candidates[0].votes == 13112


def test_ballot_measures_and_local():
    keys = by_key(load())
    assert "HI LOCAL question-1" in keys
    assert "HI LOCAL mayor" in keys
    assert keys["HI LOCAL question-1"].candidates[0].name == "Yes"


def test_precincts_and_registration():
    race_result = by_key(load())["HI LOCAL us-president"]
    assert race_result.precincts_reporting == 497
    assert race_result.precincts_total == 497
    assert race_result.registered_voters == 860868


def test_governor_and_lieutenant_governor_are_statewide_keys():
    from electionwatch.racekeys import parse as parse_key
    from electionwatch.sources.results.hi_soe import race_key

    assert race_key("GOVERNOR") == parse_key("HI GOV")
    assert race_key("LIEUTENANT GOVERNOR") == parse_key("HI LTGOV")


def test_utf16_summary_decodes():
    from electionwatch.sources.results._common import decode_text
    from electionwatch.sources.results.hi_soe import _rows

    raw = '"Format#1"\r\n1,"U.S. Senator",1,0,,0,0,0,0,0,0,900000,200,200,1,"HIRONO, Mazie",2,,1,2,3\r\n'
    rows = _rows(decode_text(b"\xff\xfe" + raw.encode("utf-16-le")))
    assert rows[0]["office"] == "U.S. Senator"
