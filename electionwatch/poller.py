"""Polling orchestration: fetch, persist, apply per-guild rules, and build Posts.

Each tick asks every enabled source that is due under its request budget
(electionwatch/ratelimit.py): results, calls, exit polls and odds for the watched races, polls
every 30 minutes, plus the races users asked about (electionwatch/demand.py). Everything is
stored in MongoDB, which is what the data commands (/bettingmarkets, /polls, /earlyvote) read."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx

from electionwatch.config import load_races
from electionwatch.models import ExitPoll, OddsSnapshot, Poll, RaceCall, RaceResult, normalize_party, to_doc
from electionwatch.poster import Post, Poster
from electionwatch.racekeys import describe
from electionwatch.settings import GuildSettings, odds_alert_for, odds_alerts_anywhere, post_due
from electionwatch.store.base import Store

logger = logging.getLogger(__name__)

_PARTY_COLOR = {"REP": 0xE03C31, "DEM": 0x2F6FDB}


TOP_CANDIDATES = 5  # results posts show only the leading five candidates


def _party_marker(party: str | None) -> str:
    try:  # full party table (electionwatch/data/parties.yaml) when installed
        from electionwatch.parties import party_emoji

        return party_emoji(party)
    except ImportError:
        return {"REP": "🔴", "DEM": "🔵"}.get(normalize_party(party) or "", "⚪")


def format_result(race_result: RaceResult, *, turnout: bool = False) -> Post:
    """Render a compact, source-neutral race result."""
    lines = []
    leaders = sorted(race_result.candidates, key=lambda candidate: candidate.votes, reverse=True)[:TOP_CANDIDATES]
    for candidate in leaders:
        pct = f" · {candidate.pct:.1f}%" if candidate.pct is not None else ""
        party = f" ({candidate.party})" if candidate.party else ""
        winner = " ✓" if candidate.winner else ""
        lines.append(f"{_party_marker(candidate.party)} {candidate.name}{party} {candidate.votes:,}{pct}{winner}")
    reporting = []
    if race_result.expected_vote_pct is not None:
        reporting.append(f"{race_result.expected_vote_pct:.1f}".removesuffix(".0") + "% expected vote")
    if race_result.precincts_reporting is not None:
        precincts = f"{race_result.precincts_reporting:,}"
        if race_result.precincts_total is not None:
            precincts += f"/{race_result.precincts_total:,}"
        reporting.append(f"{precincts} precincts")
    if reporting:
        lines.append(" · ".join(reporting))
    if turnout:
        numbers = []
        if race_result.turnout_pct is not None:
            numbers.append(f"{race_result.turnout_pct:g}%")
        if race_result.total_votes is not None and race_result.registered_voters is not None:
            numbers.append(f"{race_result.total_votes:,}/{race_result.registered_voters:,} ballots")
        if numbers:
            lines.append("Turnout: " + " · ".join(numbers))
    return Post(
        "results",
        str(race_result.key),
        race_result.title or describe(race_result.key),
        lines,
        url=race_result.url,
        footer=f"Source: {race_result.source}",
    )


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


#: How an outlet is named in call posts, where its code alone would mislead.
OUTLET_NAMES = {"CIVICAPI": "civicAPI (its own decision desk)"}


def format_call(call: RaceCall) -> Post:
    outlet = OUTLET_NAMES.get(call.outlet, call.outlet)
    if call.retracted:
        lines = [f"Call retracted: {call.winner}", f"Outlet: {outlet}"]
    else:
        party = f" ({call.party})" if call.party else ""
        lines = [f"{_party_marker(call.party)} Projected winner: {call.winner}{party}", f"Outlet: {outlet}"]
    return Post(
        "call",
        str(call.key),
        f"Race call · {describe(call.key)}",
        lines,
        url=call.url,
        color=None if call.retracted else _PARTY_COLOR.get(normalize_party(call.party) or ""),
        footer=f"Called {_as_utc(call.called_at):%b %d %H:%M} UTC",
    )


def format_exit_poll(poll: ExitPoll) -> Post:
    lines = poll.summary.splitlines() if poll.summary else ["Exit poll available"]
    lines.append(f"Outlet: {poll.outlet}")
    return Post("exitpoll", str(poll.key), f"Exit poll · {describe(poll.key)}", lines, url=poll.url)


def is_partial(race_result: RaceResult) -> bool:
    """County-merge adapters flag a partial-state tally in the title: "(partial: N counties)"."""
    return "(partial:" in (race_result.title or "")


def best_result(answers: list[RaceResult]) -> RaceResult | None:
    """First complete answer in priority order, else the first partial one."""
    return next((answer for answer in answers if not is_partial(answer)), answers[0] if answers else None)


#: Which kind of source each fetch method belongs to (ratelimit.FLOORS, EW_INTERVALS).
METHOD_KIND = {
    "fetch": "results",
    "fetch_calls": "calls",
    "fetch_exit_polls": "exitpolls",
    "fetch_odds": "odds",
    "fetch_polls": "polls",
}
POLLS_CACHE = "polls"
#: A results source's last answer stands in for it while it is not due or backing off, for at
#: most this long; after that the fallbacks answer for its races.
ANSWER_REUSE = timedelta(minutes=10)
ODDS_CHECKED = "odds_checked"  # races the odds refresh asked about and no market answered


def polls_ident(source: str, key) -> str:
    return f"{key}|{source}"


def dump_polls(polls: list[Poll]) -> str:
    def json_value(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    return json.dumps([{field: json_value(value) for field, value in to_doc(poll).items()} for poll in polls])


def load_polls(text: str) -> list[Poll]:
    from datetime import date

    from electionwatch.models import PollResult
    from electionwatch.racekeys import parse

    polls = []
    for record in json.loads(text):
        record = dict(record)
        record["key"] = parse(record["key"])
        record["results"] = [PollResult(**poll_result) for poll_result in record["results"]]
        for field in ("end_date", "start_date"):
            if record.get(field):
                record[field] = date.fromisoformat(record[field])
        polls.append(Poll(**record))
    return polls


def swing_detail(swing, history: list[OddsSnapshot], fallback: OddsSnapshot):
    """Turn detect_swing()'s answer into (move in points or None, latest snapshot).

    electionwatch/swings.py (a separate package) returns (move, earlier, latest); accept a
    plain truthy value too so a simpler detector still works.
    """
    if isinstance(swing, tuple) and len(swing) == 3:
        move, _earlier, latest = swing
        return float(move), latest
    latest = max(history, key=lambda snapshot: snapshot.ts) if history else fallback
    return None, latest


class Poller:
    def __init__(
        self,
        store: Store,
        sources: dict[str, list],
        poster: Poster,
        clock,
        http: httpx.AsyncClient,
        *,
        fetch_timeout: float = 30.0,
        rates=None,
        demand=None,
    ) -> None:
        self.store = store
        self.sources = sources
        self.poster = poster
        self.clock = clock
        self.http = http
        self.fetch_timeout = fetch_timeout
        self.default_races = load_races()
        # (guild, kind, race) -> body of the last results post. Only the top candidates are
        # shown, so a vote change further down (multi-seat races) can render an identical
        # post; those are skipped. In memory: at most one repeat after a restart.
        self._last_body: dict[tuple[int, str, str], tuple[str, ...]] = {}
        self._backoff: dict[str, tuple[int, datetime | None]] = {}  # source -> (failures, skip until)
        self._flags_specials: set[str] = set()  # sources seen answering for a special key itself
        # Request budgets (electionwatch/ratelimit.py): None asks every source every tick.
        self.rates = rates
        self.demand = demand
        self._answers: dict[str, tuple] = {}  # source -> (when, its last results), see _fetch
        self._odds_checked: dict[str, datetime] = {}  # race -> when its "no market" marker was written
        self._lock = asyncio.Lock()  # /refresh and the minute loop never overlap

    def _watched(self, guild: GuildSettings) -> set[str]:
        return set(guild.important) if guild.important else {str(race.key) for race in self.default_races}

    @staticmethod
    def _enabled(source) -> bool:
        """Every adapter in the sources dict that says it's available right now (has its API key,
        etc.). Whether it's enabled at all was decided when the dict was built (__main__ or the
        replay sandbox)."""
        try:
            return source.available()
        except Exception:
            logger.warning("Could not check availability of source %s", getattr(source, "name", "?"))
            return False

    def last_answers(self, source, method: str, keys: list) -> list:
        """A results source's last answer for keys if it is recent, else []. It stands in
        while the source is not due, backing off or failing, so a fallback is not asked about
        races an official feed answered, and the posted numbers don't flip to another
        outlet's and back during a short outage."""
        kind = METHOD_KIND.get(method, method)
        at, answers = self._answers.get(f"{getattr(source, 'name', repr(source))}:{kind}", (None, []))
        if at is None or self.clock() - at > ANSWER_REUSE:
            return []
        asked = {str(key) for key in keys}
        return [record for record in answers if str(record.key) in asked]

    async def _fetch(self, source, method: str, keys: list):
        """Fetch with a per-source back-off: after a failure the source is skipped for 1, 2, 4,
        8, then 15 minutes, so a site that starts refusing us (CNN answers 406 to bursts) is not
        hammered every minute. A success resets it."""
        name = getattr(source, "name", repr(source))
        failures, until = self._backoff.get(name, (0, None))
        now = self.clock()
        kind = METHOD_KIND.get(method, method)
        budget_name = f"{name}:{kind}"  # one adapter can be a results and a calls source
        if until is not None and now < until:
            return self.last_answers(source, method, keys)
        if self.rates is not None and not self.rates.due(budget_name, kind):
            return self.last_answers(source, method, keys)
        tracking = self.rates.track(budget_name, kind) if self.rates is not None else nullcontext()
        try:
            with tracking:
                got = await asyncio.wait_for(getattr(source, method)(keys, self.http), timeout=self.fetch_timeout)
        except Exception:
            failures += 1
            self._backoff[name] = (failures, now + timedelta(minutes=min(2 ** (failures - 1), 15)))
            raise
        if failures:
            self._backoff.pop(name, None)
        if kind == "results":
            self._answers[budget_name] = (now, list(got))
        return got

    async def _post(self, guild: GuildSettings, kind: str, ref: str, post: Post, now: datetime) -> None:
        if guild.channel_id is None or guild.paused:
            return
        ident, body = (guild.guild_id, kind, ref), tuple(post.lines)
        if kind == "results" and self._last_body.get(ident) == body:
            return
        try:
            await self.poster.post(guild.guild_id, guild.channel_id, post)
            await self.store.mark_posted(guild.guild_id, kind, ref, now)
            self._last_body[ident] = body
        except Exception:
            logger.exception("Could not deliver %s post to guild %s", kind, guild.guild_id)

    async def tick(self) -> None:
        async with self._lock:
            await self._tick()

    def _demanded(self, kind: str, keys: list) -> list:
        """keys plus the races users asked about with a command (electionwatch/demand.py)."""
        if self.demand is None:
            return keys
        from electionwatch.racekeys import parse

        demanded, seen = list(keys), {str(key) for key in keys}
        for text in self.demand.items(kind):
            if text in seen:
                continue
            try:
                demanded.append(parse(text))
            except ValueError:
                continue
            seen.add(text)
        return demanded

    async def _tick(self) -> None:
        now = self.clock()
        guilds = await self.store.all_guilds()
        if not guilds:
            return
        watched = set().union(*(self._watched(guild) for guild in guilds))
        keys = [race.key for race in self.default_races if str(race.key) in watched]
        known = {str(key): key for key in keys}
        # /watch can add races which are not in the YAML defaults.
        from electionwatch.racekeys import parse

        for text in watched - known.keys():
            try:
                key = parse(text)
            except ValueError:
                logger.warning("Ignoring invalid watched race key %s", text)
                continue
            keys.append(key)
            known[text] = key

        await self._results(now, guilds, keys)
        # Outlets and markets don't flag special elections (OH/FL Senate 2026 are specials):
        # ask them for the plain key too and file their answers under the watched special.
        self._aliases = {
            replace(key, special=False): key for key in keys if key.special and replace(key, special=False) not in keys
        }
        outlet_keys = keys + list(self._aliases)
        await self._calls(now, guilds, outlet_keys)
        await self._exit_polls(now, guilds, outlet_keys)
        await self._odds(now, guilds, self._demanded("odds", outlet_keys))
        await self._polls(now, self._demanded("polls", keys))

    def _canonical_batch(self, source, results: list) -> list:
        """Apply the special-election alias to one source's answers, unless the source flags
        specials itself (it answered for a special key, now or earlier): then its plain-key
        answers are the *regular* race (CA SEN vs CA SEN S in 2024, GA 2020, OK 2022). Aliasing
        those made the regular and the special call alternate as "new" every tick."""
        name = getattr(source, "name", repr(source))
        if any(getattr(race_result.key, "special", False) for race_result in results):
            self._flags_specials.add(name)
        if name in self._flags_specials:
            return list(results)
        return [self._canonical(race_result) for race_result in results]

    def _canonical(self, race_result):
        """The result with its key replaced by the watched special race it stands for, if any."""
        special = getattr(self, "_aliases", {}).get(race_result.key)
        return replace(race_result, key=special) if special is not None else race_result

    async def _results(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        by_race: dict[str, list[RaceResult]] = defaultdict(list)
        selected: dict[str, RaceResult] = {}
        enabled = [source for source in self.sources.get("results", []) if self._enabled(source)]
        # Fallbacks (civicAPI, the outlet feeds) are asked only about races no official feed
        # answered completely: that keeps a big watch list from pulling every race from them
        # every minute. A guild that picked a fallback by name with /settings gets it for all.
        pinned = {name for guild in guilds for name in guild.results_sources}
        primary = [source for source in enabled if not getattr(source, "fallback", False) or source.name in pinned]
        fallback = [source for source in enabled if getattr(source, "fallback", False) and source.name not in pinned]
        await self._gather_results(primary, keys, by_race)
        complete = {race for race, answers in by_race.items() if any(not is_partial(answer) for answer in answers)}
        missing = [key for key in keys if str(key) not in complete]
        if missing:
            await self._gather_results(fallback, missing, by_race)
        # Adapter order is priority order: the first complete answer is the stored result. A
        # county merge titled "(partial: N counties)" only wins when nothing complete answered,
        # so a reachable state feed (tx_sos) or an enabled national fallback is preferred over
        # a partial-state county tally for the same race.
        for race, answers in by_race.items():
            selected[race] = best_result(answers)
        for race, race_result in selected.items():
            try:
                # The store's change time is compared with mark_posted() times, which are on
                # the poller's clock; adapters stamp fetched_at with the wall clock mid-tick.
                await self.store.upsert_result(replace(race_result, fetched_at=now))
            except Exception:
                logger.exception("Could not store results for %s", race)

        for guild in guilds:
            watched = self._watched(guild)
            allowed = set(guild.results_sources)
            for race in watched:
                candidates = by_race.get(race, [])
                race_result = best_result([answer for answer in candidates if not allowed or answer.source in allowed])
                if race_result is None:
                    continue
                try:
                    last = await self.store.last_posted(guild.guild_id, "results", race)
                    changed_at = await self.store.result_changed_at(race_result.key)
                    updated = changed_at is not None and (last is None or changed_at > last)
                    if post_due(guild, last, now, updated):
                        await self._post(
                            guild, "results", race, format_result(race_result, turnout=guild.post_turnout), now
                        )
                except Exception:
                    logger.exception("Could not process results for guild %s", guild.guild_id)

    async def _gather_results(self, sources: list, keys: list, by_race: dict) -> None:
        """Fetch results for keys from sources concurrently; append answers to by_race in
        adapter (priority) order. One slow state site must not hold up the others."""
        jobs = []
        for source in sources:
            source_keys = [key for key in keys if key.state in source.states]
            if source_keys:
                jobs.append((source, source_keys))
        answers = await asyncio.gather(
            *(self._fetch(source, "fetch", source_keys) for source, source_keys in jobs),
            return_exceptions=True,
        )
        for (source, source_keys), fetched in zip(jobs, answers):
            if isinstance(fetched, BaseException):
                logger.error("Results source %s failed", source.name, exc_info=fetched)
                fetched = self.last_answers(source, "fetch", source_keys)
            asked = {str(key) for key in source_keys}
            for race_result in fetched:
                race = str(race_result.key)
                if race in asked:
                    by_race[race].append(race_result)

    async def _calls(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        for source in self.sources.get("calls", []):
            if not self._enabled(source):
                continue
            try:
                calls = await self._fetch(source, "fetch_calls", keys)
            except Exception:
                logger.exception("Call source %s failed", source.name)
                continue
            for call in self._canonical_batch(source, calls):
                try:
                    new = await self.store.record_call(call)
                except Exception:
                    logger.exception("Could not store call for %s", call.key)
                    continue
                if not new:
                    continue
                for guild in guilds:
                    if str(call.key) in self._watched(guild) and guild.post_calls and call.outlet in guild.call_outlets:
                        await self._post(guild, "call", f"{call.key}:{call.outlet}", format_call(call), now)

    async def _exit_polls(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        for source in self.sources.get("exitpolls", []):
            if not self._enabled(source):
                continue
            try:
                polls = await self._fetch(source, "fetch_exit_polls", keys)
            except Exception:
                logger.exception("Exit-poll source %s failed", source.name)
                continue
            for poll in self._canonical_batch(source, polls):
                ref = f"{poll.key}:{poll.outlet}"
                for guild in guilds:
                    if str(poll.key) not in self._watched(guild) or not guild.post_exit_polls:
                        continue
                    if await self.store.last_posted(guild.guild_id, "exitpoll", ref) is None:
                        await self._post(guild, "exitpoll", ref, format_exit_poll(poll), now)

    async def _polls(self, now: datetime, keys: list) -> None:
        """Store each poll source's polls per race; /polls reads them from the store."""
        for source in self.sources.get("polls", []):
            if not self._enabled(source):
                continue
            name = getattr(source, "name", repr(source))
            if self.rates is not None and not self.rates.due(f"{name}:polls", "polls"):
                continue
            try:
                polls = await self._fetch(source, "fetch_polls", keys)
            except Exception:
                logger.exception("Poll source %s failed", name)
                continue
            by_key: dict[str, list[Poll]] = defaultdict(list)
            for poll in polls:
                by_key[str(poll.key)].append(poll)
            for key in keys:
                try:
                    await self.store.put_cached(
                        POLLS_CACHE, polls_ident(name, key), dump_polls(by_key.get(str(key), [])), now
                    )
                except Exception:
                    logger.exception("Could not store polls for %s", key)

    async def _odds(self, now: datetime, guilds: list[GuildSettings], keys: list) -> None:
        snapshots: list[OddsSnapshot] = []
        asked = False
        for source in self.sources.get("odds", []):
            if not self._enabled(source):
                continue
            name = getattr(source, "name", repr(source))
            if self.rates is not None and not self.rates.due(f"{name}:odds", "odds"):
                continue
            try:
                fetched = await self._fetch(source, "fetch_odds", keys)
            except Exception:
                logger.exception("Odds source %s failed", source.name)
                continue
            asked = True
            for snapshot in self._canonical_batch(source, fetched):
                try:
                    await self.store.add_odds(snapshot)
                    snapshots.append(snapshot)
                except Exception:
                    logger.exception("Could not store odds for %s", snapshot.key)
        if asked:
            # /bettingmarkets tells "no market for this race" apart from "not fetched yet" with this.
            priced = {str(snapshot.key) for snapshot in snapshots}
            for key in keys:
                race = str(key)
                written = self._odds_checked.get(race)
                if race in priced or (written is not None and now - written < timedelta(minutes=30)):
                    continue
                try:
                    await self.store.put_cached(ODDS_CHECKED, race, "", now)
                    self._odds_checked[race] = now
                except Exception:
                    logger.exception("Could not store the odds check for %s", key)
        if not snapshots or not any(odds_alerts_anywhere(guild) for guild in guilds):
            return
        try:
            from electionwatch.swings import detect_swing
        except ImportError:
            logger.debug("Swing detector is not installed; skipping odds alerts")
            return

        for guild in guilds:
            if not odds_alerts_anywhere(guild):
                continue
            watched = self._watched(guild)
            for snapshot in snapshots:
                if str(snapshot.key) not in watched or snapshot.market not in guild.odds_markets:
                    continue
                ref = f"{snapshot.key}:{snapshot.market}:{snapshot.outcome}"
                try:
                    # Per race: a race or category override can change all three (/bettingalerts).
                    alert = odds_alert_for(guild, snapshot.key)
                    if not alert.enabled:
                        continue
                    window = timedelta(minutes=alert.window)
                    last = await self.store.last_posted(guild.guild_id, "odds_swing", ref)
                    if last is not None and now - last < window:
                        continue
                    history = await self.store.odds_since(snapshot.key, snapshot.market, snapshot.outcome, now - window)
                    # detect_swing takes a timedelta window (see electionwatch/swings.py).
                    swing = detect_swing(history, alert.threshold, window)
                    if not swing:
                        continue
                    move, latest = swing_detail(swing, history, snapshot)
                    headline = f"{snapshot.market} · {snapshot.outcome}: {latest.prob:.1%}"
                    if move is not None:
                        headline += f" ({move:+.1f} pts)"
                    post = Post(
                        "odds_swing",
                        str(snapshot.key),
                        f"Odds swing · {describe(snapshot.key)}",
                        [headline, f"Swing within {alert.window} minutes"],
                        url=snapshot.url,
                    )
                    await self._post(guild, "odds_swing", ref, post, now)
                except Exception:
                    logger.exception("Could not process odds swing for guild %s", guild.guild_id)
