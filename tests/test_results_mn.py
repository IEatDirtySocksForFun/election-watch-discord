import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import mn_sos
from electionwatch.sources.results.mn_sos import (
    parse_counties, parse_mn, race_key, folder, decode_bytes,
)

FIX = Path(__file__).parent / "fixtures" / "mn_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)

FILES = ("ussenate.txt", "ushouse.txt", "stsenate.txt", "LegislativeByDistrict.txt",
         "governor.txt", "auditor.txt", "judicial.txt", "cntyRaceQuestions.txt")


def load(name: str) -> str:
    return decode_bytes((FIX / name).read_bytes())


def payload() -> dict[str, str]:
    return {name: load(name) for name in FILES}


def parse_all():
    return parse_mn(payload(), fetched_at=AT, counties=parse_counties(load("Cntytbl.txt")))


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_governor_ticket_votes_and_precincts():
    gov = by_key(parse_all())["MN GOV"]
    assert gov.key.level is Level.GOVERNOR
    assert gov.title == "Governor & Lt Governor"
    assert [c.name for c in gov.candidates] == [
        "Lisa Demuth and Ryan Wilson", "Amy Klobuchar and Ben Schierer",
        "Steven Young and Jane Kirby"]                    # sorted by votes
    assert gov.candidates[0].votes == 20929
    assert gov.candidates[0].party == "REP"
    assert gov.candidates[1].party == "DEM"               # DFL -> DEM
    assert gov.total_votes == 68628                       # contest total from the feed
    assert (gov.precincts_reporting, gov.precincts_total) == (1517, 4105)
    assert gov.expected_vote_pct == 36.95
    assert gov.url == "https://electionresults.sos.mn.gov/"


def test_write_in_rows_are_dropped():
    sen = by_key(parse_all())["MN SEN"]
    assert "WRITE-IN" not in [c.name for c in sen.candidates]
    assert [c.party for c in sen.candidates] == ["REP", "DEM", "LIB", "IND"]
    assert sen.candidates[0].votes == 27309
    assert sen.candidates[0].pct == 29.56


def test_federal_and_legislative_districts():
    keys = by_key(parse_all())
    assert set(keys) >= {"MN-01", "MN-02", "MN SD-01", "MN SD-02", "MN HD-1A", "MN HD-1B"}
    assert keys["MN-01"].key.level is Level.HOUSE
    assert keys["MN-01"].title == "U.S. Representative District 1"
    assert keys["MN HD-1A"].key.district == "1A"
    assert keys["MN HD-1A"].candidates[0].party == "REP"


def test_judicial_and_county_races_become_local():
    keys = by_key(parse_all())
    assert "MN LOCAL associate-justice-supreme-court-1" in keys
    assert keys["MN LOCAL associate-justice-supreme-court-1"].candidates[0].party == "NPA"
    county = keys["MN LOCAL aitkin-county-commissioner-district-1"]
    assert county.title == "Aitkin County Commissioner District 1"
    assert county.key.level is Level.LOCAL
    assert [c.name for c in county.candidates] == ["Timothy Catlin", "J. Mark Wedel"]


def test_percent_is_taken_from_the_feed_when_present():
    auditor = by_key(parse_all())["MN AUD"]
    assert auditor.candidates[0].name == "Zack Filipovich"      # write-in row is skipped
    assert auditor.candidates[0].pct == 30.08                   # 0-100 as published
    assert auditor.total_votes == 70151


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senator")) == "MN SEN"
    assert format_key(race_key("U.S. Representative District 7")) == "MN-07"
    assert format_key(race_key("Governor & Lt Governor")) == "MN GOV"
    assert format_key(race_key("State Auditor")) == "MN AUD"
    assert format_key(race_key("Attorney General")) == "MN AG"
    assert format_key(race_key("Secretary of State")) == "MN SOS"
    assert format_key(race_key("State Senator District 4")) == "MN SD-04"
    assert format_key(race_key("State Representative District 12B")) == "MN HD-12B"
    assert format_key(race_key("County Sheriff", county="Dane")) == "MN LOCAL dane-county-sheriff"


def test_county_table_and_folder():
    assert parse_counties(load("Cntytbl.txt"))["01"] == "Aitkin"
    assert parse_counties("") == {}
    assert folder("2026-11-03") == "20261103"


def test_malformed_and_empty_rows_are_skipped():
    assert parse_mn({"x.txt": "not;a;row\n\n"}, fetched_at=AT) == []
    assert parse_mn({"x.txt": "WI;;;0102;U.S. Senator;;1001;Someone;;;IND;1;1;5;100;5\n"},
                    fetched_at=AT) == []                      # wrong state prefix


def test_decode_bytes_falls_back_from_cp1252():
    assert decode_bytes(b"MN;;;0104;Jos\xe9") .endswith("José")


def test_fetch_reads_the_office_files(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        seen.append(name)
        if name in FILES or name == "Cntytbl.txt":
            return httpx.Response(200, content=(FIX / name).read_bytes())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await mn_sos.MnSos().fetch([parse("MN GOV"), parse("MN-01")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"MN GOV", "MN-01"}
    assert got["MN GOV"].source == "mn_sos"
    assert got["MN-01"].precincts_total == 750
    assert all(name.endswith(".txt") for name in seen)


def test_fetch_without_published_files_is_unavailable(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await mn_sos.MnSos().fetch([parse("MN GOV")], c)

    with pytest.raises(SourceUnavailable, match="none of the"):
        asyncio.run(go())


def test_list_races_covers_loaded_files(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        path = FIX / name
        if path.exists():
            return httpx.Response(200, content=path.read_bytes())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await mn_sos.MnSos().list_races("MN", c)

    races = asyncio.run(go())
    keys = {format_key(k) for k, _ in races}
    assert {"MN GOV", "MN SEN", "MN-01", "MN SD-01", "MN HD-1A"} <= keys
    assert asyncio.run(_other_state()) == []


async def _other_state():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(404))) as c:
        return await mn_sos.MnSos().list_races("WI", c)
