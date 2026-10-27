from datetime import datetime, timezone
from pathlib import Path

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.results.id_sos import (
    _needed_files, archive_index, page_links, parse_id, race_key,
)

FIX = Path(__file__).parent / "fixtures" / "id_sos"
AT = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)


def load(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_congressional_page():
    races = parse_id(load("congressional.html"), fetched_at=AT,
                     url="https://archive.voteidaho.gov/results/2024/general/congressional.html")
    assert len(races) == 1
    race = races[0]
    assert format_key(race.key) == "ID-01"
    assert race.key.level is Level.HOUSE
    assert race.title == "District 1 US Representative"
    names = {c.name: c for c in race.candidates}
    assert names["Russ Fulcher"].party == "REP"
    assert names["Russ Fulcher"].votes == 331049
    assert names["Kaylee Peterson"].votes == 118656
    assert race.total_votes == sum(c.votes for c in race.candidates)
    assert names["Russ Fulcher"].pct > 70


def test_legislative_page_seats():
    races = {format_key(r.key): r for r in parse_id(load("legislative.html"), fetched_at=AT)}
    senate = races["ID SD-01"]
    assert senate.key.level is Level.STATE
    assert senate.title == "District 1 Senator"
    assert senate.candidates[0].name == "Jim Woodward"
    assert senate.candidates[1].party == "IND"
    house = races["ID HD-01A"]
    assert house.key.office == "HD"
    assert house.key.district == "01A"          # Idaho House seats A and B are separate races
    assert house.title == "District 1 Representative A"


def test_archive_index_urls():
    assert archive_index("2026-11-03") == \
        "https://archive.voteidaho.gov/results/2026/general/index.html"
    assert archive_index("2026-05-19") == \
        "https://archive.voteidaho.gov/results/2026/primary/index.html"
    assert archive_index("2026-06-02") is None


def test_page_links_from_index():
    links = page_links(load("index.html"),
                       "https://archive.voteidaho.gov/results/2024/general/index.html")
    names = [n for n, _ in links]
    assert "congressional.html" in names and "legislative.html" in names
    assert not {"county.html", "measures.html", "turnout.html", "xml.html"} & set(names)
    urls = dict(links)
    assert urls["congressional.html"] == \
        "https://archive.voteidaho.gov/results/2024/general/congressional.html"


def test_only_the_needed_pages_are_fetched():
    assert _needed_files([parse("ID-01")]) == {"congressional.html"}
    assert _needed_files([parse("ID SD-01"), parse("ID HD-01A")]) == {"legislative.html"}
    assert _needed_files([parse("ID SEN")]) is None      # statewide: page name varies by year


def test_race_key_mapping():
    assert format_key(race_key("Governor")) == "ID GOV"
    assert format_key(race_key("Lieutenant Governor")) == "ID LTGOV"
    assert format_key(race_key("Secretary of State")) == "ID SOS"
    assert format_key(race_key("State Controller")) == "ID COMP"
    # Only offices racekeys.parse understands can be watched with /watch add.
    from electionwatch.racekeys import parse as parse_key
    for office in ("State Controller", "Superintendent of Public Instruction"):
        assert race_key(office) == parse_key(format_key(race_key(office)))
    assert format_key(race_key("Superintendent of Public Instruction")) == "ID SUPT"
    assert format_key(race_key("District 12 Representative B")) == "ID HD-12B"
    assert format_key(race_key("District 2 Senator")) == "ID SD-02"
    assert format_key(race_key("President and Vice President")) == "ID LOCAL us-president"


def test_unknown_heading_is_a_local_race():
    page = ('<h4>Ada County Commissioner District 2</h4>'
            '<script type="application/json" data-for="w">'
            '{"x":{"tag":{"attribs":{"data":{"ContestantName":["Ann Lee (REP)","Bob Ray (DEM)"],'
            '"TotalVotes":[1200,900]}}}}}</script>')
    races = parse_id(page, fetched_at=AT)
    assert len(races) == 1
    assert format_key(races[0].key) == "ID LOCAL ada-county-commissioner-district-2"
    assert races[0].candidates[0].party == "REP"


def test_write_in_rows_are_skipped():
    page = ('<h4>District 3 Senator</h4>'
            '<script type="application/json" data-for="w">'
            '{"x":{"tag":{"attribs":{"data":{"ContestantName":["Ann Lee (REP)","Write-in"],'
            '"TotalVotes":[1200,7]}}}}}</script>')
    races = parse_id(page, fetched_at=AT)
    assert [c.name for c in races[0].candidates] == ["Ann Lee"]
