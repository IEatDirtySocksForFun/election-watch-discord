"""ReplaySource: a ResultsSource that serves the snapshot current at the fake clock time."""

from __future__ import annotations

import bisect
import copy
from datetime import datetime
from typing import Callable

from electionwatch.models import RaceKey, RaceResult
from electionwatch.racekeys import STATES, describe
from electionwatch.sources.base import ResultsSource, register
from replay.timeline import Snapshot


@register
class ReplaySource(ResultsSource):
    name = "replay"
    states = frozenset(STATES)
    enabled_by_default = False  # only the sandbox constructs it (it needs timelines + clock)

    def __init__(self, timelines: dict[str, list[Snapshot]], clock: Callable[[], datetime]) -> None:
        self.timelines = {
            race_key: sorted(snapshots, key=lambda snapshot: snapshot.ts) for race_key, snapshots in timelines.items()
        }
        self._ts = {race_key: [snapshot.ts for snapshot in snapshots] for race_key, snapshots in self.timelines.items()}
        self.clock = clock

    def current(self, key: RaceKey | str) -> Snapshot | None:
        race_key = str(key)
        if race_key not in self.timelines:
            return None
        i = bisect.bisect_right(self._ts[race_key], self.clock())
        return self.timelines[race_key][i - 1] if i else None

    async def fetch(self, keys, client=None) -> list[RaceResult]:
        results = []
        for key in keys:
            snap = self.current(key)
            if snap is None:
                continue
            result = copy.deepcopy(snap.result)
            result.fetched_at = self.clock()
            results.append(result)
        return results

    async def list_races(self, state, client=None):
        return [
            (series[0].result.key, describe(series[0].result.key))
            for race_key, series in self.timelines.items()
            if series[0].result.key.state == state
        ]

    def next_change(self, after: datetime) -> datetime | None:
        """Earliest snapshot time strictly after `after` across all races, or None."""
        nxt = [ts[i] for ts in self._ts.values() if (i := bisect.bisect_right(ts, after)) < len(ts)]
        return min(nxt, default=None)

    def span(self) -> tuple[datetime, datetime]:
        firsts = [snapshots[0].ts for snapshots in self.timelines.values()]
        lasts = [snapshots[-1].ts for snapshots in self.timelines.values()]
        return min(firsts), max(lasts)
