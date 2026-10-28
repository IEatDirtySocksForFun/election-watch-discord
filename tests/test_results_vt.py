import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import format_key
from electionwatch.racekeys import parse as parse_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import vt_sos
from electionwatch.sources.results.vt_sos import parse_vt, race_key

FIX = Path(__file__).parent / "fixtures" / "vt_sos" / "VT"
GUID = "67a35c79-05a2-4ae3-a77d-97812256188b"
BASE = "https://static.electionresults.vermont.gov/elections"
AT = datetime(2026, 10, 27, 16, 0, tzinfo=timezone.utc)


def docs():
    return [json.loads(p.read_text()) for p in sorted(FIX.glob(f"{GUID}-*-2026*.json"))]


def parse():
    return parse_vt(docs(), fetched_at=AT)


def by_key(results):
    return {format_key(r.key): r for r in results}


def test_keys_cover_federal_statewide_legislature():
    got = by_key(parse())
    assert got["VT SEN"].key.level is Level.SENATE
    assert got["VT-AL"].key.level is Level.HOUSE  # single at-large seat
    from electionwatch.racekeys import parse as parse_key

    assert got["VT-AL"].key == parse_key("VT-AL")  # what /watch add VT-AL stores
    assert got["VT GOV"].key.level is Level.GOVERNOR
    assert got["VT LTGOV"].key.level is Level.GOVERNOR
    assert got["VT TREAS"].key.level is Level.GOVERNOR
    assert got["VT SD-add-1"].key.level is Level.STATE
    assert got["VT HD-add-1"].key.level is Level.STATE
    assert "VT LOCAL" in format_key(got["VT LOCAL us-president"].key)


def test_named_vermont_districts_become_slugs():
    assert format_key(race_key("STATE SENATOR", "BEN 1")) == "VT SD-ben-1"
    assert format_key(race_key("STATE REPRESENTATIVE", "Add Rut")) == "VT HD-add-rut"
    assert format_key(race_key("STATE SENATOR", "CHI SE 1")) == "VT SD-chi-se-1"


def test_candidates_aggregated_across_towns():
    got = by_key(parse())
    sen = got["VT SEN"]
    sanders = next(c for c in sen.candidates if c.name == "Bernie Sanders")
    assert sanders.party == "IND"

    expected = 0
    for contest in docs():
        for ct in contest["d"][0]["o"]:
            if ct.get("on") != "US SENATOR":
                continue
            for row in ct.get("cs") or []:
                for choice in row.get("rc") or []:
                    if choice.get("cn") == "BERNIE SANDERS":
                        expected += choice.get("vc") or 0
    assert sanders.votes == expected > 0
    assert sen.total_votes == sum(x.votes for x in sen.candidates)


def test_party_labels_and_write_ins():
    got = by_key(parse())
    gov = got["VT GOV"]
    scott = next(c for c in gov.candidates if c.name == "Phil Scott")
    assert scott.party == "REP"
    charlestin = next(c for c in gov.candidates if c.name == "Esther Charlestin")
    assert charlestin.party == "DEM"  # source label "DEM/PROG"
    assert any(c.name == "Write-In" for c in gov.candidates)


def test_multi_seat_house_district_marks_top_two_winners():
    got = by_key(parse())
    hd3 = got["VT HD-add-3"]
    winners = [c.name for c in hd3.candidates if c.winner]
    assert len(winners) == 2  # vf == 2 for this district
    assert winners == ["Rob North", "Matt Birong"]


def test_title_keeps_district_name():
    got = by_key(parse())
    assert got["VT SEN"].title == "US SENATOR"
    assert got["VT HD-add-1"].title == "STATE REPRESENTATIVE - ADDISON-1"
    assert got["VT SD-add-1"].title == "STATE SENATOR - ADDISON"


def test_parse_tolerates_junk():
    assert parse_vt(None, fetched_at=AT) == []
    assert parse_vt({}, fetched_at=AT) == []
    assert parse_vt([{"d": [{"o": [{"on": ""}]}]}], fetched_at=AT) == []


def _handler(payload_by_suffix, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        for suffix, body in payload_by_suffix.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    return handler


def test_fetch_follows_metadata_paths_and_timestamps(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    seen = []
    bodies = {f"{GUID}.json": (FIX / f"{GUID}.json").read_text()}
    for p in FIX.glob(f"{GUID}-*-2026*.json"):
        bodies[p.name] = p.read_text()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(bodies, seen))) as c:
            return await vt_sos.VtSos().fetch([parse_key("VT SEN"), RaceKey("VT", Level.STATE, "HD", "add-1")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"VT SEN", "VT HD-add-1"}
    assert seen[0] == f"{BASE}/{GUID}.json"
    assert sum("2026" in u for u in seen) == 4  # f, s, sn, h documents
    # lastUpdatedDate "06/11/2026 03:31 PM" is Eastern -> UTC.
    assert got["VT SEN"].source_updated_at == datetime(2026, 6, 11, 19, 31, tzinfo=timezone.utc)


def test_discovery_picks_general_election_by_date(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []
    index = [
        {
            "electionDate": "2026-08-11T00:00:00",
            "electionGuid": "primary-guid",
            "isGeneralElection": False,
            "isStateWideElection": True,
        },
        {
            "electionDate": "2026-11-03T00:00:00",
            "electionGuid": "general-guid",
            "isGeneralElection": True,
            "isStateWideElection": True,
        },
    ]
    bodies = {"elections.json": json.dumps(index)}
    bodies.update(
        {
            f"{GUID}{suffix}": (FIX / f"{GUID}{suffix}").read_text()
            for suffix in (".json",) + tuple(p.name[len(GUID) :] for p in FIX.glob(f"{GUID}-*-2026*.json"))
        }
    )
    bodies["general-guid.json"] = (FIX / f"{GUID}.json").read_text().replace(GUID, "general-guid")
    for p in FIX.glob(f"{GUID}-*-2026*.json"):
        bodies[f"general-guid{p.name[len(GUID) :]}"] = p.read_text()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(bodies, seen))) as c:
            return await vt_sos.VtSos().fetch([parse_key("VT GOV")], c)

    assert set(by_key(asyncio.run(go()))) == {"VT GOV"}
    assert seen[0] == f"{BASE}/elections.json"
    assert f"{BASE}/general-guid.json" in seen


def test_unavailable_when_date_missing_from_index(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    seen = []
    bodies = {"elections.json": json.dumps([])}

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler(bodies, seen))) as c:
            return await vt_sos.VtSos().fetch([parse_key("VT GOV")], c)

    with pytest.raises(SourceUnavailable, match="no 2026-11-03"):
        asyncio.run(go())


def test_winners_come_only_from_the_feed_not_from_ranking_a_partial_count():
    doc = {
        "d": [
            {
                "o": [
                    {
                        "on": "GOVERNOR",
                        "vf": 1,
                        "cs": [
                            {
                                "rc": [
                                    {"cid": 1, "cn": "A", "pn": "REPUBLICAN", "vc": 10, "isWinner": False},
                                    {"cid": 2, "cn": "B", "pn": "DEMOCRATIC", "vc": 9, "isWinner": False},
                                ]
                            }
                        ],
                    }
                ]
            }
        ]
    }
    (gov,) = parse_vt([doc], fetched_at=AT)
    assert not any(c.winner for c in gov.candidates)
