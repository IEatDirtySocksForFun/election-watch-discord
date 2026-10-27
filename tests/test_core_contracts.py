from datetime import datetime, timedelta, timezone

import pytest

from electionwatch.models import CandidateResult, Level, RaceKey, RaceResult
from electionwatch.racekeys import RaceKeyError, describe, parse
from electionwatch.settings import GuildSettings, PostMode, post_due
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,canon,name", [
    ("NC-01", "NC-01", "North Carolina Congressional District 1"),
    ("nc-1", "NC-01", "North Carolina Congressional District 1"),
    ("AK-AL", "AK-AL", "Alaska At-Large Congressional District"),
    ("TX SEN", "TX SEN", "Texas Senate"),
    ("TX-SEN", "TX SEN", "Texas Senate"),
    ("OH SEN S", "OH SEN S", "Ohio Senate (Special)"),
    ("GA GOV", "GA GOV", "Georgia Governor"),
    ("TX SD-5", "TX SD-05", "Texas State Senate District 5"),
    ("TX HD 121", "TX HD-121", "Texas State House District 121"),
    ("TX LOCAL Harris County Judge", "TX LOCAL harris-county-judge", "Texas local: harris county judge"),
    ("Texas Senate", "TX SEN", "Texas Senate"),
    ("North Carolina Congressional District 1", "NC-01", "North Carolina Congressional District 1"),
    ("New York Governor", "NY GOV", "New York Governor"),
])
def test_parse_roundtrip(text, canon, name):
    k = parse(text)
    assert str(k) == canon
    assert parse(canon) == k
    assert describe(k) == name


@pytest.mark.parametrize("bad", ["", "ZZ SEN", "TX", "TX FOO", "TX LOCAL", "TX SEN X"])
def test_parse_rejects(bad):
    with pytest.raises(RaceKeyError):
        parse(bad)


def test_post_due_rule():
    gs = GuildSettings(guild_id=1)
    assert not post_due(gs, None, NOW, updated=False)
    assert post_due(gs, None, NOW, updated=True)
    assert not post_due(gs, NOW - timedelta(minutes=4), NOW, True)
    assert post_due(gs, NOW - timedelta(minutes=5), NOW, True)
    gs.post_mode, gs.interval_minutes = PostMode.INTERVAL, 15
    assert not post_due(gs, NOW - timedelta(minutes=10), NOW, True)
    assert post_due(gs, NOW - timedelta(minutes=15), NOW, True)
    gs.post_mode = PostMode.IMMEDIATE
    assert post_due(gs, NOW - timedelta(seconds=1), NOW, True)
    gs.paused = True
    assert not post_due(gs, None, NOW, True)


async def test_memory_store_change_detection():
    s = MemoryStore()
    key = parse("TX SEN")
    r = RaceResult(key, [CandidateResult("A", "REP", 10), CandidateResult("B", "DEM", 30)],
                   "test", NOW).finalize()
    assert r.candidates[0].name == "B" and r.candidates[0].pct == 75.0
    assert await s.upsert_result(r) is True
    assert await s.upsert_result(r) is False
    r.candidates[1].votes += 1
    assert await s.upsert_result(r) is True
    assert len(s.history) == 2


def test_normalize_party():
    from electionwatch.models import normalize_party
    assert [normalize_party(p) for p in ("GOP", "r", "Democratic", "DFL", "lib", None, "CST")] == \
        ["REP", "REP", "DEM", "DEM", "LIB", None, "CST"]


@pytest.mark.parametrize("text,canon,name", [
    ("PR-AL", "PR-AL", "Puerto Rico Resident Commissioner"),
    ("GU-AL", "GU-AL", "Guam Delegate to Congress"),
    ("PR GOV", "PR GOV", "Puerto Rico Governor"),
    ("Guam Governor", "GU GOV", "Guam Governor"),
    ("VI LOCAL st-thomas-senator", "VI LOCAL st-thomas-senator", "U.S. Virgin Islands local: st thomas senator"),
])
def test_territory_keys(text, canon, name):
    k = parse(text)
    assert str(k) == canon and describe(k) == name and parse(canon) == k


def test_adapter_coverage_list_is_states_and_dc_only():
    from electionwatch.racekeys import STATES, TERRITORIES
    assert len(STATES) == 51 and not set(TERRITORIES) & set(STATES)


def test_single_seat_states_key_as_at_large(monkeypatch):
    assert str(RaceKey("AK", Level.HOUSE, "HOUSE", "01")) == "AK-AL"
    assert str(RaceKey("WY", Level.HOUSE, "HOUSE", "1")) == "WY-AL"
    assert str(parse("DE-01")) == "DE-AL"
    assert str(RaceKey("MT", Level.HOUSE, "HOUSE", "01")) == "MT-01"      # two seats since 2022
    monkeypatch.setenv("ELECTION_DATE", "2018-11-06")
    assert str(RaceKey("MT", Level.HOUSE, "HOUSE", "01")) == "MT-AL"
    assert str(RaceKey("TX", Level.HOUSE, "HOUSE", "01")) == "TX-01"


@pytest.mark.parametrize("raw,shown", [
    ("Martha Mcsally", "Martha McSally"), ("DAVE MCCORMICK", "Dave McCormick"),
    ("Thomas J Kennedy Iii", "Thomas J Kennedy III"), ("JD VANCE", "JD Vance"),
    ("Angus S King Jr", "Angus S King Jr."), ("Mack Mackey", "Mack Mackey"), ("Begich, Nick", "Nick Begich"), ("King, Jr.", "King, Jr."), ("BEGICH, NICK", "Nick Begich"), ("HARRIS, Kamala D.", "Kamala D. Harris"), ("Donald J. Trump, \"JD\" Vance", "Donald J. Trump, \"JD\" Vance"), ("Yes, In Favor", "Yes, In Favor"), ("Ng Lee", "Ng Lee"),
])
def test_pretty_name(raw, shown):
    from electionwatch.models import pretty_name
    assert pretty_name(raw) == shown


def test_finalize_drops_ballot_statistics_and_keeps_real_options():
    rows = [CandidateResult("A", "REP", 60, pct=50.0), CandidateResult("B", "DEM", 40, pct=33.3),
            CandidateResult("Blank Ballots", None, 15), CandidateResult("Times Over Voted", None, 2),
            CandidateResult("Registered Voters", None, 400), CandidateResult("None Of These Candidates", None, 5),
            CandidateResult("Write-ins", None, 3)]
    r = RaceResult(parse("NV SEN"), rows, "t", NOW).finalize()
    assert [c.name for c in r.candidates] == ["A", "B", "None Of These Candidates", "Write-ins"]
    assert r.registered_voters == 400 and r.total_votes == 108 and r.candidates[0].pct == 55.56
