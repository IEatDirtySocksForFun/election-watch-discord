"""Per-user limits on the data commands (/bettingmarkets, /polls, /earlyvote) and per-server /refresh.

The data commands only read the database, so these limits protect Discord and the database,
not the sources. They use the same rule as the source budgets (ratelimit.py): stay at half
the useful rate. A refresh of the data is the most that can change between two looks, so by
default a user can look at the same thing at most once every two refreshes:

    cooldown for (user, command, target) = 2 x refresh interval of that data
    /refresh cooldown for a server       = 2 x shortest refresh interval of what it refreshes

Operators override the defaults (EW_USER_COOLDOWN seconds, EW_USER_HOURLY commands per user
per hour across the data commands, EW_REFRESH_COOLDOWN seconds); server admins override
them for their server with /limits.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping

FACTOR = 2.0  # half the useful rate


def _number(environ: Mapping[str, str], name: str) -> float | None:
    try:
        value = float(environ.get(name, ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class Cooldowns:
    def __init__(
        self,
        *,
        user_cooldown: float | None = None,
        user_hourly: float | None = None,
        refresh_cooldown: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_cooldown = user_cooldown  # None = FACTOR x the data's refresh interval
        self.user_hourly = user_hourly  # None or 0 = no overall cap
        self.refresh_cooldown = refresh_cooldown  # None = FACTOR x the shortest refresh interval
        self.clock = clock
        self._last: dict[tuple, float] = {}  # (user, command, target) -> last answered
        self._recent: dict[int, deque[float]] = {}  # user -> answer times in the last hour
        self._refreshed: dict[int, float] = {}  # guild -> last /refresh

    @classmethod
    def from_env(cls, environ: Mapping[str, str], clock: Callable[[], float] = time.monotonic) -> "Cooldowns":
        return cls(
            user_cooldown=_number(environ, "EW_USER_COOLDOWN"),
            user_hourly=_number(environ, "EW_USER_HOURLY"),
            refresh_cooldown=_number(environ, "EW_REFRESH_COOLDOWN"),
            clock=clock,
        )

    def user_seconds(self, refresh_interval: float, server: int | None = None) -> float:
        """The cooldown for one target: the server's value, else the operator's, else computed."""
        for value in (server, self.user_cooldown):
            if value is not None:
                return float(value)
        return FACTOR * refresh_interval

    def refresh_seconds(self, refresh_interval: float, server: int | None = None) -> float:
        for value in (server, self.refresh_cooldown):
            if value is not None:
                return float(value)
        return FACTOR * refresh_interval

    def hit(self, user: int, command: str, target: str, seconds: float, hourly: float | None = None) -> float:
        """Seconds the user must wait (0 = allowed, and the use is recorded)."""
        now = self.clock()
        ident = (user, command, target.casefold())
        last = self._last.get(ident)
        if last is not None and now - last < seconds:
            return seconds - (now - last)
        cap = self.user_hourly if hourly is None else hourly
        recent = self._recent.setdefault(user, deque())
        while recent and recent[0] <= now - 3600:
            recent.popleft()
        if cap and len(recent) >= cap:
            return recent[0] + 3600 - now
        self._last[ident] = now
        recent.append(now)
        if len(self._last) > 50_000:  # forget expired entries now and then
            self._last = {identity: last_use for identity, last_use in self._last.items() if now - last_use < 6 * 3600}
        return 0.0

    def refresh_hit(self, guild: int, seconds: float) -> float:
        now = self.clock()
        last = self._refreshed.get(guild)
        if last is not None and now - last < seconds:
            return seconds - (now - last)
        self._refreshed[guild] = now
        return 0.0
