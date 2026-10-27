"""Matrix tests for the user-facing data commands: /odds, /polls, /faq and race autocomplete.

Every command is invoked through ``<command>.callback(interaction, ...)`` with a recording fake
interaction (tests/fake_interaction_data.py). Every message, embed, view and autocomplete
answer is validated against Discord's hard limits by one shared checker, and every flow must
acknowledge exactly once and reply ephemerally.

Checklist
---------
/odds
  [x] no race, DM                          -> default races.yaml overview
  [x] no race, guild without settings      -> default races.yaml overview
  [x] no race, guild with 0 watched        -> default races.yaml overview
  [x] no race, guild with 1 watched        -> that race only
  [x] no race, guild with 200 watched      -> overview fits 4096/6000
  [x] no race, guild whose watched keys are all invalid -> "No races are being watched."
  [x] single race in every key format (short, dashed, long, house, at-large, special,
      statewide, state legislature, lettered district, local slug, territory, lowercase)
  [x] invalid key (several kinds, incl. 6000-char garbage) -> one ephemeral error, no defer
  [x] race with no odds
  [x] Kalshi only / Polymarket only / both (Kalshi listed first)
  [x] 50 outcomes, 300-char outcome names, long URLs -> field limits held
  [x] 1h / 24h moves from stored history
  [x] stale stored odds + failing live source -> stale values with their timestamp
  [x] stale stored odds + working live source -> fresh values
  [x] odds source raising -> "no odds" embed, not a crash
  [x] flow: defer(ephemeral=True) THEN followup(ephemeral=True), nothing else
/polls
  [x] count 1..10 (with 12 polls available) -> exactly ``count`` fields
  [x] internals true / false
  [x] 0 polls; no poll source installed; invalid race key
  [x] duplicates merged across sources (pollster vs sponsor naming)
  [x] 300-char pollster names
  [x] 10 polls x 60 candidates -> total embed <= 6000
  [x] polls without start date / sample size / url
  [x] sources raising RuntimeError, SourceUnavailable, httpx timeout, asyncio timeout,
      and available() raising -> the other sources' polls still shown
  [x] a failed source does not pin an empty result in the 15-minute cache
/faq
  [x] no state -> state list + outlets, valid YAML, < 2000 with fences
  [x] every code in racekeys.STATES (51, incl. DC), upper and lower case -> < 2000 with
      fences, valid YAML, contains that state's official results URL
  [x] territories PR GU VI AS MP -> ephemeral "no FAQ" answer, not a crash
  [x] invalid codes -> ephemeral error
  [x] FAQ data unreadable -> ephemeral notice that leaks no server path
  [x] FAQ module not importable -> "FAQ data not installed"
race_autocomplete
  [x] empty / partial / case-insensitive input
  [x] territory in the watch list
  [x] DM -> default races are offered
  [x] guild with no watch list -> default races
  [x] > 25 matches truncated to 25
  [x] slow source in many states -> answers within Discord's 3 s deadline
  [x] local slug longer than 100 chars is never offered as a choice value
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
import yaml

from electionwatch.bot import navigator
from electionwatch.bot.discord import faq_command, odds_command, polls_command, race_autocomplete
from electionwatch.models import Level, OddsSnapshot, Poll, PollResult, RaceKey
from electionwatch.racekeys import STATES, TERRITORIES, describe, parse
from electionwatch.settings import GuildSettings
from electionwatch.sources.base import OddsSource, PollSource, ResultsSource, SourceUnavailable
from electionwatch.store.memory import MemoryStore
from fake_interaction_data import check_choices, check_embed, embed_total, FakeInteraction, make_client

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TX_SEN = parse("TX SEN")


@pytest.fixture(autouse=True)
def _clean_list_cache():
    navigator._list_cache.clear()
    yield
    navigator._list_cache.clear()


# --------------------------------------------------------------------------- fakes
class Store(MemoryStore):
    """MemoryStore with the optional latest_odds lookup CommandData uses when present."""

    async def latest_odds(self, key):
        return [item for item in self.odds if item.key == key]


class Odds(OddsSource):
    name = "fake_odds"

    def __init__(self, per_key=None, *, markets=("kalshi", "polymarket"), outcomes=("DEM", "REP"),
                 error: BaseException | None = None, url="https://kalshi.example/m"):
        self.per_key = per_key
        self.markets = markets
        self.outcomes = outcomes
        self.error = error
        self.url = url
        self.calls = 0

    async def fetch_odds(self, keys, client):
        self.calls += 1
        if self.error:
            raise self.error
        if self.per_key is not None:
            return [s for s in self.per_key if s.key in keys]
        return [
            OddsSnapshot(key, market, outcome, 0.5 if len(self.outcomes) <= 2 else 1 / len(self.outcomes),
                         NOW, url=self.url)
            for key in keys for market in self.markets for outcome in self.outcomes
        ]


class Polls(PollSource):
    name = "fake_polls"

    def __init__(self, polls=(), error: BaseException | None = None, avail_error=False):
        self.polls = list(polls)
        self.error = error
        self.avail_error = avail_error
        self.calls = 0

    def available(self):
        if self.avail_error:
            raise RuntimeError("env lookup failed")
        return True

    async def fetch_polls(self, keys, client):
        self.calls += 1
        if self.error:
            raise self.error
        return [p for p in self.polls if p.key in keys]


class AllStates(ResultsSource):
    name = "all_states"
    states = frozenset(STATES)

    def __init__(self, per_state=None, delay=0.0, long_slug=False):
        self.per_state = per_state or {}
        self.delay = delay
        self.long_slug = long_slug

    async def list_races(self, state, client):
        if self.delay:
            await asyncio.sleep(self.delay)
        races = list(self.per_state.get(state, []))
        if self.long_slug:
            races.append((RaceKey(state, Level.LOCAL, "LOCAL", "x" * 120), "Very long local race"))
        return races


def poll(pollster="Example", end=date(2026, 10, 26), key=TX_SEN, **changes):
    values = dict(
        key=key, pollster=pollster, end_date=end, start_date=end - timedelta(days=2),
        results=[PollResult("Doe", "DEM", 49.0), PollResult("Smith", "REP", 47.0)],
        sample_size=800, population="LV", url="https://polls.example/one", source="a",
    )
    values.update(changes)
    return Poll(**values)


async def run(command, client, *args, guild_id=1, **kwargs) -> FakeInteraction:
    interaction = FakeInteraction(client, guild_id=guild_id)
    await command.callback(interaction, *args, **kwargs)
    return interaction


def deferred_embed(interaction: FakeInteraction):
    assert interaction.kinds == ["defer", "followup"], interaction.kinds
    reply = interaction.assert_ephemeral_flow()
    assert reply.embed is not None and reply.content is None
    return reply.embed


def immediate_text(interaction: FakeInteraction) -> str:
    assert interaction.kinds == ["send_message"], interaction.kinds
    reply = interaction.assert_ephemeral_flow()
    return reply.content


# --------------------------------------------------------------------------- /odds overview
@pytest.mark.parametrize("where", ["dm", "guild_no_settings", "guild_zero_watched"])
async def test_odds_overview_defaults_to_races_yaml(where):
    store = Store()
    guild_id = None if where == "dm" else 1
    if where == "guild_zero_watched":
        await store.save_guild(GuildSettings(1, important=[]))
    client = make_client(store, {"odds": [Odds()]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, guild_id=guild_id))
    assert embed.title == "Watched race odds"
    assert "**TX SEN**" in embed.description and "Kalshi" in embed.description


@pytest.mark.parametrize("count", [1, 200])
async def test_odds_overview_for_watched_races(count):
    store = Store()
    keys = [f"TX HD-{n:03d}" for n in range(1, count + 1)] if count > 1 else ["GA GOV"]
    await store.save_guild(GuildSettings(1, important=keys))
    client = make_client(store, {"odds": [Odds(outcomes=("Candidate " + "x" * 40, "Other " + "y" * 40))]},
                         clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client))
    assert f"**{parse(keys[0])}**" in embed.description
    if count == 1:
        assert "TX SEN" not in embed.description
    else:
        assert len(embed.description) <= 4096 and embed_total(embed) <= 6000


async def test_odds_overview_skips_invalid_watched_keys():
    store = Store()
    await store.save_guild(GuildSettings(1, important=["ZZ SEN", "TX NOPE"]))
    client = make_client(store, {"odds": [Odds()]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client))
    assert embed.description == "No races are being watched."


async def test_odds_overview_without_any_odds():
    client = make_client(Store(), {"odds": []}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, guild_id=None))
    assert "No current odds" in embed.description


# --------------------------------------------------------------------------- /odds one race
KEY_FORMATS = [
    "TX SEN", "TX-SEN", "tx sen", "Texas Senate", "texas senate", "NC-01", "NC-1", "nc-1",
    "AK-AL", "North Carolina Congressional District 1", "OH SEN S", "OH SEN SPECIAL",
    "Ohio Senate Special", "GA GOV", "Georgia Governor", "TX LTGOV", "AZ AG", "TX RRC",
    "TX SD-05", "TX SD 5", "TX HD-121", "AK SD-A", "VT HD-CHT-6-7", "TX LOCAL harris-county-judge",
    "PR-AL", "GU-AL", "DC-AL", "District of Columbia Senate", "NC-01 S",
]


@pytest.mark.parametrize("text", KEY_FORMATS)
async def test_odds_single_race_in_every_key_format(text):
    key = parse(text)
    client = make_client(Store(), {"odds": [Odds()]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race=text))
    assert embed.title == f"Odds · {describe(key)}"
    assert [f.name for f in embed.fields] == ["Kalshi", "Polymarket"]


@pytest.mark.parametrize("text", [
    "ZZ SEN", "TX", "TX NOPE", "TX SEN X", "TX LOCAL", "   ", "Atlantis Senate", "TX-99999",
    pytest.param("TX " + "Q" * 6000, id="6000-chars"),
])
async def test_odds_invalid_key_is_one_ephemeral_error(text):
    client = make_client(Store(), {"odds": [Odds()]})
    content = immediate_text(await run(odds_command, client, race=text))
    assert content


@pytest.mark.parametrize("markets,expected", [
    ((), None),
    (("kalshi",), ["Kalshi"]),
    (("polymarket",), ["Polymarket"]),
    (("polymarket", "kalshi"), ["Kalshi", "Polymarket"]),
])
async def test_odds_market_combinations(markets, expected):
    client = make_client(Store(), {"odds": [Odds(markets=markets)]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    if expected is None:
        assert "No current prediction-market odds" in embed.description
        assert not embed.fields
    else:
        assert [f.name for f in embed.fields] == expected
        assert "50.0%" in embed.fields[0].value
        assert embed.footer.text == "Last update: 2026-10-27 12:00 UTC"


@pytest.mark.parametrize("outcomes,url", [
    (tuple(f"Candidate {n:02d}" for n in range(50)), "https://kalshi.example/m"),
    (tuple(f"{n:02d} " + "Long Name " * 30 for n in range(50)), "https://kalshi.example/m"),
    (("DEM", "REP"), "https://polymarket.example/" + "p" * 900),
    (tuple(f"Candidate {n:02d}" for n in range(50)), "https://polymarket.example/" + "p" * 300),
    (("x" * 2000,), None),
])
async def test_odds_many_long_outcomes_and_urls_stay_within_limits(outcomes, url):
    client = make_client(Store(), {"odds": [Odds(outcomes=outcomes, url=url)]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    assert len(embed.fields) == 2


async def test_odds_moves_1h_and_24h():
    store = Store()
    for hours, prob in ((20, 0.40), (0.5, 0.52)):
        await store.add_odds(OddsSnapshot(TX_SEN, "kalshi", "REP", prob, NOW - timedelta(hours=hours)))
    current = [OddsSnapshot(TX_SEN, "kalshi", "REP", 0.55, NOW, url="https://k.example")]
    client = make_client(store, {"odds": [Odds(per_key=current)]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    assert "1h +3.0 pts" in embed.fields[0].value
    assert "24h +15.0 pts" in embed.fields[0].value


async def test_stale_stored_odds_are_shown_when_the_live_source_fails():
    store = Store()
    old = NOW - timedelta(hours=3)
    await store.add_odds(OddsSnapshot(TX_SEN, "polymarket", "DEM", 0.31, old))
    client = make_client(store, {"odds": [Odds(error=httpx.ConnectError("down"))]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    assert "31.0%" in embed.fields[0].value
    assert embed.footer.text == f"Last update: {old:%Y-%m-%d %H:%M} UTC"


async def test_stale_stored_odds_are_replaced_by_live_odds():
    store = Store()
    await store.add_odds(OddsSnapshot(TX_SEN, "kalshi", "DEM", 0.31, NOW - timedelta(hours=3)))
    source = Odds(markets=("kalshi",), outcomes=("DEM",))
    client = make_client(store, {"odds": [source]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    assert source.calls == 1
    assert "50.0%" in embed.fields[0].value


@pytest.mark.parametrize("error", [RuntimeError("x"), SourceUnavailable("gone"),
                                   httpx.ReadTimeout("slow"), asyncio.TimeoutError()])
async def test_odds_source_errors_render_no_odds(error):
    client = make_client(Store(), {"odds": [Odds(error=error)]}, clock=lambda: NOW)
    embed = deferred_embed(await run(odds_command, client, race="TX SEN"))
    assert "No current prediction-market odds" in embed.description


# --------------------------------------------------------------------------- /polls
TWELVE = [poll(f"Pollster {n:02d}", date(2026, 10, 1) + timedelta(days=n)) for n in range(12)]


@pytest.mark.parametrize("count", range(1, 11))
async def test_polls_count_boundaries(count):
    client = make_client(Store(), {"polls": [Polls(TWELVE)]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", count=count))
    assert len(embed.fields) == count
    assert embed.fields[0].name == "Pollster 11"  # newest first


@pytest.mark.parametrize("internals", [True, False])
async def test_polls_internals_filter(internals):
    polls = [poll("Public"), poll("Campaign", date(2026, 10, 25), partisan="REP", sponsor="Smith campaign")]
    client = make_client(Store(), {"polls": [Polls(polls)]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", internals=internals))
    names = [f.name for f in embed.fields]
    assert ("Campaign INTERNAL (R)" in names) is internals
    assert "Public" in names
    assert ("included" if internals else "excluded") in embed.description


@pytest.mark.parametrize("internals", [True, False])
async def test_polls_none_available(internals):
    client = make_client(Store(), {"polls": [Polls([poll(partisan="DEM")] if not internals else [])]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", internals=internals))
    assert embed.description.startswith("No recent polls")


async def test_polls_without_any_source():
    client = make_client(Store(), {})
    embed = deferred_embed(await run(polls_command, client, "TX SEN"))
    assert embed.description == "No poll source is installed or available."


@pytest.mark.parametrize("text", ["ZZ SEN", "TX", "nonsense", pytest.param("TX " + "Q" * 6000, id="6000-chars")])
async def test_polls_invalid_key(text):
    client = make_client(Store(), {"polls": [Polls(TWELVE)]})
    assert immediate_text(await run(polls_command, client, text))


async def test_polls_duplicates_merged_across_sources():
    a = poll("N+ Univision", date(2026, 10, 20), source="votehub", sample_size=None, url=None)
    b = poll("YouGov", date(2026, 10, 18), sponsor="N+ Univision", source="270towin")
    exact_copy = poll("Example", source="votehub")
    exact_copy2 = poll("example ", source="270towin", population=None)
    client = make_client(Store(), {"polls": [Polls([a, exact_copy]), Polls([b, exact_copy2])]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", count=10))
    assert len(embed.fields) == 2
    assert "Sponsor: N+ Univision" in "".join(f.value for f in embed.fields)


async def test_polls_long_pollster_names_are_clipped():
    polls = [poll("P" * 300 + str(n), date(2026, 10, n + 1), partisan="DEM") for n in range(10)]
    client = make_client(Store(), {"polls": [Polls(polls)]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", count=10))
    assert all(len(f.name) <= 256 for f in embed.fields)


async def test_ten_polls_with_many_candidates_fit_the_6000_char_embed_total():
    def results(seed):
        return [PollResult(f"Candidate Number {n:02d}", "DEM" if n % 2 else "REP", seed + n / 10)
                for n in range(60)]

    polls = [poll(f"Pollster {n}", date(2026, 10, n + 1), results=results(n),
                  sponsor=f"Sponsor {n} " + "s" * 80) for n in range(10)]
    client = make_client(Store(), {"polls": [Polls(polls)]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN", count=10))
    assert embed.fields  # check_embed (run by the fake) enforced the 6000 total
    assert embed.fields[0].name == "Pollster 9"


async def test_polls_with_missing_optional_fields():
    bare = poll("Bare", start_date=None, sample_size=None, population=None, url=None, results=[])
    client = make_client(Store(), {"polls": [Polls([bare])]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN"))
    value = embed.fields[0].value
    assert value.startswith("Oct 26, 2026") and "No candidate results supplied" in value
    assert "Poll link" not in value


@pytest.mark.parametrize("bad", [
    Polls(error=RuntimeError("boom")),
    Polls(error=SourceUnavailable("gone")),
    Polls(error=httpx.ReadTimeout("slow")),
    Polls(error=asyncio.TimeoutError()),
    Polls([poll("Hidden")], avail_error=True),
])
async def test_poll_source_failures_leave_the_other_sources(bad):
    good = Polls([poll("Survivor")])
    client = make_client(Store(), {"polls": [bad, good]})
    embed = deferred_embed(await run(polls_command, client, "TX SEN"))
    assert [f.name for f in embed.fields] == ["Survivor"]


async def test_failed_poll_source_does_not_cache_an_empty_answer():
    clock = [NOW]
    source = Polls(error=httpx.ReadTimeout("slow"))
    client = make_client(Store(), {"polls": [source]}, clock=lambda: clock[0])
    first = deferred_embed(await run(polls_command, client, "TX SEN"))
    assert first.description.startswith("No recent polls")
    source.error, source.polls = None, [poll("Back")]
    clock[0] = NOW + timedelta(minutes=1)
    second = deferred_embed(await run(polls_command, client, "TX SEN"))
    assert [f.name for f in second.fields] == ["Back"]


async def test_successful_poll_answers_are_cached_for_15_minutes():
    clock = [NOW]
    source = Polls([poll("One")])
    client = make_client(Store(), {"polls": [source]}, clock=lambda: clock[0])
    for minutes in (0, 14):
        clock[0] = NOW + timedelta(minutes=minutes)
        deferred_embed(await run(polls_command, client, "TX SEN"))
    assert source.calls == 1
    clock[0] = NOW + timedelta(minutes=16)
    deferred_embed(await run(polls_command, client, "TX SEN"))
    assert source.calls == 2


# --------------------------------------------------------------------------- /faq
def faq_reply(interaction: FakeInteraction) -> str:
    content = immediate_text(interaction)
    assert len(content) < 2000
    return content


def yaml_body(content: str):
    assert content.startswith("```yaml\n") and content.endswith("\n```")
    return yaml.safe_load(content[len("```yaml\n"):-len("\n```")])


async def test_faq_without_a_state():
    content = faq_reply(await run(faq_command, make_client(Store())))
    for code in STATES:
        assert code in content
    assert "Associated Press" in content
    yaml_body(content)  # valid YAML, truncated or not


@pytest.mark.parametrize("code", sorted(STATES))
@pytest.mark.parametrize("case", [str.upper, str.lower])
async def test_faq_every_state(code, case):
    from electionwatch.faq.loader import load_states

    content = faq_reply(await run(faq_command, make_client(Store()), state=case(code)))
    data = yaml_body(content)
    assert load_states()[code]["official_results_url"] in content
    assert "truncated" not in content  # every shipped state fits whole
    assert set(data) >= {code}


@pytest.mark.parametrize("code", sorted(TERRITORIES))
async def test_faq_territories_answer_without_crashing(code):
    content = faq_reply(await run(faq_command, make_client(Store()), state=code.lower()))
    assert f"No FAQ data for {TERRITORIES[code]}" in content
    yaml_body(content)


@pytest.mark.parametrize("code", ["ZZ", "Texas", "T", "", "TXX", "U.S."])
async def test_faq_invalid_state(code):
    interaction = await run(faq_command, make_client(Store()), state=code or None)
    assert faq_reply(interaction)


async def test_faq_unreadable_data_does_not_leak_server_paths(monkeypatch):
    from electionwatch.faq import loader

    def broken():
        raise loader.FAQDataError("cannot read /var/lib/election-watch/states.yaml: [Errno 2]")

    monkeypatch.setattr(loader, "load_states", broken)
    content = faq_reply(await run(faq_command, make_client(Store()), state="TX"))
    assert content == "FAQ data not installed"


async def test_faq_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "electionwatch.faq.loader", None)
    content = faq_reply(await run(faq_command, make_client(Store()), state="TX"))
    assert content == "FAQ data not installed"


# --------------------------------------------------------------------------- autocomplete
async def autocomplete(client, current, guild_id=1):
    interaction = FakeInteraction(client, guild_id=guild_id)
    choices = await race_autocomplete(interaction, current)
    check_choices(choices)
    assert interaction.events == []  # autocomplete returns choices; it never responds itself
    return choices


def results_client(store=None, **source_kwargs):
    return make_client(store or Store(), {"results": [AllStates(**source_kwargs)]})


@pytest.mark.parametrize("current,expected", [
    ("", {"TX SEN", "TX GOV", "TX-07"}),
    ("sen", {"TX SEN"}),
    ("SEN", {"TX SEN"}),
    ("texas gov", {"TX GOV"}),
    ("tx-0", {"TX-07"}),
    ("district 7", {"TX-07"}),
    ("nothing matches", set()),
])
async def test_autocomplete_filters_case_insensitively(current, expected):
    races = {"TX": [(parse("TX GOV"), "Texas Governor"), (parse("TX-07"), "Texas District 7")]}
    choices = await autocomplete(results_client(per_state=races), current)
    assert {c.value for c in choices} == expected


async def test_autocomplete_offers_watched_territory():
    store = Store()
    await store.save_guild(GuildSettings(1, important=["PR-AL", "GU-AL"]))
    choices = await autocomplete(results_client(store), "puerto")
    assert [c.value for c in choices] == ["PR-AL"]
    assert "Resident Commissioner" in choices[0].name


async def test_autocomplete_in_dm_offers_default_races():
    choices = await autocomplete(results_client(), "", guild_id=None)
    assert "TX SEN" in {c.value for c in choices}


async def test_autocomplete_guild_without_watch_list_uses_defaults():
    store = Store()
    await store.save_guild(GuildSettings(1))
    choices = await autocomplete(results_client(store), "")
    assert {c.value for c in choices} == {"TX SEN"}


async def test_autocomplete_truncates_to_25():
    store = Store()
    await store.save_guild(GuildSettings(1, important=[f"TX HD-{n:03d}" for n in range(1, 201)]))
    choices = await autocomplete(results_client(store), "hd")
    assert len(choices) == 25


async def test_autocomplete_answers_in_time_with_a_slow_source_in_many_states(monkeypatch):
    monkeypatch.setattr(navigator, "LIST_TIMEOUT_SECONDS", 0.3)
    store = Store()
    await store.save_guild(GuildSettings(1, important=[f"{code} SEN" for code in sorted(STATES)[:20]]))
    started = time.monotonic()
    choices = await autocomplete(results_client(store, delay=10), "")
    assert time.monotonic() - started < 1.5   # 20 states x 0.3 s sequentially would be 6 s
    assert len(choices) == 20


async def test_autocomplete_never_offers_a_value_over_100_chars():
    choices = await autocomplete(results_client(long_slug=True), "")
    assert {c.value for c in choices} == {"TX SEN"}
