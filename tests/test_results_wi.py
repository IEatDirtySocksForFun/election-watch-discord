import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import wi_dane
from electionwatch.sources.results.wi_dane import COUNTIES, parse_dane, partial_title, race_key

FIX = Path(__file__).parent / "fixtures" / "wi_dane"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)


def load() -> str:
    return (FIX / "Election-Result.html").read_text(encoding="utf-8")


def by_key(results):
    return {format_key(race_result.key): race_result for race_result in results}


def test_partial_title_names_the_counties():
    assert partial_title("Governor / Lieutenant Governor") == "Governor / Lieutenant Governor (partial: 1 county)"
    assert len(COUNTIES) == 1


def test_statewide_and_district_keys_from_the_real_page():
    keys = by_key(parse_dane(load(), fetched_at=AT))
    assert set(keys) == {"WI GOV", "WI-02", "WI SD-15"}
    assert keys["WI GOV"].key.level is Level.GOVERNOR
    assert keys["WI-02"].key.level is Level.HOUSE
    assert keys["WI SD-15"].key.level is Level.STATE


def test_candidates_parties_and_precincts():
    gov = by_key(parse_dane(load(), fetched_at=AT))["WI GOV"]
    assert [candidate.name for candidate in gov.candidates] == [
        "Tom Tiffany /David Varnam",
        "David Crowley /Sarah Godlewski",
    ]
    assert [candidate.party for candidate in gov.candidates] == ["REP", "DEM"]
    assert (gov.precincts_reporting, gov.precincts_total) == (0, 282)
    assert gov.expected_vote_pct is None  # 0 of 282 reported
    assert gov.url == "https://elections.wi.gov/elections-voting/results"
    assert gov.source == "wi_dane"


def test_party_prefixed_names_are_cleaned():
    senate = by_key(parse_dane(load(), fetched_at=AT))["WI SD-15"]
    assert [candidate.name for candidate in senate.candidates] == [
        "Scott Fleming",
        "Mark Spreitzer",
        "Christopher Dean",
    ]
    assert senate.candidates[2].party == "PNP"


def test_write_in_rows_and_ballots_cast_are_dropped():
    names = {
        candidate.name for race_result in parse_dane(load(), fetched_at=AT) for candidate in race_result.candidates
    }
    assert not any(name.lower().startswith("write-in") for name in names)
    assert not any(
        "BALLOTS CAST" in (race_result.title or "").upper() for race_result in parse_dane(load(), fetched_at=AT)
    )


def test_votes_and_percent_are_read_from_the_markup():
    # Mid-count markup, same table shape as the fixture (see README.txt).
    html = """
    <div class="row" id="race0004">
      <div class="col-8 text-left"><h4>Governor / Lieutenant Governor</h4></div>
      <div class="col-12">Precincts Reporting (141 of 282)</div>
      <table><tbody>
        <tr><td class="text-left"><strong>REP Tom Tiffany /David Varnam</strong>
            <small><em>(REP)</em></small></td>
            <td class="text-left"><div class="progress"><div class="progress-bar"
                aria-valuenow="52.4"></div></div>52.4%</td>
            <td class="text-right">120,000</td></tr>
        <tr><td class="text-left"><strong>DEM David Crowley /Sarah Godlewski</strong>
            <small><em>(DEM)</em></small></td>
            <td class="text-left"><div class="progress"><div class="progress-bar"
                aria-valuenow="47.6"></div></div>47.6%</td>
            <td class="text-right">108,990</td></tr>
        <tr><td class="text-left"><strong>write-in:</strong>
            <small><em>(NON)</em></small></td>
            <td class="text-left">0.0%</td><td class="text-right">12</td></tr>
      </tbody></table>
    </div>
    """
    gov = parse_dane(html, fetched_at=AT)[0]
    assert gov.candidates[0].votes == 120000
    assert gov.candidates[0].pct == 52.4
    assert gov.total_votes == 228990  # recomputed, write-in row excluded
    assert (gov.precincts_reporting, gov.precincts_total) == (141, 282)
    assert gov.expected_vote_pct == 50.0


def test_county_offices_become_local():
    html = """
    <div class="row" id="race0026"><h4>Sheriff</h4>
      <table><tbody>
        <tr><td><strong>Kalvin Barrett</strong> <em>(DEM)</em></td>
            <td>100.0%</td><td class="text-right">5</td></tr>
      </tbody></table></div>
    """
    (only,) = parse_dane(html, fetched_at=AT)
    assert only.key.level is Level.LOCAL
    assert format_key(only.key) == "WI LOCAL sheriff"


def test_race_key_mapping():
    assert format_key(race_key("Governor / Lieutenant Governor")) == "WI GOV"
    assert format_key(race_key("Attorney General")) == "WI AG"
    assert format_key(race_key("State Treasurer")) == "WI TREAS"
    assert format_key(race_key("Representative in Congress District 3")) == "WI-03"
    assert format_key(race_key("State Senator District 9")) == "WI SD-09"
    assert format_key(race_key("Representative to the Assembly District 78")) == "WI HD-78"
    assert format_key(race_key("Clerk of Circuit Court")) == "WI LOCAL clerk-of-circuit-court"


def test_adapter_is_off_by_default():
    assert wi_dane.WiDane.scrape is True
    assert wi_dane.WiDane.enabled_by_default is False


def test_fetch_uses_the_results_page(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == wi_dane.URL
        return httpx.Response(200, text=load())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await wi_dane.WiDane().fetch([parse("WI GOV"), parse("WI-02")], client)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"WI GOV", "WI-02"}


def test_fetch_surfaces_a_blocked_page():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda race_result: httpx.Response(403))) as client:
            return await wi_dane.WiDane().fetch([parse("WI GOV")], client)

    with pytest.raises(SourceUnavailable, match="HTTP 403"):
        asyncio.run(go())


def test_list_races_and_other_states():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=load())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            both = await wi_dane.WiDane().list_races("WI", client)
            other = await wi_dane.WiDane().list_races("MN", client)
            return both, other

    races, other = asyncio.run(go())
    assert other == []
    assert [format_key(race_key) for race_key, _ in races] == ["WI GOV", "WI-02", "WI SD-15"]
