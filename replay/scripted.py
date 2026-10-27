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
    for k, snaps in src.timelines.items():
        final = snaps[-1].result
        if not final.candidates:
            continue
        lead = max(final.candidates, key=lambda c: c.votes)
        at = next((s.ts for s in snaps if (s.result.expected_vote_pct or 0) >= call_at_eevp),
                  snaps[-1].ts + timedelta(minutes=15))
        calls.append(ScriptedCall(k, at, lead.name, lead.party, outlet))
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
        want = {str(k): k for k in keys}
        now = self.clock()
        return [RaceCall(want[c.key], c.outlet, c.winner, c.party, c.at, retracted=c.retracted)
                for c in self.calls if c.key in want and c.at <= now]


def implied_prob(margin_pct: float, eevp: float) -> float:
    """Toy model: certainty grows with the leader's margin and the share of the vote counted."""
    return 1 / (1 + math.exp(-0.6 * margin_pct * math.sqrt(max(eevp, 1.0) / 100)))


@register
class ScriptedOddsSource(OddsSource):
    name = "replay_odds"
    enabled_by_default = False

    def __init__(self, results: ReplaySource, clock: Callable[[], datetime],
                 swings: list[ScriptedSwing] | None = None, market: str = "kalshi") -> None:
        self.results = results
        self.clock = clock
        self.swings = swings or []
        self.market = market

    async def fetch_odds(self, keys, client=None) -> list[OddsSnapshot]:
        now = self.clock()
        out = []
        for key in keys:
            snap = self.results.current(key)
            if snap is None:
                continue
            r = snap.result
            tot = sum(c.votes for c in r.candidates) or 1
            parties = {}
            for c in r.candidates:
                if c.party and c.party not in parties:
                    parties[c.party] = 100.0 * c.votes / tot
            if len(parties) < 2:
                continue
            (pa, sa), (pb, sb) = sorted(parties.items(), key=lambda kv: -kv[1])[:2]
            p = implied_prob(sa - sb, r.expected_vote_pct or 50.0)
            probs = {pa: p, pb: 1 - p}
            for sw in self.swings:
                if sw.key == str(key) and sw.start <= now <= sw.start + timedelta(minutes=sw.minutes):
                    f = (now - sw.start) / timedelta(minutes=sw.minutes)
                    probs[sw.outcome] = sw.p0 + (sw.p1 - sw.p0) * f
                    other = pb if sw.outcome == pa else pa
                    probs[other] = 1 - probs[sw.outcome]
            out += [OddsSnapshot(key if isinstance(key, RaceKey) else r.key, self.market, o, round(v, 4), now)
                    for o, v in probs.items()]
        return out


def default_swings(src: ReplaySource, after_min: float = 60, minutes: float = 20) -> list[ScriptedSwing]:
    """One 60-point swing per race, an hour into its night, toward the eventual winner's party."""
    out = []
    for k, snaps in src.timelines.items():
        final = snaps[-1].result
        if not final.candidates or not final.candidates[0].party:
            continue
        lead = max(final.candidates, key=lambda c: c.votes)
        out.append(ScriptedSwing(k, lead.party, snaps[0].ts + timedelta(minutes=after_min), minutes, 0.2, 0.8))
    return out
