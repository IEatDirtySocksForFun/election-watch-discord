import json
from pathlib import Path

import httpx
import pytest

from electionwatch.bot.exit_polls import (
    batches,
    exit_poll_embeds,
    headline_questions,
    race_choices,
    resolve_race,
    state_choices,
    summary_line,
)
from electionwatch.sources.exit_polls_cnn import (
    CACHE_KIND,
    INDEX_IDENT,
    NATIONAL,
    CnnExitPolls,
    parse_exit_poll,
    parse_index,
    race_ident,
    release_time,
    released,
)
from electionwatch.store.memory import MemoryStore

FIX = Path(__file__).parent / "fixtures" / "cnn_exit_polls"


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_race_idents():
    assert race_ident("GG", "PA") == "PA GOV"
    assert race_ident("SG", "TX") == "TX SEN"
    assert race_ident("SP", "OH") == "OH SEN S"
    assert race_ident("HG", "US") == NATIONAL
    assert race_ident("I4G", "CA") is None  # ballot measures are not races here
    assert race_ident("GG", "XX") is None


def test_index_keeps_the_main_subset_of_each_race():
    races = parse_index(_load("index_2026.json"))
    assert {race["ident"] for race in races} == {"PA GOV", "TX GOV", "TX SEN", "OH SEN S", NATIONAL}
    pa = next(race for race in races if race["ident"] == "PA GOV")
    assert pa == {
        "ident": "PA GOV",
        "label": "Pennsylvania Governor",
        "code": "GG",
        "state": "PA",
        "file": "exit-poll/2026-GG-PA.json",
    }


def test_an_embargoed_file_is_not_released_and_says_when_it_will_be():
    payload = _load("2026-GG-PA.json")
    assert not released(payload)
    assert release_time(payload) == 1793754000.0  # 8 PM ET, when Pennsylvania's polls close


def test_a_released_file_parses_to_candidates_and_answers():
    payload = _load("2024-SG-PA.json")
    assert released(payload)
    race = {"ident": "PA SEN", "label": "Pennsylvania Senate", "code": "SG", "state": "PA", "file": "x"}
    poll = parse_exit_poll(payload, race, "2024")
    assert poll["respondents"] == 2954
    assert poll["candidates"][:2] == [["Casey", "DEM"], ["McCormick", "REP"]]
    assert poll["url"] == "https://www.cnn.com/election/2024/exit-polls/pennsylvania/general/senate/0"
    age = next(question for question in poll["questions"] if question["q"] == "Age")
    assert age["answers"][0] == {"a": "18-29", "share": 12, "by": [55, 42]}
    assert [question["q"] for question in headline_questions(poll, 3)] == ["Gender", "Age", "Race"]
    assert summary_line(poll).startswith("[**Pennsylvania Senate**](https://www.cnn.com/") and "Casey" in summary_line(
        poll
    )


class FakeCnn:
    def __init__(self, files: dict[str, dict]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.asked.append(path)
        if path.startswith("/config/"):
            return httpx.Response(200, json=_load("index_2026.json"))
        name = path.rsplit("/", 1)[-1]
        if name in self.files:
            return httpx.Response(200, json=self.files[name])
        return httpx.Response(404)


def _released_pa() -> dict:
    return _load("2024-SG-PA.json")


async def test_refresher_waits_for_the_release_then_stores_the_poll(monkeypatch):
    monkeypatch.delenv("ELECTION_DATE", raising=False)
    embargoed = _load("2026-GG-PA.json")
    fake = FakeCnn({"2026-GG-PA.json": embargoed})
    store = MemoryStore()
    clock = [1793750000.0]  # an hour before Pennsylvania's release
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        refresher = CnnExitPolls(client, store, clock=lambda: clock[0], sleep=lambda _: _noop())
        assert await refresher.refresh() == 0
        index = json.loads((await store.get_cached(CACHE_KIND, INDEX_IDENT))[1])
        assert next(race for race in index if race["ident"] == "PA GOV")["release"] == 1793754000.0
        asked = len(fake.asked)
        clock[0] += 600  # still embargoed: not asked again
        await refresher.refresh()
        assert [path for path in fake.asked[asked:] if "GG-PA" in path] == []
        fake.files["2026-GG-PA.json"] = _released_pa()
        clock[0] = 1793754100.0  # after the release
        assert await refresher.refresh() == 1
    assert (await store.get_cached(CACHE_KIND, "PA GOV")) is not None


async def _noop() -> None:
    return None


async def _store_with_polls() -> MemoryStore:
    store = MemoryStore()
    races = parse_index(_load("index_2026.json"))
    await store.put_cached(CACHE_KIND, INDEX_IDENT, json.dumps(races), _now())
    for ident, label, state in (("PA GOV", "Pennsylvania Governor", "PA"), ("TX SEN", "Texas Senate", "TX")):
        race = {"ident": ident, "label": label, "code": "GG", "state": state, "file": "x"}
        await store.put_cached(CACHE_KIND, ident, json.dumps(parse_exit_poll(_released_pa(), race, "2026")), _now())
    return store


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


async def test_one_race_one_state_or_all():
    store = await _store_with_polls()
    race = await exit_poll_embeds(store, "PA GOV")
    assert len(race) == 1 and race[0].title.endswith("Pennsylvania Governor") and len(race[0].fields) == 6
    texas = await exit_poll_embeds(store, state="TX")
    assert [embed.title for embed in texas] == ["🗳️ Exit poll · Texas Senate"]
    everything = await exit_poll_embeds(store)
    assert len(everything) == 1 and "Pennsylvania Governor" in everything[0].description
    assert "Texas Senate" in everything[0].description
    assert all(sum(len(embed) for embed in group) <= 6000 and len(group) <= 10 for group in batches(everything))


async def test_nothing_released_yet_names_the_next_releases():
    store = MemoryStore()
    races = [{**race, "release": 1793754000.0} for race in parse_index(_load("index_2026.json"))]
    await store.put_cached(CACHE_KIND, INDEX_IDENT, json.dumps(races), _now())
    reply = await exit_poll_embeds(store, "PA GOV")
    assert isinstance(reply, str) and "No exit polls out yet" in reply and "<t:1793754000:t>" in reply
    assert isinstance(await exit_poll_embeds(store), str)


async def test_races_resolve_from_codes_names_and_the_national_vote():
    store = await _store_with_polls()
    assert await resolve_race(store, "pa gov") == "PA GOV"
    assert await resolve_race(store, "Pennsylvania Governor") == "PA GOV"
    assert await resolve_race(store, "national house") == NATIONAL
    with pytest.raises(ValueError):
        await resolve_race(store, "NY-14")
    assert ("Pennsylvania Governor", "PA GOV") in await race_choices(store, "penn")
    assert ("Texas", "TX") in await state_choices(store, "tex")
