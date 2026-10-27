"""ny_nycboe: New York City Board of Elections recap CSVs (New York's block has no state feed)."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key, parse
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import _county, ny_nycboe

FIX = Path(__file__).parent / "fixtures" / "ny_nycboe"
AT = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)
CD10 = "Representative in Congress 10th Congressional District"


def by_key(results):
    return {format_key(r.key): r for r in results}


def recap(name, county, contest):
    return ny_nycboe.parse_recap((FIX / name).read_text(encoding="utf-8"),
                                 county=county, contest=contest, fetched_at=AT)


def merged():
    results = (recap("nyc_2024_senator_citywide.csv", "Citywide", "United States Senator")
               + recap("nyc_2024_cd10_new_york.csv", "New York", CD10)
               + recap("nyc_2024_cd10_kings.csv", "Kings", CD10))
    return by_key(_county.merge(results, source="ny_nycboe", url=ny_nycboe.HUMAN))


def test_senate_totals_come_from_the_total_for_block():
    sen = merged()["NY SEN"]
    assert sen.key.level is Level.SENATE
    cands = {c.name: (c.party, c.votes) for c in sen.candidates}
    assert cands["Kirsten E. Gillibrand"] == ("DEM", 1919931)
    assert cands["Michael D. Sapraicone"] == ("REP", 734629)
    assert sen.title == "United States Senator (partial: 1 counties)"


def test_machine_category_rows_are_not_candidates():
    names = {c.name for c in merged()["NY SEN"].candidates}
    assert not names & {"Public Counter", "Absentee / Military", "Affidavit", "Federal"}


def test_two_borough_files_add_up_to_one_race():
    cd10 = merged()["NY-10"]
    assert cd10.key.level is Level.HOUSE and cd10.key.district == "10"
    assert {c.name: c.votes for c in cd10.candidates}["Daniel Goldman"] == 93606 + 112600
    assert cd10.title.endswith("(partial: 2 counties)")
    assert cd10.total_votes == sum(c.votes for c in cd10.candidates)


def test_write_ins_keep_their_marker():
    cands = {c.name: c.party for c in merged()["NY-10"].candidates}
    assert cands["ADAM GAZA (Write-In)"] == "WRI"


def test_index_parsing_filters_date_and_stage():
    html = """
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00000800000Citywide United States Senator Citywide Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00101900010New York Representative in Congress 10th Congressional District Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00001900010Crossover Representative in Congress 10th Congressional District Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20240625Primary Election/00000800000Citywide United States Senator Citywide Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2022/20221108General Election/00000800000Citywide United States Senator Citywide Recap.csv">Recap</a>
    """
    entries = ny_nycboe.parse_index(html, "2024-11-05")
    assert [(e["county"], e["contest"]) for e in entries] == [
        ("Citywide", "United States Senator"),
        ("New York", "Representative in Congress 10th Congressional District"),
    ]
    assert ny_nycboe.parse_index(html, "2026-11-03") == []


def test_selection_prefers_citywide_and_matches_keys():
    html = """
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00000800000Citywide United States Senator Citywide Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00100800000New York United States Senator Citywide Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00101900010New York Representative in Congress 10th Congressional District Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00301900010Kings Representative in Congress 10th Congressional District Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00100600000New York City Comptroller Recap.csv">Recap</a>
    """
    entries = ny_nycboe.parse_index(html, "2024-11-05")
    picked = ny_nycboe.select_entries(entries, [parse("NY SEN")])
    assert len(picked) == 1 and picked[0]["county"] == "Citywide"
    both = ny_nycboe.select_entries(entries, [parse("NY-10")])
    assert [e["county"] for e in both] == ["New York", "Kings"]


def test_it_is_marked_as_a_scrape_and_off_by_default():
    # The vote.nyc index is HTML, so AGENTS.md treats this adapter as a scraper.
    assert ny_nycboe.NyNycBoe.scrape is True
    assert ny_nycboe.NyNycBoe.enabled_by_default is False


def test_race_key_mapping():
    key = lambda name: format_key(ny_nycboe.race_key(name))  # noqa: E731
    assert key("United States Senator") == "NY SEN"
    assert key("Representative in Congress 10th Congressional District") == "NY-10"
    assert key("Governor") == "NY GOV"
    assert key("Lieutenant Governor") == "NY LTGOV"
    assert key("State Senator 26th Senatorial District") == "NY SD-26"
    assert key("Member of the Assembly 65th Assembly District") == "NY HD-65"
    assert key("President Vice President") == "NY LOCAL president-vice-president"
    assert key("City Comptroller") == "NY LOCAL nyc-city-comptroller"


def test_fetch_downloads_only_matching_contests(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    index = """
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00000800000Citywide United States Senator Citywide Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00101900010New York Representative in Congress 10th Congressional District Recap.csv">Recap</a>
      <a href="/sites/default/files/pdf/election_results/2024/20241105General Election/00301900010Kings Representative in Congress 10th Congressional District Recap.csv">Recap</a>
    """
    fetched = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        fetched.append(url)
        if "election-results-summary" in url:
            return httpx.Response(200, text=index)
        if "00000800000" in url:
            return httpx.Response(200, content=(FIX / "nyc_2024_senator_citywide.csv").read_bytes())
        if "00101900010" in url:
            return httpx.Response(200, content=(FIX / "nyc_2024_cd10_new_york.csv").read_bytes())
        if "00301900010" in url:
            return httpx.Response(200, content=(FIX / "nyc_2024_cd10_kings.csv").read_bytes())
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await ny_nycboe.NyNycBoe().fetch([parse("NY SEN")], c)

    got = by_key(asyncio.run(go()))
    assert set(got) == {"NY SEN"}
    assert not any("01900010" in url for url in fetched)   # CD10 files were not requested


def test_fetch_raises_when_nothing_is_published(monkeypatch):
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>no results</body></html>")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await ny_nycboe.NyNycBoe().fetch([parse("NY SEN")], c)

    with pytest.raises(SourceUnavailable):
        asyncio.run(go())


@pytest.mark.parametrize("name", [
    "Governor/Lieutenant Governor", "Governor Lieutenant Governor", "Governor and Lieutenant Governor",
])
def test_joint_governor_ticket_is_the_governor_race(name):
    assert format_key(ny_nycboe.race_key(name)) == "NY GOV"
