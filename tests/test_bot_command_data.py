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
            item
            for item in self.odds
            if item.key == key and item.market == market and item.outcome == outcome and item.ts >= since
        ]

    async def get_cached(self, kind, ident):
        return getattr(self, "cache", {}).get((kind, ident))

    async def put_cached(self, kind, ident, data, at):
        self.__dict__.setdefault("cache", {})[(kind, ident)] = (at, data)


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
async def test_odds_come_from_the_store_only_and_unknown_races_are_queued():
    from electionwatch.demand import Demand

    source = FakeOdds([snapshot(prob=0.6)])
    store = FakeStore([snapshot(prob=0.55)])
    data = CommandData(store, {"odds": [source]}, object(), lambda: NOW, demand=Demand())

    for _ in range(3):
        assert (await data.get_odds(KEY))[0].prob == 0.55
    assert source.calls == 0
    other = parse("GA SEN")
    embed = await data.odds_embed(other)
    assert "queued for the next odds refresh (about every 1 min)" in embed.description
    assert data.demand.items("odds") == ["GA SEN"] and source.calls == 0


@pytest.mark.asyncio
async def test_a_race_the_refresh_found_no_market_for_says_so():
    from electionwatch.poller import ODDS_CHECKED

    store = FakeStore()
    await store.put_cached(ODDS_CHECKED, "TX SEN", "", NOW)
    data = CommandData(store, {"odds": [FakeOdds([])]}, object(), lambda: NOW)
    assert "No current prediction-market odds" in (await data.odds_embed(KEY)).description


@pytest.mark.asyncio
async def test_odds_history_supplies_one_and_24_hour_moves():
    store = FakeStore(
        [
            snapshot(prob=0.50, ts=NOW - timedelta(hours=23)),
            snapshot(prob=0.52, ts=NOW - timedelta(minutes=30)),
            snapshot(prob=0.55),
        ]
    )
    data = CommandData(store, {}, object(), lambda: NOW)

    embed = await data.odds_embed(KEY)

    assert "1h +3.0 pts" in embed.fields[0].value
    assert "24h +5.0 pts" in embed.fields[0].value


@pytest.mark.asyncio
async def test_stored_poll_answers_are_merged_and_deduped_without_calling_the_sources():
    from electionwatch.poller import POLLS_CACHE, dump_polls, polls_ident

    first = FakePolls([poll(sponsor=None)])
    second = FakePolls([poll(sponsor="Newsroom")])
    second.name = "fake_polls_2"
    store = FakeStore()
    data = CommandData(store, {"polls": [first, second]}, object(), lambda: NOW)
    assert await data.get_polls(KEY) is None  # nothing stored yet
    for source in (first, second):
        await store.put_cached(POLLS_CACHE, polls_ident(source.name, KEY), dump_polls(source.values), NOW)
    values = await data.get_polls(KEY)
    assert len(values) == 1 and values[0].sponsor == "Newsroom"
    assert first.calls == second.calls == 0


@pytest.mark.asyncio
async def test_missing_poll_source_is_explained():
    data = CommandData(FakeStore(), {}, object(), lambda: NOW)
    embed = await data.polls_embed(KEY)
    assert "No poll source is installed or available" in embed.description


def test_merge_polls_joins_sponsor_and_fieldwork_copies():
    from datetime import date

    from electionwatch.bot.command_data import merge_polls
    from electionwatch.models import Poll, PollResult
    from electionwatch.racekeys import parse

    def res():
        return [PollResult("Talarico", "DEM", 50.0), PollResult("Paxton", "REP", 45.0)]

    a = Poll(parse("TX SEN"), "N+ Univision", res(), date(2026, 10, 27), source="wikipedia")
    b = Poll(
        parse("TX SEN"), "YouGov", res(), date(2026, 10, 21), sponsor="N+ Univision", sample_size=1000, source="votehub"
    )
    c = Poll(parse("TX SEN"), "Emerson College", res(), date(2026, 10, 22))  # other firm: kept
    merged = merge_polls([a, b, c])
    assert [p.pollster for p in merged] == ["Emerson College", "YouGov"]


def test_format_polls_stays_under_discords_6000_character_embed_limit():
    # Found by tools/live_command_check.py: Discord answered 400 "Embed size exceeds maximum
    # size of 6000" for ten polls with many long candidate names.
    from datetime import date

    from electionwatch.models import Poll, PollResult

    name = "Bartholomew-Maximilian Featherstonehaugh-Wolfeschlegelsteinhausen Jr."
    polls = [
        Poll(
            KEY,
            f"{name} Research {p}",
            [PollResult(f"{name} {c}", "DEM", 10.0) for c in range(12)],
            date(2026, 10, 30 - p),
            sponsor=f"{name} Committee {p}",
            url="https://example.org/p",
        )
        for p in range(12)
    ]
    embed = format_polls(KEY, polls, count=10)
    assert len(embed) <= 6000
    assert 1 <= len(embed.fields) < 10
    assert embed.description.startswith(f"Showing {len(embed.fields)} most recent poll")
