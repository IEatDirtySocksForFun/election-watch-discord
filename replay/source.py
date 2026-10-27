"""ReplaySource: a ResultsSource that serves the snapshot current at the fake clock time."""
from __future__ import annotations

import bisect
import copy
from typing import Callable

from datetime import datetime

from electionwatch.models import RaceKey, RaceResult
from electionwatch.racekeys import STATES, describe
from electionwatch.sources.base import ResultsSource, register

from replay.timeline import Snapshot


@register
class ReplaySource(ResultsSource):
    name = "replay"
    states = frozenset(STATES)
    enabled_by_default = False     # only the sandbox constructs it (it needs timelines + clock)

    def __init__(self, timelines: dict[str, list[Snapshot]], clock: Callable[[], datetime]) -> None:
        self.timelines = {k: sorted(v, key=lambda s: s.ts) for k, v in timelines.items()}
        self._ts = {k: [s.ts for s in v] for k, v in self.timelines.items()}
        self.clock = clock

    def current(self, key: RaceKey | str) -> Snapshot | None:
        k = str(key)
        if k not in self.timelines:
            return None
        i = bisect.bisect_right(self._ts[k], self.clock())
        return self.timelines[k][i - 1] if i else None

    async def fetch(self, keys, client=None) -> list[RaceResult]:
        out = []
        for key in keys:
            snap = self.current(key)
            if snap is None:
                continue
            r = copy.deepcopy(snap.result)
            r.fetched_at = self.clock()
            out.append(r)
        return out

    async def list_races(self, state, client=None):
        return [(s[0].result.key, describe(s[0].result.key)) for k, s in self.timelines.items()
                if s[0].result.key.state == state]

    def next_change(self, after: datetime) -> datetime | None:
        """Earliest snapshot time strictly after `after` across all races, or None."""
        nxt = [ts[i] for ts in self._ts.values() if (i := bisect.bisect_right(ts, after)) < len(ts)]
        return min(nxt, default=None)

    def span(self) -> tuple[datetime, datetime]:
        firsts = [v[0].ts for v in self.timelines.values()]
        lasts = [v[-1].ts for v in self.timelines.values()]
        return min(firsts), max(lasts)
