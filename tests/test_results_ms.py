from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.results.ms_sos import parse_ms, race_key

FIX = Path(__file__).parent / "fixtures" / "ms_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    text = (FIX / "2024_general_recaps.csv").read_text(encoding="utf-8-sig")
    return parse_ms(text, fetched_at=AT)


def by_key():
    return {format_key(r.key): r for r in parse()}


def test_senate_uses_state_total_not_county_sum():
    r = by_key()["MS SEN"]
    assert r.key.level is Level.SENATE
    assert r.candidates[0].name == "Roger F. Wicker"
    assert r.candidates[0].party == "REP"
    assert r.candidates[0].votes == 761934          # State Total column
    assert r.candidates[1].votes == 450749
    assert r.total_votes == 1212683


def test_house_district_and_president_ticket_names():
    keys = by_key()
    assert keys["MS-01"].candidates[0].name == "Trent Kelly"
    president = keys["MS LOCAL us-president"]
    assert president.key.level is Level.LOCAL
    names = [c.name for c in president.candidates]
    assert "Donald J. Trump / JD Vance" in names     # elector prose reduced to the ticket
    assert president.candidates[0].pct == 60.91


def test_court_race_becomes_local():
    r = by_key()["MS LOCAL southern-district-supreme-court-district-2-southern-position-2"]
    assert r.key.level is Level.LOCAL
    assert r.candidates[0].name == "Dawn H. Beam"
    assert r.candidates[0].party is None


def test_race_key_mapping():
    assert format_key(race_key("United States-Senate")) == "MS SEN"
    assert format_key(race_key("United States-President")) == "MS LOCAL us-president"
    assert format_key(race_key("US House Of Rep 02-2nd Congressional District")) == "MS-02"
    assert format_key(race_key("State Senate 05-Fifth Senatorial District")) == "MS SD-05"
    assert format_key(race_key("State House Of Rep 12-Twelfth Representative District")) == "MS HD-12"
    assert format_key(race_key("Court Of Appeals 03-District 3  Position 1")) == \
        "MS LOCAL court-of-appeals-03-district-3-position-1"


def test_empty_payload_is_not_an_error():
    assert parse_ms("", fetched_at=AT) == []
