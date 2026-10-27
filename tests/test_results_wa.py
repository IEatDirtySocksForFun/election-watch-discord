import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import wa_sos
from electionwatch.sources.results.wa_sos import parse_wa, race_key

FIX = Path(__file__).parent / "fixtures" / "wa_sos"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def parse():
    return parse_wa((FIX / "20241105_AllState.xml").read_text(encoding="utf-8"),
                    fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_federal_and_statewide_keys():
    got = by_key(parse())
    assert got["WA SEN"].key.level is Level.SENATE
    assert got["WA-01"].key.level is Level.HOUSE
    assert got["WA GOV"].key.level is Level.GOVERNOR
    assert got["WA AG"].key.level is Level.GOVERNOR


def test_party_labels_are_normalised():
    got = by_key(parse())
    # Source sends "(Prefers Democratic Party)" / "(Prefers Republican Party)".
    assert [c.party for c in got["WA SEN"].candidates] == ["DEM", "REP", None]
    assert got["WA GOV"].candidates[0].party == "DEM"


def test_votes_and_percentages():
    got = by_key(parse())
    sen = got["WA SEN"]
    assert sen.candidates[0].name == "Maria Cantwell"
    assert sen.candidates[0].votes == 2252577
    assert sen.candidates[0].pct == 59.09            # source field, not recomputed
    assert sen.total_votes == sum(c.votes for c in sen.candidates)
    assert got["WA LOCAL initiative-measure-no-2066"].candidates[0].name == "Yes"


def test_legislative_positions_get_distinct_keys():
    got = by_key(parse())
    assert got["WA SD-01"].key.level is Level.STATE
    assert got["WA HD-01"].key.level is Level.STATE
    assert got["WA HD-01P2"].key.level is Level.STATE
    assert got["WA HD-01"].candidates[0].name == "Davina Duerr"
    assert got["WA HD-01P2"].candidates[0].name == "Shelley Kloba"
    # Case-insensitive: the Pos. 2 row is capitalised differently in the export.
    assert format_key(race_key("Legislative District 1 - State Representative Pos. 2")) == \
        "WA HD-01P2"


def test_race_key_mapping():
    assert format_key(race_key("U.S. Senator")) == "WA SEN"
    assert format_key(race_key("CONGRESSIONAL DISTRICT 3 - U.S. Representative")) == "WA-03"
    assert format_key(race_key("Congressional District 10 - U.S. Representative")) == "WA-10"
    assert format_key(race_key("Lt. Governor")) == "WA LTGOV"
    assert format_key(race_key("Commissioner of Public Lands")) == "WA LANDCOM"
    assert format_key(race_key("State Auditor")) == "WA AUD"
    assert format_key(race_key("LEGISLATIVE DISTRICT 12 - State Senator")) == "WA SD-12"
    assert format_key(race_key("Adams Superior Court  - Judge Position 1")) == \
        "WA LOCAL adams-superior-court-judge-position-1"
    assert format_key(race_key("President/Vice President")) == "WA LOCAL president-vice-president"


def test_no_precincts_or_winner_flags_in_export():
    r = by_key(parse())["WA SEN"]
    assert r.precincts_reporting is None and r.precincts_total is None
    assert r.expected_vote_pct is None
    assert not any(c.winner for c in r.candidates)


def test_empty_and_bad_payloads():
    assert parse_wa("", fetched_at=AT) == []
    with pytest.raises(SourceUnavailable):
        parse_wa("<Results><Result>", fetched_at=AT)


def test_fetch_reads_date_folder(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=(FIX / "20241105_AllState.xml").read_text())

    from electionwatch.racekeys import parse as parse_key

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await wa_sos.WaSos().fetch([parse_key("WA SEN"), parse_key("WA-01")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"WA SEN", "WA-01"}
    assert seen == ["https://results.vote.wa.gov/results/20241105/export/20241105_AllState.xml"]


def test_fetch_reports_unavailable_before_publication(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    from electionwatch.racekeys import parse as parse_key

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await wa_sos.WaSos().fetch([parse_key("WA SEN")], c)

    with pytest.raises(SourceUnavailable, match="not published yet"):
        asyncio.run(go())
