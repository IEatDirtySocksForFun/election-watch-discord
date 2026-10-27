"""tx_counties: Texas county Clarity ENR feeds merged into partial statewide results."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, tx_counties

FIX = Path(__file__).parent / "fixtures" / "tx_counties"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / name).read_text())


def by_key(results):
    return {format_key(r.key): r for r in results}


def merged_2024():
    denton = tx_counties.parse_tx_county(load("denton_2024_summary.json"), None,
                                         county="Denton", fetched_at=AT)
    elpaso = tx_counties.parse_tx_county(load("el_paso_2024_summary.json"), None,
                                         county="El_Paso", fetched_at=AT)
    return _county.merge(denton + elpaso, source="tx_counties", url=tx_counties.HUMAN)


def test_senate_votes_are_summed_across_counties():
    sen = by_key(merged_2024())["TX SEN"]
    cands = {c.name: c.votes for c in sen.candidates}
    # Denton 237978 + El Paso 92997 for Cruz; 200676 + 141826 for Allred.
    assert cands["Ted Cruz"] == 237978 + 92997
    assert cands["Colin Allred"] == 200676 + 141826
    assert sen.total_votes == 448459 + 245124
    assert sen.title == "United States Senator (partial: 2 counties)"
    assert sen.source == "tx_counties"
    assert sen.url == "https://results.texas-election.com/races"


def test_merged_percentages_and_precincts_are_recomputed():
    sen = by_key(merged_2024())["TX SEN"]
    assert sen.candidates[0].name == "Colin Allred"  # finalize() sorts by votes
    assert sen.candidates[0].pct == round(100.0 * 342502 / 693583, 2)
    assert (sen.precincts_reporting, sen.precincts_total) == (224 + 215, 240 + 221)
    assert sen.expected_vote_pct == round(100.0 * 439 / 461, 2)


def test_congressional_districts_come_from_two_different_name_shapes():
    keys = by_key(merged_2024())
    assert keys["TX-26"].key.level is Level.HOUSE
    assert keys["TX-16"].key.level is Level.HOUSE
    # Each district is published by one county only, so no partial suffix.
    assert keys["TX-16"].title == "US Representative, District 16 (partial: 1 counties)"
    assert keys["TX-16"].candidates[0].name == "Veronica Escobar"


def test_statewide_executive_and_legislature():
    keys = by_key(merged_2024())
    assert keys["TX RRC"].key.level is Level.GOVERNOR
    assert keys["TX HD-65"].key.level is Level.STATE
    assert keys["TX HD-65"].key.district == "65"


def test_local_races_are_namespaced_by_county_but_shared_courts_are_not():
    keys = by_key(merged_2024())
    assert "TX LOCAL denton-sheriff" in keys
    assert "TX LOCAL el-paso-sheriff" in keys
    assert keys["TX LOCAL denton-sheriff"].candidates[0].votes == 250298
    # County-court abbreviations are expanded, so El Paso's "Crt of App Dist" is a
    # statewide appeals-court race rather than a county-local one.
    assert keys["TX LOCAL court-of-appeals-8-chief-justice"].key.office == "LOCAL"
    assert "TX LOCAL el-paso-court-of-appeals-8-chief-justice" not in keys


def test_2026_general_file_parses_with_zero_votes():
    results = by_key(tx_counties.parse_tx_county(
        load("denton_2026_summary.json"), None, county="Denton", fetched_at=AT))
    assert results["TX SEN"].candidates[0].name == "Ken Paxton"
    assert results["TX SEN"].candidates[0].votes == 0
    assert results["TX GOV"].title == "Governor"
    assert results["TX-26"].key.level is Level.HOUSE
    assert "TX LOCAL denton-city-of-the-colony-place-3" in results
    assert "TX LOCAL denton-city-of-dallas-proposition-a" in results


def test_elections_listing_finds_the_general_for_a_date():
    entries = _county.parse_elections(load("denton_elections.json"))
    assert _county.find_election(entries, "2026-11-03") == "127398"
    assert _county.find_election(entries, "2024-11-05") == "122426"
    assert _county.find_election(entries, "2022-11-08") is None


def test_elections_listing_falls_back_when_the_general_is_filed_under_its_final_date():
    # Mercer County files "2024 General" under 12/5/2024 (its certification date).
    entries = _county.parse_elections([
        {"EID": "122845", "ElectionName": "2024 General", "Date": "12/5/2024 12:00:00 AM"},
        {"EID": "123019", "ElectionName": "2024 Trenton Runoff", "Date": "12/3/2024 12:00:00 AM"},
        {"EID": "122320", "ElectionName": "2024 Special School", "Date": "9/17/2024 12:00:00 AM"},
    ])
    assert _county.find_election(entries, "2024-11-05") == "122845"


def test_elections_listing_ignores_wrong_year_and_primaries():
    entries = _county.parse_elections([
        {"EID": "1", "ElectionName": "2026 Primary Election", "Date": "6/2/2026 12:00:00 AM"},
        {"EID": "2", "ElectionName": "2025 General Election", "Date": "11/4/2025 12:00:00 AM"},
    ])
    assert _county.find_election(entries, "2026-11-03") is None


def test_county_override_from_env(monkeypatch):
    monkeypatch.delenv("TX_COUNTY_FEEDS", raising=False)
    assert [c["county"] for c in tx_counties.counties("2026-11-03")][0] == "Dallas"
    monkeypatch.setenv("TX_COUNTY_FEEDS", json.dumps([
        {"county": "Harris", "election_id": "123456", "date": "2026-11-03"},
        {"county": "Bexar", "election_id": "123457", "date": "2024-11-05"},
    ]))
    got = tx_counties.counties("2026-11-03")
    assert got == [{"county": "Harris", "slug": "harris", "election_id": "123456"}]
    with pytest.raises(SourceUnavailable):
        monkeypatch.setenv("TX_COUNTY_FEEDS", "{oops")
        tx_counties.counties("2026-11-03")


def test_fetch_merges_pinned_counties_and_skips_a_dead_one(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    bodies = {
        ("Denton", "122426"): "denton_2024_summary.json",
        ("El_Paso", "122595"): "el_paso_2024_summary.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")           # TX/<County>/<id>/<...>
        key = (parts[1], parts[2])
        if key not in bodies:
            return httpx.Response(503)                            # Travis is pinned but down
        if request.url.path.endswith("current_ver.txt"):
            return httpx.Response(200, text="355989")
        if request.url.path.endswith("summary.json"):
            return httpx.Response(200, json=json.loads((FIX / bodies[key]).read_text()))
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await tx_counties.TxCounties().fetch(
                [parse("TX SEN"), parse("TX-16"), parse("TX GOV")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"TX SEN", "TX-16"}
    assert got["TX SEN"].total_votes == 693583


def test_fetch_reports_unavailable_when_no_county_answers(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    monkeypatch.setenv("TX_COUNTY_FEEDS", json.dumps([
        {"county": "Harris", "election_id": "1", "date": "2024-11-05"}]))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(503))) as c:
            await tx_counties.TxCounties().fetch([parse("TX SEN")], c)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


# --- the eleven counties added 2026-10-27 -------------------------------------------------

def _parse(county_file: str, county: str):
    return tx_counties.parse_tx_county(load(county_file), None, county=county, fetched_at=AT)


def merged_all_2024():
    results = []
    for path in sorted(FIX.glob("*_2024_summary.json")):
        stem = path.name.split("_")[0]
        results += _parse(path.name, "El_Paso" if stem == "el" else stem.capitalize())
    return by_key(_county.merge(results, source="tx_counties", url=tx_counties.HUMAN))


def test_county_list_covers_nineteen_clarity_feeds():
    assert len(tx_counties.COUNTIES) == 19
    for county in ("Galveston", "Bell", "Comal", "Midland", "Gregg", "Kaufman",
                   "Rockwall", "Lamar", "Rusk", "Bastrop", "Hood"):
        assert county in tx_counties.COUNTIES
        assert tx_counties.COUNTIES[county] == county.lower()
    # Harris runs its own JSON feed (tx_counties.parse_harris), not Clarity; Bexar moved
    # to Clarity for 2026.
    assert "Harris" not in tx_counties.COUNTIES
    assert tx_counties.COUNTIES["Bexar"] == "bexar"


def test_senate_merges_across_thirteen_2024_counties():
    sen = merged_all_2024()["TX SEN"]
    cands = {c.name: c for c in sen.candidates}
    # 13 of the 2024 fixtures report a Senate contest; totals are the fixtures' T values.
    assert sen.title == "US Senator (partial: 13 counties)"
    assert sen.total_votes == 1949354
    assert cands["Ted Cruz"].votes == 928197
    assert cands["Colin Allred"].votes == 970491
    assert cands["Ted Cruz"].party == "REP"
    assert cands["Colin Allred"].party == "DEM"


def test_repeated_party_prefix_is_stripped_before_merging():
    # Lamar, Rockwall, Rusk and Bastrop print "REP Ted Cruz" although P already says REP.
    keys = by_key(_parse("lamar_2024_summary.json", "Lamar"))
    assert keys["TX SEN"].candidates[0].name == "Ted Cruz"
    assert {c.name: c.party for c in keys["TX SEN"].candidates}["Ted Cruz"] == "REP"
    # The prefix is only stripped when it repeats the party column, so a title is kept.
    assert _county.split_party_prefix("Rep. John Lewis", None) == "Rep. John Lewis"
    assert _county.split_party_prefix("REP Ted Cruz", "DEM") == "REP Ted Cruz"


def test_house_district_name_variants():
    assert "TX-25" in by_key(_parse("hood_2024_summary.json", "Hood"))       # "U. S. ..."
    assert "TX-11" in by_key(_parse("bell_2024_summary.json", "Bell"))       # "Dist 11"
    assert "TX-73" not in by_key(_parse("comal_2024_summary.json", "Comal"))  # HD, not US House
    assert "TX HD-73" in by_key(_parse("comal_2024_summary.json", "Comal"))  # double space


def test_abbreviated_shared_courts_and_board_of_education():
    bell = by_key(_parse("bell_2024_summary.json", "Bell"))
    assert "TX LOCAL presiding-judge-court-criminal-appeals" in bell      # "Ct of Crim App"
    assert "TX LOCAL state-board-of-education-10" in bell                 # "State BoE, Dist 10"
    assert "TX LOCAL supreme-court-place-2" in bell                       # "Pl 2"
    hood = by_key(_parse("hood_2024_summary.json", "Hood"))
    assert "TX LOCAL supreme-court-place-6" in hood                       # "Superme Court"
    assert "TX LOCAL hood-sheriff" in hood                                # still local


def test_2026_aliases_for_land_commissioner_and_unexpired_terms():
    kaufman = by_key(_parse("kaufman_2026_summary.json", "Kaufman"))
    assert kaufman["TX LANDCOM"].key.level is Level.GOVERNOR           # "Comm General Land Office"
    assert kaufman["TX LANDCOM"].candidates[0].votes == 0
    assert "TX LOCAL supreme-court-place-2" in kaufman                 # "Pl 2 Unexp"
    galveston = by_key(_parse("galveston_2026_summary.json", "Galveston"))
    assert galveston["TX GOV"].title == "Governor"
    assert "TX LOCAL supreme-court-place-2" in galveston               # "... Unexpired Term"


def test_rejected_and_unassigned_write_in_rows_are_not_candidates():
    keys = by_key(_parse("hood_2024_summary.json", "Hood"))
    names = [c.name for c in keys["TX-25"].candidates]
    assert names == ["Roger Williams", "Chad Hagg"]      # counted write-in kept, tallies dropped


def test_president_merges_across_every_county_that_reports_it():
    pres = merged_all_2024()["TX LOCAL president-vice-president"]
    assert pres.title == "President/Vice President (partial: 8 counties)"
    cands = {c.name: c for c in pres.candidates}
    assert {c.party for c in pres.candidates[:2]} == {"REP", "DEM"}
    # Three ticket spellings collapse to the presidential name:
    # "Donald J. Trump/JD Vance" (most), "Donald J. Trump JD Vance" (Comal, no slash)
    # and "(R) Donald J. Trump" (Travis, no running mate at all).
    assert cands["Donald J. Trump"].votes == 729798
    assert cands["Kamala D. Harris"].votes == 721587
    assert not [n for n in cands if n.startswith(("Donald", "Kamala")) and "/" in n]


def test_parenthesised_party_prefixes_are_stripped():
    # Travis prints "(R) Ted Cruz" with the party also in the column, like Lamar's "REP ...".
    keys = by_key(_parse("travis_2024_summary.json", "Travis"))
    names = [c.name for c in keys["TX SEN"].candidates]
    assert names == ["Colin Allred", "Ted Cruz", "Ted Brown", "Write-in"]
    assert {c.name: c.party for c in keys["TX SEN"].candidates}["Ted Cruz"] == "REP"
    pres = {c.name: c.party for c in keys["TX LOCAL president-vice-president"].candidates}
    assert pres["Donald J. Trump"] == "REP" and pres["Kamala D. Harris"] == "DEM"


def test_travis_districts_and_propositions():
    keys = by_key(_parse("travis_2024_summary.json", "Travis"))
    assert keys["TX-37"].key.level is Level.HOUSE
    assert keys["TX-35"].candidates[0].name == "Greg Casar"
    assert keys["TX SD-14"].title == "State Senator, District 14"
    # "Presiding Judge, Court of Criminal Appeals, Place 1" is the same shared seat that
    # Denton publishes without the ", Place 1".
    assert "TX LOCAL presiding-judge-court-criminal-appeals" in keys
    assert "TX LOCAL travis-travis-county-proposition-a" in keys



# --- Tarrant, Fort Bend, Bexar pins and Harris County's own feed (2026-10-27) -------------

def test_pinned_ids_for_counties_whose_elections_json_is_empty():
    pins24 = tx_counties.ELECTIONS["2024-11-05"]
    pins26 = tx_counties.ELECTIONS["2026-11-03"]
    assert (pins24["Tarrant"], pins26["Tarrant"]) == ("122489", "127297")
    assert (pins24["Fort_Bend"], pins26["Fort_Bend"]) == ("122596", "127480")
    assert pins26["Bexar"] == "127291"
    assert "Williamson" in tx_counties.COUNTIES   # discovered from elections.json


def test_fort_bend_repeated_district_suffix_maps_to_shared_keys():
    got = by_key(_parse("fort_bend_2024_general.json", "Fort_Bend"))
    assert set(got) == {"TX SEN", "TX-07", "TX SD-17", "TX HD-26"}
    assert got["TX SEN"].total_votes == 356419
    assert tx_counties.shared_key("UNITED STATES REPRESENTATIVE, DISTRICT 7 District 7") \
        == parse("TX-07")


def test_tarrant_and_bexar_2026_files_parse_with_zero_votes():
    tarrant = by_key(_parse("tarrant_2026_general.json", "Tarrant"))
    bexar = by_key(_parse("bexar_2026_general.json", "Bexar"))
    assert {"TX SEN", "TX-12", "TX GOV"} <= set(tarrant)
    assert {"TX SEN", "TX-20", "TX GOV", "TX HD-116"} <= set(bexar)
    # "Place 2, (Unexpired Term)" (Tarrant) and "Place 2 - Unexpired Term" (Bexar) merge.
    assert "TX LOCAL supreme-court-place-2" in tarrant
    assert "TX LOCAL supreme-court-place-2" in bexar
    assert tarrant["TX SEN"].total_votes == 0
    assert tarrant["TX SEN"].precincts_total == 2985


def test_harris_2024_feed_parses_party_suffixes_and_totals():
    got = by_key(tx_counties.parse_harris(load("harris_2024_resultsbyballot.json"),
                                          fetched_at=AT))
    sen = got["TX SEN"]
    cands = {c.name.upper(): (c.party, c.votes) for c in sen.candidates}
    assert cands["TED CRUZ"] == ("REP", 666027)
    assert cands["COLIN ALLRED"] == ("DEM", 841784)
    assert cands["ANALISA ROCHE"][0] == "WRI"
    assert sen.total_votes == 1548914
    assert (sen.precincts_reporting, sen.precincts_total) == (1172, 1172)
    assert sen.source == "tx_counties"
    assert {"TX-07", "TX RRC", "TX HD-134", "TX LOCAL president-vice-president"} <= set(got)


def test_harris_merges_with_clarity_counties():
    tarrant = _parse("tarrant_2024_general.json", "Tarrant")
    harris = tx_counties.parse_harris(load("harris_2024_resultsbyballot.json"), fetched_at=AT)
    sen = by_key(_county.merge(tarrant + harris, source="tx_counties",
                               url=tx_counties.HUMAN))["TX SEN"]
    assert sen.total_votes == 821473 + 1548914
    cruz = next(c for c in sen.candidates if c.name.upper() == "TED CRUZ")
    assert cruz.votes == 399927 + 666027
    assert sen.title.endswith("(partial: 2 counties)")


def test_harris_2026_file_and_wrong_date_guard():
    payload = load("harris_2026_resultsbyballot.json")
    got = by_key(tx_counties.parse_harris(payload, fetched_at=AT, date="2026-11-03"))
    assert {"TX SEN", "TX-07", "TX GOV", "TX LOCAL court-of-appeals-15-chief-justice",
            "TX LOCAL harris-county-judge"} <= set(got)
    assert got["TX SEN"].total_votes == 0
    assert tx_counties.parse_harris(payload, fetched_at=AT, date="2026-11-10") == []


def test_harris_election_code_and_switch(monkeypatch):
    monkeypatch.delenv("TX_HARRIS_CODE", raising=False)
    assert tx_counties.harris_entries("2026-11-03")[0]["code"] == "1126"
    assert tx_counties.harris_entries("2024-11-05")[0]["code"] == "1124"
    monkeypatch.setenv("TX_HARRIS_CODE", "0526")
    assert tx_counties.harris_entries("2026-11-03")[0]["code"] == "0526"
    monkeypatch.setenv("TX_HARRIS_CODE", "off")
    assert tx_counties.harris_entries("2026-11-03") == []


def test_fetch_includes_harris_alongside_clarity(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    monkeypatch.delenv("TX_HARRIS_CODE", raising=False)
    monkeypatch.setenv("TX_COUNTY_FEEDS", json.dumps([
        {"county": "Tarrant", "election_id": "122489"}]))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "appfiles.harrisvotes.com":
            assert request.url.path == "/harrisvotes/prd/Data/1124/resultsbyballot.json"
            return httpx.Response(200, json=load("harris_2024_resultsbyballot.json"))
        if request.url.path.endswith("current_ver.txt"):
            return httpx.Response(200, text="355610")
        if request.url.path.endswith("summary.json"):
            return httpx.Response(200, json=load("tarrant_2024_general.json"))
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await tx_counties.TxCounties().fetch([parse("TX SEN"), parse("TX-07")], c)

    got = by_key(asyncio.run(go()))
    assert got["TX SEN"].total_votes == 821473 + 1548914
    assert got["TX-07"].total_votes == 173438       # Harris only
