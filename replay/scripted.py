"""Scripted race calls and synthetic prediction-market odds, driven by the replay timelines.

Neither is real data: they exist so a replay run exercises the "call" and "odds_swing" paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from electionwatch.models import OddsSnapshot, RaceCall, RaceKey
from electionwatch.sources.base import CallSource, OddsSource, register
from replay.source import ReplaySource


@dataclass
class ScriptedCall:
    key: str
    at: datetime
    winner: str
    party: str | None
    outlet: str = "AP"
    retracted: bool = False


@dataclass
class ScriptedSwing:
    """Force `outcome`'s price from p0 to p1 linearly over [start, start+minutes]."""

    key: str
    outcome: str
    start: datetime
    minutes: float
    p0: float
    p1: float


def default_calls(src: ReplaySource, call_at_eevp: float = 95.0, outlet: str = "AP") -> list[ScriptedCall]:
    """Call each race for its final leader at the first snapshot with >= call_at_eevp expected vote
    (or 15 min after the last snapshot if the series never gets there)."""
    calls = []
    for race_key, series in src.timelines.items():
        final = series[-1].result
        if not final.candidates:
            continue
        lead = max(final.candidates, key=lambda candidate: candidate.votes)
        at = next(
            (snapshot.ts for snapshot in series if (snapshot.result.expected_vote_pct or 0) >= call_at_eevp),
            series[-1].ts + timedelta(minutes=15),
        )
        calls.append(ScriptedCall(race_key, at, lead.name, lead.party, outlet))
    return calls


@register
class ScriptedCallSource(CallSource):
    name = "replay_calls"
    outlet = "AP"
    enabled_by_default = False

    def __init__(self, calls: list[ScriptedCall], clock: Callable[[], datetime]) -> None:
        self.calls = calls
        self.clock = clock

    async def fetch_calls(self, keys, client=None) -> list[RaceCall]:
        want = {str(call_key): call_key for call_key in keys}
        now = self.clock()
        return [
            RaceCall(want[call.key], call.outlet, call.winner, call.party, call.at, retracted=call.retracted)
            for call in self.calls
            if call.key in want and call.at <= now
        ]


def implied_prob(margin_pct: float, eevp: float) -> float:
    """Toy model: certainty grows with the leader's margin and the share of the vote counted."""
    return 1 / (1 + math.exp(-0.6 * margin_pct * math.sqrt(max(eevp, 1.0) / 100)))


@register
class ScriptedOddsSource(OddsSource):
    name = "replay_odds"
    enabled_by_default = False

    def __init__(
        self,
        results: ReplaySource,
        clock: Callable[[], datetime],
        swings: list[ScriptedSwing] | None = None,
        market: str = "kalshi",
    ) -> None:
        self.results = results
        self.clock = clock
        self.swings = swings or []
        self.market = market

    async def fetch_odds(self, keys, client=None) -> list[OddsSnapshot]:
        now = self.clock()
        snapshots = []
        for key in keys:
            snapshot = self.results.current(key)
            if snapshot is None:
                continue
            race_result = snapshot.result
            total = sum(candidate.votes for candidate in race_result.candidates) or 1
            parties = {}
            for candidate in race_result.candidates:
                if candidate.party and candidate.party not in parties:
                    parties[candidate.party] = 100.0 * candidate.votes / total
            if len(parties) < 2:
                continue
            (pa, sa), (pb, sb) = sorted(parties.items(), key=lambda kv: -kv[1])[:2]
            p = implied_prob(sa - sb, race_result.expected_vote_pct or 50.0)
            probs = {pa: p, pb: 1 - p}
            for sw in self.swings:
                if sw.key == str(key) and sw.start <= now <= sw.start + timedelta(minutes=sw.minutes):
                    f = (now - sw.start) / timedelta(minutes=sw.minutes)
                    probs[sw.outcome] = sw.p0 + (sw.p1 - sw.p0) * f
                    other = pb if sw.outcome == pa else pa
                    probs[other] = 1 - probs[sw.outcome]
            snapshots += [
                OddsSnapshot(key if isinstance(key, RaceKey) else race_result.key, self.market, o, round(v, 4), now)
                for o, v in probs.items()
            ]
        return snapshots


def default_swings(src: ReplaySource, after_min: float = 60, minutes: float = 20) -> list[ScriptedSwing]:
    """One 60-point swing per race, an hour into its night, toward the eventual winner's party."""
    swings = []
    for race_key, snaps in src.timelines.items():
        final = snaps[-1].result
        if not final.candidates or not final.candidates[0].party:
            continue
        lead = max(final.candidates, key=lambda candidate: candidate.votes)
        swings.append(
            ScriptedSwing(race_key, lead.party, snaps[0].ts + timedelta(minutes=after_min), minutes, 0.2, 0.8)
        )
    return swings
