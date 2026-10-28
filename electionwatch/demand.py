"""Races (and early-vote pages) people asked about that no server watches.

Commands answer only from the database; when the database has nothing yet for what a user
asked, the command records it here and the next background refresh fetches it. Entries
expire after a day without a request, and the list is capped, so a user cycling through
every race cannot grow the refresh without bound (EW_DEMAND_MAX, EW_DEMAND_HOURS).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping


class Demand:
    def __init__(
        self, *, limit: int = 200, ttl: float = 24 * 3600, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.ttl = ttl
        self.clock = clock
        self._asked: dict[str, dict[str, float]] = {}  # kind -> {ident: last asked}

    @classmethod
    def from_env(cls, environ: Mapping[str, str], clock: Callable[[], float] = time.monotonic) -> "Demand":
        def number(name: str, default: float) -> float:
            try:
                return float(environ.get(name, "") or default)
            except ValueError:
                return default

        return cls(limit=int(number("EW_DEMAND_MAX", 200)), ttl=number("EW_DEMAND_HOURS", 24) * 3600, clock=clock)

    def add(self, kind: str, ident: str) -> bool:
        """Record a request; False when the list is full (the oldest entries are kept)."""
        asked = self._live(kind)
        if ident not in asked and len(asked) >= self.limit:
            return False
        asked.pop(ident, None)  # keep the list in the order requests were last made
        asked[ident] = self.clock()
        return True

    def items(self, kind: str) -> list[str]:
        """Live requests, oldest first (by when each was last made)."""
        return list(self._live(kind))

    def _live(self, kind: str) -> dict[str, float]:
        asked = self._asked.setdefault(kind, {})
        cutoff = self.clock() - self.ttl
        for ident in [i for i, at in asked.items() if at < cutoff]:
            del asked[ident]
        return asked
