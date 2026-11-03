import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from electionwatch.models import Level
from electionwatch.racekeys import format_key
from electionwatch.sources.base import SourceUnavailable
from electionwatch.sources.results import sd_sos
from electionwatch.sources.results.sd_sos import SdSos, media_info, parse_sd, race_key

FIX = Path(__file__).parent / "fixtures" / "sd_sos" / "SD"
AT = datetime(2026, 10, 27, 16, 30, tzinfo=timezone.utc)
CSVS = (
    "2026_general_swr.csv",
    "2026_general_bq.csv",
    "2026_general_leg.csv",
    "2026_general_cty.csv",
    "2026_general_mun.csv",
)
#: Election-morning fetch, before any precinct had reported.
LIVE_AT = datetime(2026, 11, 3, 13, 50, tzinfo=timezone.utc)


def texts():
    return [(FIX / name).read_text(encoding="utf-8") for name in CSVS]


def media():
    return (FIX / "2026_general_media.html").read_text(encoding="utf-8")


def by_key():
    return {format_key(race_result.key): race_result for race_result in parse_sd(texts(), media(), fetched_at=AT)}


def test_media_page_header():
    media_details = media_info(media())
    assert media_details["date"] == "2026-11-03"
    assert media_details["election_type"] == "General"
    assert media_details["ballots_cast"] == 6441
    assert media_details["registered_voters"] == 622188
    # "10/27/2026 10:53 AM CT" -> 15:53 UTC
    assert media_details["updated_at"] == datetime(2026, 10, 27, 15, 53, tzinfo=timezone.utc)


def test_senate_and_at_large_house():
    got = by_key()
    senate = got["SD SEN"]
    assert senate.key.level is Level.SENATE
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in senate.candidates] == [
        ("Mike Rounds", "REP", 2132),
        ("Brian L Bengs", "IND", 2099),
    ]
    assert senate.candidates[0].pct == 50.39  # 0-1 fraction in the feed
    assert senate.precincts_reporting == 122
    assert senate.precincts_total == 671
    assert senate.expected_vote_pct == 18.18
    assert senate.source_updated_at == datetime(2026, 10, 27, 15, 53, tzinfo=timezone.utc)
    assert senate.turnout_pct == 1.04  # 6441 / 622188

    house = got["SD-AL"]
    assert house.key.level is Level.HOUSE
    assert house.key.district == "AL"
    assert [candidate.name for candidate in house.candidates] == ["Marty Jackley", "Nicole 'Nikki' Gronli"]


def test_statewide_executive_offices():
    got = by_key()
    assert got["SD GOV"].title == "Governor and Lieutenant Governor"
    assert [candidate.name for candidate in got["SD GOV"].candidates] == [
        "Larry Rhoden and Tony Venhuizen",
        "Dan Ahlers and Steven McCleerey",
    ]
    assert [candidate.party for candidate in got["SD SOS"].candidates] == ["DEM", "LIB", "REP"]
    assert got["SD AG"].candidates[0].pct == 100.0
    assert got["SD AUD"].key.level is Level.GOVERNOR
    assert got["SD TREAS"].candidates[0].name == "Melissa Hull"
    assert got["SD LANDCOM"].key.office == "LANDCOM"
    # No shared code exists for the utilities commission: it stays an identifiable LOCAL race.
    assert "SD LOCAL public-utilities-commissioner" in got


def test_legislative_races_and_two_seat_house_districts():
    got = by_key()
    assert got["SD SD-01"].key.level is Level.STATE
    assert got["SD SD-01"].candidates[0].name == "Michael H. Rohl"
    assert got["SD SD-01"].precincts_reporting == 16
    assert got["SD SD-01"].precincts_total == 43
    # District 02 has reported nothing yet: no precinct figure is published as zero.
    assert got["SD SD-02"].precincts_reporting is None
    assert got["SD SD-02"].precincts_total == 12

    hd = got["SD HD-01"]
    assert hd.key.office == "HD"
    assert [candidate.name for candidate in hd.candidates] == ["Daniel Kjos", "Nick Fosness", "Courtney Gale Enfield"]
    assert hd.title == "State Representative (District 01)"


def test_ballot_measures_get_compact_slugs():
    got = by_key()
    i = got["SD LOCAL constitutional-amendment-i"]
    assert [candidate.name for candidate in i.candidates] == ["No", "Yes"]
    assert i.candidates[0].votes == 1669
    assert i.candidates[0].party is None
    assert got["SD LOCAL constitutional-amendment-l"].candidates[0].votes == 1706


def test_county_and_city_races_stay_distinct():
    got = by_key()
    codington = got["SD LOCAL county-auditor-codington"]
    grant = got["SD LOCAL county-auditor-grant"]
    assert codington.candidates[0].name == "Brenda Hanten"
    assert grant.candidates[0].votes == 15
    assert grant.precincts_reporting == 4 and grant.precincts_total == 17
    assert "SD LOCAL referendum-83" in got

    council = got["SD LOCAL city-council-member-brookings"]
    assert council.title == "City Council Member (Brookings)"
    assert council.candidates[0].name == "Lisa Hager"
    assert council.candidates[0].party == "NPA"


def test_race_key_mapping():
    assert format_key(race_key("United States Senator")) == "SD SEN"
    assert format_key(race_key("United States Representative")) == "SD-AL"
    assert format_key(race_key("Governor and Lieutenant Governor")) == "SD GOV"
    assert format_key(race_key("Secretary of State")) == "SD SOS"
    assert format_key(race_key("Attorney General")) == "SD AG"
    assert format_key(race_key("Commissioner of School and Public Lands")) == "SD LANDCOM"
    assert format_key(race_key("State Senator", "District 7")) == "SD SD-07"
    assert format_key(race_key("State Representative", "District 12")) == "SD HD-12"
    # Lettered House sub-districts keep their letter.
    assert format_key(race_key("State Representative", "District 26A")) == "SD HD-26A"
    assert format_key(race_key("State Senator", "District 26")) == "SD SD-26"
    assert format_key(race_key("City Council Member", "Brookings")) == "SD LOCAL city-council-member-brookings"


def test_election_morning_feed_parses_before_any_vote():
    election_day_media = (FIX / "2026_general_electionday_media.html").read_text(encoding="utf-8")
    details = media_info(election_day_media)
    assert details["date"] == "2026-11-03"
    assert details["election_type"] == "General"
    assert details["registered_voters"] == 628629
    # hidVoterTurnout=0: no ballots counted yet, so no turnout figure is invented.
    assert details["ballots_cast"] is None
    # "10/30/2026 5:41 PM CT" -> 22:41 UTC
    assert details["updated_at"] == datetime(2026, 10, 30, 22, 41, tzinfo=timezone.utc)

    csv_text = (FIX / "2026_general_electionday_swr.csv").read_text(encoding="utf-8")
    got = {
        format_key(race_result.key): race_result
        for race_result in parse_sd(csv_text, election_day_media, fetched_at=LIVE_AT)
    }
    senate = got["SD SEN"]
    assert [(candidate.name, candidate.party, candidate.votes) for candidate in senate.candidates] == [
        ("Mike Rounds", "REP", 0),
        ("Brian L Bengs", "IND", 0),
    ]
    assert senate.candidates[0].pct == 0.0
    # "0/671": the total is published, the reported count is zero, not missing.
    assert senate.precincts_reporting is None
    assert senate.precincts_total == 671
    assert senate.expected_vote_pct is None
    assert senate.total_votes == 0
    assert senate.turnout_pct is None
    assert senate.source_updated_at == datetime(2026, 10, 30, 22, 41, tzinfo=timezone.utc)


def test_lettered_house_subdistricts_do_not_overwrite_each_other():
    csv_text = (FIX / "2026_general_electionday_leg.csv").read_text(encoding="utf-8")
    got = {format_key(race_result.key): race_result for race_result in parse_sd(csv_text, fetched_at=LIVE_AT)}
    assert set(got) == {"SD HD-26A", "SD HD-26B", "SD HD-28A", "SD HD-28B"}
    assert [candidate.name for candidate in got["SD HD-26A"].candidates] == ["Ron Frederick", "Eric E. Emery"]
    assert [candidate.name for candidate in got["SD HD-26B"].candidates] == ["Angi Hanzlik", "Lewis J. GrassRope"]
    assert got["SD HD-26B"].title == "State Representative (District 26B)"
    assert got["SD HD-26A"].precincts_total == 15
    assert got["SD HD-26B"].precincts_total == 22
    assert got["SD HD-28A"].precincts_total == 31
    assert [candidate.name for candidate in got["SD HD-28B"].candidates] == ["Travis Ismay"]


def test_partial_feed_does_not_crash():
    # Only one group fetched: the parser reports what it has.
    got = parse_sd((FIX / "2026_general_swr.csv").read_text(encoding="utf-8"), fetched_at=AT)
    assert {format_key(race_result.key) for race_result in got} >= {"SD SEN", "SD-AL", "SD GOV"}
    assert got[0].source_updated_at is None  # no media page -> no timestamp


def test_live_county_areas_with_a_vote_centre_suffix():
    # Live 2026-11-03 rows: counties that count at vote centres append the suffix to AreaNum,
    # and the county-commissioner heading repeats the district name.
    got = {
        format_key(race_result.key): race_result
        for race_result in parse_sd(
            (FIX / "2026_general_county_areas.csv").read_text(encoding="utf-8"), fetched_at=LIVE_AT
        )
    }
    assert set(got) == {
        "SD LOCAL county-auditor-codington",
        "SD LOCAL sheriff-brookings-brookings-vote-center",
        "SD LOCAL county-commissioner-campbell-campbell",
    }
    sheriff = got["SD LOCAL sheriff-brookings-brookings-vote-center"]
    # The heading already names the county: the vote-centre area is not repeated in the title.
    assert sheriff.title == "Sheriff - Brookings"
    assert [(candidate.name, candidate.party) for candidate in sheriff.candidates] == [
        ("David Biteler", "REP"),
        ("Manuel 'Manny' Langstraat", "IND"),
    ]
    assert sheriff.precincts_reporting is None
    assert sheriff.precincts_total == 10
    assert all(race_result.key.level is Level.LOCAL for race_result in got.values())


def test_live_municipal_questions_and_wards():
    got = {
        format_key(race_result.key): race_result
        for race_result in parse_sd(
            (FIX / "2026_general_municipal_areas.csv").read_text(encoding="utf-8"), fetched_at=LIVE_AT
        )
    }
    assert len(got) == 4
    assert [candidate.name for candidate in got["SD LOCAL city-council-member-brookings"].candidates] == [
        "Al Austreim",
        "Lisa Hager",
        "Shawn Hostler",
        "Wade Wantoch",
        "Brianna Doran",
    ]
    assert got["SD LOCAL city-council-member-platte-ward-01"].title == "City Council Member (Platte Ward-01)"
    titles = {race_result.title: race_result for race_result in got.values()}
    question = titles["Question : TEMPORARY ONE PERCENT (1%) MUNICIPAL GROSS RECEIPTS TAX (Milbank)"]
    assert [(candidate.name, candidate.party) for candidate in question.candidates] == [("Yes", None), ("No", None)]
    assert question.precincts_total == 7
    assert titles["Bond Issue : Armour School District 21-1 Bond"].precincts_total == 10


# --- the media page answers its error redirect about half the time -------------------------------

ERROR_REDIRECT = (FIX / "2026_general_media_error_302.html").read_text(encoding="utf-8")
ELECTION_DAY_MEDIA = (FIX / "2026_general_electionday_media.html").read_text(encoding="utf-8")
CSV_BODY = (FIX / "2026_general_electionday_swr.csv").read_text(encoding="utf-8")


def sd_client(media_bodies):
    """Serve the media page from `media_bodies` in order, and the CSVs from the fixture."""
    seen = {"media": 0, "csv": 0}

    def handler(request):
        if "ResultsMedia.aspx" in str(request.url):
            index = min(seen["media"], len(media_bodies) - 1)
            seen["media"] += 1
            status, body = media_bodies[index]
            return httpx.Response(status, text=body)
        seen["csv"] += 1
        return httpx.Response(200, text=CSV_BODY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_error_redirect_is_retried_once(monkeypatch):
    monkeypatch.delenv("SD_EID", raising=False)
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    client, seen = sd_client([(302, ERROR_REDIRECT), (200, ELECTION_DAY_MEDIA)])
    async with client:
        got = await SdSos().fetch([race_key("United States Senator")], client)
    assert [format_key(race_result.key) for race_result in got] == ["SD SEN"]
    assert got[0].precincts_total == 671  # the retry restored the media page's figures
    assert got[0].source_updated_at == datetime(2026, 10, 30, 22, 41, tzinfo=timezone.utc)
    assert seen["media"] == 2
    assert seen["csv"] >= 1


async def test_another_elections_page_is_refused_before_the_csvs(monkeypatch):
    monkeypatch.delenv("SD_EID", raising=False)
    monkeypatch.setenv("ELECTION_DATE", "2024-11-05")
    client, seen = sd_client([(200, ELECTION_DAY_MEDIA)])
    async with client:
        with pytest.raises(SourceUnavailable, match="reporting the 2026-11-03 election"):
            await SdSos().fetch([race_key("United States Senator")], client)
    assert seen["csv"] == 0


async def test_two_error_redirects_keep_the_counts(monkeypatch):
    # The page failed both times: the CSVs are still the state's numbers, so they are reported
    # without the turnout/stamp the page would have supplied.
    monkeypatch.delenv("SD_EID", raising=False)
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    client, seen = sd_client([(302, ERROR_REDIRECT), (302, ERROR_REDIRECT)])
    async with client:
        got = await SdSos().fetch([race_key("United States Senator")], client)
    assert [format_key(race_result.key) for race_result in got] == ["SD SEN"]
    assert got[0].source_updated_at is None
    assert got[0].turnout_pct is None
    assert got[0].precincts_total == 671
    assert seen["media"] == 2  # one read before the CSVs, one after


async def test_pinned_election_is_read_without_the_media_page(monkeypatch):
    # SD_EID names the election, so a broken media page costs the turnout figures only.
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.setenv("SD_EID", "684")
    client, seen = sd_client([(302, ERROR_REDIRECT), (302, ERROR_REDIRECT)])
    async with client:
        got = await SdSos().fetch([race_key("United States Senator")], client)
    assert [format_key(race_result.key) for race_result in got] == ["SD SEN"]
    assert got[0].source_updated_at is None
    assert seen["csv"] >= 1


async def test_media_page_timeout_does_not_lose_the_counts(monkeypatch):
    monkeypatch.delenv("SD_EID", raising=False)
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")

    def handler(request):
        if "ResultsMedia.aspx" in str(request.url):
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, text=CSV_BODY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        got = await SdSos().fetch([race_key("United States Senator")], client)
    assert [format_key(race_result.key) for race_result in got] == ["SD SEN"]
    assert got[0].turnout_pct is None
    assert got[0].precincts_total == 671


async def test_a_hanging_media_page_cannot_hold_up_the_counts(monkeypatch):
    # Live on 2026-11-03 the media page sometimes accepted the connection and never answered,
    # while the CSVs replied at once. The read is bounded, so the counts still arrive.
    monkeypatch.delenv("SD_EID", raising=False)
    monkeypatch.setenv("ELECTION_DATE", "2026-11-03")
    monkeypatch.setattr(sd_sos, "MEDIA_TIMEOUT", 0.05)
    seen = {"media": 0, "csv": 0}

    async def handler(request):
        if "ResultsMedia.aspx" in str(request.url):
            seen["media"] += 1
            await asyncio.sleep(5)
            return httpx.Response(200, text=ELECTION_DAY_MEDIA)
        seen["csv"] += 1
        return httpx.Response(200, text=CSV_BODY)

    started = time.monotonic()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        got = await SdSos().fetch([race_key("United States Senator")], client)
    assert time.monotonic() - started < 2  # two bounded reads, not two five-second hangs
    assert [format_key(race_result.key) for race_result in got] == ["SD SEN"]
    assert got[0].precincts_total == 671
    assert got[0].turnout_pct is None
    assert got[0].source_updated_at is None
    assert seen["media"] == 2
    assert seen["csv"] >= 1
