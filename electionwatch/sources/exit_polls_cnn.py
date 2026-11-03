"""CNN exit polls: the National Election Pool survey that ABC, CBS, CNN and NBC share.

CNN publishes an index of every exit-poll file of an election, then one JSON file per race
(and per voter subset; only the "Main" subset is read here):

    https://politics.api.cnn.io/config/exit-poll-dropdown-config/2026-11-03-GE.json
        {"dropdownConfig": {"GG": [{"stateAbbr": "PA", "subsetNumber": 0,
                                    "url": "exit-poll/2026-GG-PA.json", ...}, ...],
                            "SG": [...], "SP": [...], "HG": [...]}}
    https://politics.api.cnn.io/results/exit-poll/2026-GG-PA.json
        {"exitPollEmbargo": {"exitPollsRelease": 1793754000000, "exitPollsReleased": false},
         "hasData": false, "respondents": 0, "candidates": [...],
         "questions": [{"question": "Gender", "answers": [{"answer": "Male",
             "totalPercentage": 48, "candidateAnswers": [{"candidateId": 1, "percentage": 55}]}]}]}

Race codes: GG governor, SG Senate, SP Senate special (Ohio and Florida in 2026), HG the
national House vote. Each file is embargoed until the state's polls close
(``exitPollsRelease``, epoch ms) and carries every question with zeros until then.

CNN blocks bursts from one address, so the refresher reads the index once an hour, asks for
a file only after its release time and then at most every 15 minutes, and spaces requests.
Parsed polls are kept in the store (``exitpoll`` cache, one entry per race); the /exitpoll
command reads only the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

from electionwatch.models import Level, RaceKey
from electionwatch.racekeys import STATES, format_key
from electionwatch.sources.results._common import election_date

logger = logging.getLogger(__name__)

API = "https://politics.api.cnn.io"
INDEX_URL = API + "/config/exit-poll-dropdown-config/{date}-GE.json"
FILE_URL = API + "/results/{file}"  # the index gives "exit-poll/2026-GG-PA.json"
CACHE_KIND = "exitpoll"
#: Cache entry with the race list and release times, for autocomplete and "not out yet" replies.
INDEX_IDENT = "_index"
NATIONAL = "US House"

#: CNN race code -> (level, office, special, CNN page segment)
RACE_CODES = {
    "GG": (Level.GOVERNOR, "GOV", False, "governor"),
    "SG": (Level.SENATE, "SEN", False, "senate"),
    "SP": (Level.SENATE, "SEN", True, "senate"),
}


def race_ident(code: str, state: str) -> str | None:
    """'GG', 'PA' -> 'PA GOV'; 'SP', 'OH' -> 'OH SEN S'; 'HG', 'US' -> 'US House'."""
    if code == "HG" and state == "US":
        return NATIONAL
    race = RACE_CODES.get(code)
    if race is None or state not in STATES:
        return None
    level, office, special, _ = race
    return format_key(RaceKey(state, level, office, None, special))


def race_label(code: str, state: str) -> str:
    if code == "HG":
        return "U.S. House (national)"
    level = RACE_CODES[code][0]
    office = "Governor" if level is Level.GOVERNOR else "Senate"
    special = " special" if RACE_CODES[code][2] else ""
    return f"{STATES.get(state, state)} {office}{special}"


def page_url(code: str, state: str, year: str) -> str:
    base = f"https://www.cnn.com/election/{year}/exit-polls"
    if code == "HG":
        return f"{base}/national-results/general/house/0"
    slug = STATES.get(state, state).lower().replace(" ", "-")
    return f"{base}/{slug}/general/{RACE_CODES[code][3]}/0"


def parse_index(payload: dict) -> list[dict]:
    """The races of an election's index: one {ident, label, code, state, file} per race."""
    races = []
    for code, rows in (payload.get("dropdownConfig") or {}).items():
        for row in rows or []:
            if not isinstance(row, dict) or row.get("subsetNumber") != 0 or row.get("isDisabled"):
                continue
            state = str(row.get("stateAbbr") or "").upper()
            ident = race_ident(code, state)
            if ident is None or not row.get("url"):
                continue
            races.append(
                {"ident": ident, "label": race_label(code, state), "code": code, "state": state, "file": row["url"]}
            )
    return races


def release_time(payload: dict) -> float | None:
    """When the file's embargo lifts (epoch seconds), if it says."""
    embargo = payload.get("exitPollEmbargo") or {}
    raw = embargo.get("exitPollsRelease") or embargo.get("assignedPollsClose")
    try:
        return float(raw) / 1000.0
    except (TypeError, ValueError):
        return None


def released(payload: dict) -> bool:
    embargo = payload.get("exitPollEmbargo") or {}
    return bool(embargo.get("exitPollsReleased")) and bool(payload.get("hasData"))


def _percent(value) -> int | None:
    """A share, or None: CNN masks numbers as -1 (before release, and for groups too small to report)."""
    return value if isinstance(value, (int, float)) and value >= 0 else None


def parse_exit_poll(payload: dict, race: dict, year: str) -> dict:
    """One released exit-poll file -> the stored shape:
    {ident, label, state, url, respondents, updated, candidates: [[name, party]],
     questions: [{q, answers: [{a, share, by: [percent per candidate]}]}]}"""
    candidates = []
    order: dict[int, int] = {}
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("onExitPoll") is False:
            continue
        order[candidate.get("candidateId")] = len(candidates)
        party = candidate.get("majorParty") or candidate.get("bopPartyId") or candidate.get("partyAbbreviation")
        candidates.append([candidate.get("lastName") or candidate.get("fullName") or "?", party])
    questions = []
    for question in payload.get("questions") or []:
        answers = []
        for answer in question.get("answers") or []:
            by = [None] * len(candidates)
            for share in answer.get("candidateAnswers") or []:
                index = order.get(share.get("candidateId"))
                if index is not None:
                    by[index] = _percent(share.get("percentage"))
            answers.append(
                {
                    "a": (answer.get("answer") or answer.get("answerShort") or "").strip(),
                    "share": _percent(answer.get("totalPercentage")),
                    "by": by,
                }
            )
        if answers:
            questions.append({"q": " ".join(str(question.get("question") or "").split()), "answers": answers})
    return {
        "ident": race["ident"],
        "label": race["label"],
        "state": race["state"],
        "url": page_url(race["code"], race["state"], year),
        "respondents": payload.get("respondents"),
        "updated": payload.get("timestamp"),
        "candidates": candidates,
        "questions": questions,
    }


class CnnExitPolls:
    """Background refresher: CNN's exit-poll files into the store."""

    INDEX_TTL = 3600.0  # the race list
    FILE_TTL = 900.0  # one released file
    RETRY = 300.0  # a file past its release time that is still embargoed
    SPACING = 3.0  # seconds between two requests to CNN

    def __init__(
        self,
        http: httpx.AsyncClient,
        store,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        self.http = http
        self.store = store
        self._clock = clock
        self._sleep = sleep
        self._races: list[dict] = []
        self._index_at: float | None = None
        self._next: dict[str, float] = {}  # file -> earliest next request
        self._release: dict[str, float] = {}  # ident -> release time, for "not out yet" replies

    async def refresh(self) -> int:
        """Fetch what is due; returns the number of exit polls stored."""
        date = election_date()
        now = self._clock()
        if self._index_at is None or now - self._index_at >= self.INDEX_TTL:
            payload = await self._get(INDEX_URL.format(date=date))
            if payload is None:
                return 0
            self._races = parse_index(payload)
            self._index_at = now
            await self._save_index()
        stored = 0
        for race in self._races:
            if self._clock() < self._next.get(race["file"], 0.0):
                continue
            await self._sleep(self.SPACING)
            payload = await self._get(FILE_URL.format(file=race["file"]))
            now = self._clock()
            if payload is None:
                self._next[race["file"]] = now + self.RETRY
                continue
            if not released(payload):
                release = release_time(payload)
                if release is not None:
                    self._release[race["ident"]] = release
                self._next[race["file"]] = max(release or 0.0, now + self.RETRY)
                continue
            poll = parse_exit_poll(payload, race, date[:4])
            await self.store.put_cached(CACHE_KIND, race["ident"], json.dumps(poll), datetime.now(timezone.utc))
            self._release.pop(race["ident"], None)
            self._next[race["file"]] = now + self.FILE_TTL
            stored += 1
        await self._save_index()
        return stored

    async def _get(self, url: str) -> dict | None:
        try:
            resp = await self.http.get(url)
        except httpx.HTTPError as exc:
            logger.warning("CNN exit polls: %s unreachable: %s", url, exc)
            return None
        if resp.status_code != 200:
            logger.warning("CNN exit polls: %s answered %s", url, resp.status_code)
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _save_index(self) -> None:
        index = [{**race, "release": self._release.get(race["ident"])} for race in self._races]
        await self.store.put_cached(CACHE_KIND, INDEX_IDENT, json.dumps(index), datetime.now(timezone.utc))


async def stored_index(store) -> list[dict]:
    hit = await store.get_cached(CACHE_KIND, INDEX_IDENT)
    return json.loads(hit[1]) if hit else []


async def stored_polls(store, idents: list[str] | None = None) -> list[dict]:
    """Released exit polls from the store; ``idents`` limits them (None = all)."""
    if idents is None:
        idents = [ident for ident in await store.cached_idents(CACHE_KIND) if ident != INDEX_IDENT]
    hits = await asyncio.gather(*(store.get_cached(CACHE_KIND, ident) for ident in idents))
    return [json.loads(hit[1]) for hit in hits if hit]
