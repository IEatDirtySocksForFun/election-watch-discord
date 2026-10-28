"""mo_stlouis: St. Louis County's per-precinct CSV export (Missouri's ENR is Cloudflare-blocked)."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, mo_stlouis

FIX = Path(__file__).parent / "fixtures" / "mo_stlouis"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)
CSV = (FIX / "st_louis_2024_csv.csv").read_bytes().decode("cp1252")


def by_key(results):
    return {format_key(r.key): r for r in results}


def parse_csv():
    return mo_stlouis.parse_csv(CSV, fetched_at=AT)


def merged():
    return _county.merge(parse_csv(), source="mo_stlouis", url=mo_stlouis.HUMAN)


def test_election_directory_naming():
    assert mo_stlouis.election_dir("2024-11-05") == "el241105"
    assert mo_stlouis.election_dir("2026-11-03") == "el261103"


def test_votes_are_summed_over_precincts():
    got = by_key(merged())
    sen = got["MO SEN"]
    assert sen.key.level is Level.SENATE
    assert {c.name: c.votes for c in sen.candidates}["Lucas Kunce"] == 798
    assert sen.total_votes == 1246
    assert sen.candidates[0].pct == round(100.0 * 798 / 1246, 2)
    assert sen.precincts_reporting == 5
    assert sen.precincts_total is None  # the export carries no precinct total
    assert sen.title == "U.S. SENATOR (partial: 1 counties)"
    assert sen.url == "https://enr.sos.mo.gov/"


def test_statewide_and_district_mapping():
    got = by_key(merged())
    assert got["MO GOV"].key.level is Level.GOVERNOR
    assert got["MO-01"].key.district == "01"
    assert got["MO HD-73"].candidates[0].name == "Raychel C Proudie"


def test_ballot_measures_and_retention_are_local():
    got = by_key(merged())
    assert got["MO LOCAL constitutional-amendment-no-2"].candidates[0].name == "Yes"
    prop = got["MO LOCAL st-louis-county-proposition-a"]
    assert prop.key.office == "LOCAL"
    assert "st-louis-county-st-louis-county" not in str(prop.key)
    # Judicial retention contests are titled with the judge's name and use YES/NO.
    retention = got["MO LOCAL st-louis-county-kelly-c-broniec"]
    assert {c.name: c.votes for c in retention.candidates} == {"No": 544, "Yes": 479}


def test_unfiled_write_ins_are_dropped():
    names = {c.name for c in by_key(merged())["MO SEN"].candidates}
    assert "Gina Bufe" not in names  # empty party, zero votes, choice id 0
    assert "Jared Young" in names  # zero votes but a filed party


def test_cp1252_bytes_decode():
    assert "RENÉE" not in CSV  # the retained precincts do not include that contest
    assert mo_stlouis.race_key("ST. LOUIS COUNTY - PROPOSITION C").office == "LOCAL"


def test_fetch_requests_the_election_directory(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=CSV.encode("cp1252"))

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await mo_stlouis.MoStLouis().fetch([parse("MO SEN"), parse("MO-01")], c)

    got = by_key(asyncio.run(go()))
    assert seen == ["https://extcontent.stlouisco.com/BOE/eResults/el241105/CSV.csv"]
    assert set(got) == {"MO SEN", "MO-01"}


def test_fetch_reports_a_missing_export(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as c:
            await mo_stlouis.MoStLouis().fetch([parse("MO SEN")], c)

    with pytest.raises(SourceUnavailable) as err:
        asyncio.run(go())
    assert "el261103" in str(err.value)
