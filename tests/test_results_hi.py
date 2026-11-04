from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results.hi_soe import (
    HiSoe,
    _parse_name,
    _rows,
    parse_hi,
    race_key,
    summary_url,
)

FIX = Path(__file__).parent / "fixtures" / "hi_soe" / "2024-general.txt"
FIX_2026 = Path(__file__).parent / "fixtures" / "hi_soe" / "2026-general.txt"
AT = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
AT_2026 = datetime(2026, 11, 4, 13, 0, tzinfo=timezone.utc)


def load():
    with FIX.open(encoding="utf-8") as fh:
        return fh.read()


def load_2026():
    with FIX_2026.open(encoding="utf-8") as fh:
        return fh.read()


def by_key(text, at=AT):
    return {format_key(race_result.key): race_result for race_result in parse_hi(_rows(text), fetched_at=at)}


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


def test_governor_ticket_title_maps_to_the_governor_race():
    assert race_key("Governor and Lieutenant Governor") == parse("HI GOV")


def test_2026_general_tab_export():
    """The 2026 general export is tab-delimited with a header row and counted votes."""
    keys = by_key(load_2026(), AT_2026)
    assert set(keys) == {"HI-01", "HI-02", "HI GOV", "HI SD-02", "HI HD-43", "HI LOCAL question-1"}
    case = keys["HI-01"]
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in case.candidates] == [
        ("Ed Case", "DEM", 100096),
        ("Adriel C. Lam", "REP", 28685),
        ("Jordan S. Conley", "GRN", 4528),
        ("Nathan M. Berning", "NPA", 1780),
    ]
    assert (case.precincts_reporting, case.precincts_total, case.registered_voters) == (112, 223, 853399)
    assert case.expected_vote_pct == 50.22
    assert case.total_votes == 135089


def test_2026_general_governor_ticket_keeps_the_top_of_the_ticket_name():
    governor = by_key(load_2026(), AT_2026)["HI GOV"]
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in governor.candidates] == [
        ("Josh Green", "DEM", 227046),
        ("Gary Cordery", "REP", 75978),
    ]
    assert governor.title == "Governor and Lieutenant Governor"


def test_2026_general_rows_without_party_prefixes_and_uncounted_precincts():
    keys = by_key(load_2026(), AT_2026)
    assert [(candidate.name, candidate.party) for candidate in keys["HI HD-43"].candidates] == [
        ("Kanani Souza", None),
        ("Sheila Medeiros", None),
    ]
    # Votes are present while no precinct has reported yet: the share stays unknown.
    assert keys["HI SD-02"].precincts_reporting == 0
    assert keys["HI SD-02"].expected_vote_pct is None


def test_summary_url_moved_for_2026():
    assert summary_url("2026-11-03") == ("https://elections.hawaii.gov/wp-content/results/2026%20General/summary.txt")
    assert summary_url("2024-11-05") == "https://files.hawaii.gov/elections/files/results/2024/General/summary.txt"


@respx.mock
async def test_fetch_reads_the_2026_export_from_the_results_host(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    route = respx.get(summary_url("2026-11-03")).respond(text=load_2026())
    async with httpx.AsyncClient() as client:
        results = await HiSoe().fetch([parse("HI GOV"), parse("HI-01")], client)
    assert route.called
    assert {format_key(race_result.key) for race_result in results} == {"HI GOV", "HI-01"}
    assert {race_result.total_votes for race_result in results} == {303024, 135089}
