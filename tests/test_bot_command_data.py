from datetime import date, datetime, timedelta, timezone

import pytest

from electionwatch.bot.command_data import CommandData, format_odds, format_odds_overview, format_polls, merge_polls
from electionwatch.models import OddsSnapshot, Poll, PollResult
from electionwatch.racekeys import parse
from electionwatch.sources.base import OddsSource, PollSource

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
KEY = parse("TX SEN")


def snapshot(market="kalshi", outcome="REP", prob=0.55, ts=NOW, url="https://market.example/race"):
    return OddsSnapshot(KEY, market, outcome, prob, ts, url=url)


def poll(pollster="Example", end_date=date(2026, 10, 26), **changes):
    values = dict(
        key=KEY,
        pollster=pollster,
        results=[PollResult("Doe", "DEM", 49.0), PollResult("Smith", "REP", 47.0)],
        end_date=end_date,
        start_date=date(2026, 10, 24),
        sample_size=800,
        population="LV",
        url="https://polls.example/one",
    )
    values.update(changes)
    return Poll(**values)


class FakeStore:
    def __init__(self, odds=()):
        self.odds = list(odds)

    async def latest_odds(self, key):
        return [item for item in self.odds if item.key == key]

    async def add_odds(self, item):
        self.odds.append(item)

    async def odds_since(self, key, market, outcome, since):
        return [
            item for item in self.odds
            if item.key == key and item.market == market and item.outcome == outcome and item.ts >= since
        ]


class FakeOdds(OddsSource):
    name = "fake"

    def __init__(self, values=()):
        self.values = list(values)
        self.calls = 0

    async def fetch_odds(self, keys, client):
        self.calls += 1
        return self.values


class FakePolls(PollSource):
    name = "fake"

    def __init__(self, values=()):
        self.values = list(values)
        self.calls = 0

    async def fetch_polls(self, keys, client):
        self.calls += 1
        return self.values


def test_odds_format_has_market_links_moves_and_update_time():
    embed = format_odds(
        KEY,
        [snapshot(), snapshot("polymarket", "DEM", 0.46)],
        {("kalshi", "REP"): {1: 2.0, 24: -1.5}},
    )

    assert [field.name for field in embed.fields] == ["Kalshi", "Polymarket"]
    assert "55.0%" in embed.fields[0].value
    assert "1h +2.0 pts" in embed.fields[0].value
    assert "24h -1.5 pts" in embed.fields[0].value
    assert "https://market.example/race" in embed.fields[0].value
    assert "2026-10-27 12:00 UTC" in embed.footer.text


def test_odds_overview_is_one_compact_line_per_race():
    embed = format_odds_overview([(KEY, [snapshot(), snapshot("polymarket", "DEM", 0.46)])])
    assert len(embed.description.splitlines()) == 1
    assert "Kalshi" in embed.description and "Polymarket" in embed.description


def test_poll_merge_prefers_more_fields_and_sorts_newest_first():
    sparse = poll(sponsor=None, sample_size=None, start_date=None, url=None)
    complete = poll(sponsor="Newsroom")
    older = poll("Older", date(2026, 10, 20))

    merged = merge_polls([sparse, older, complete])

    assert len(merged) == 2
    assert merged[0] is complete
    assert merged[1] is older


def test_poll_format_has_sample_sponsor_markers_internal_tag_and_link():
    internal = poll(partisan="REP", sponsor="Campaign")
    embed = format_polls(KEY, [internal])

    assert "INTERNAL (R)" in embed.fields[0].name
    assert "n=800 LV" in embed.fields[0].value
    assert "Sponsor: Campaign" in embed.fields[0].value
    assert "🔵 Doe 49.0%" in embed.fields[0].value
    assert "🔴 Smith 47.0%" in embed.fields[0].value
    assert "https://polls.example/one" in embed.fields[0].value
    assert "partisan internals excluded" not in format_polls(KEY, [internal], internals=False).description


@pytest.mark.asyncio
async def test_fresh_stored_odds_avoid_fetch_and_live_odds_are_cached_for_60_seconds():
    source = FakeOdds([snapshot(prob=0.6)])
    store = FakeStore([snapshot(prob=0.55)])
    clock = [NOW]
    data = CommandData(store, {"odds": [source]}, object(), lambda: clock[0])

    assert (await data.get_odds(KEY))[0].prob == 0.55
    assert source.calls == 0

    clock[0] += timedelta(seconds=121)
    assert (await data.get_odds(KEY))[0].prob == 0.6
    assert source.calls == 1
    clock[0] += timedelta(seconds=59)
    await data.get_odds(KEY)
    assert source.calls == 1
    clock[0] += timedelta(seconds=1)
    await data.get_odds(KEY)
    assert source.calls == 2


@pytest.mark.asyncio
async def test_odds_history_supplies_one_and_24_hour_moves():
    store = FakeStore([
        snapshot(prob=0.50, ts=NOW - timedelta(hours=23)),
        snapshot(prob=0.52, ts=NOW - timedelta(minutes=30)),
        snapshot(prob=0.55),
    ])
    data = CommandData(store, {}, object(), lambda: NOW)

    embed = await data.odds_embed(KEY)

    assert "1h +3.0 pts" in embed.fields[0].value
    assert "24h +5.0 pts" in embed.fields[0].value


@pytest.mark.asyncio
async def test_poll_sources_are_merged_deduped_and_cached_for_15_minutes():
    first = FakePolls([poll(sponsor=None)])
    second = FakePolls([poll(sponsor="Newsroom")])
    clock = [NOW]
    data = CommandData(FakeStore(), {"polls": [first, second]}, object(), lambda: clock[0])

    values = await data.get_polls(KEY)
    assert len(values) == 1 and values[0].sponsor == "Newsroom"
    assert first.calls == second.calls == 1
    clock[0] += timedelta(minutes=14)
    await data.get_polls(KEY)
    assert first.calls == second.calls == 1
    clock[0] += timedelta(minutes=1)
    await data.get_polls(KEY)
    assert first.calls == second.calls == 2


@pytest.mark.asyncio
async def test_missing_poll_source_is_explained():
    data = CommandData(FakeStore(), {}, object(), lambda: NOW)
    embed = await data.polls_embed(KEY)
    assert "No poll source is installed or available" in embed.description
