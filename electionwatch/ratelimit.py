"""Request budgets: how often each source may be asked, from its host's published rate limit.

Every request on the bot's shared HTTP client is counted per host (an httpx event hook) and
attributed to the source that made it (a context variable set around each fetch). A source's
refresh interval is then

    allowance per hour = host limit per hour / EW_RATE_SAFETY      (default 2: half the limit)
    interval           = max(floor for its kind, requests per refresh x 3600 / allowance)

so a bigger watch list automatically means a slower refresh rather than more requests. Before
each refresh the hosts the source used last time must still have room in the last hour's
allowance, so sources that share a host (civicAPI results, calls and early vote) cannot add
up past it either. Every number can be overridden by the operator (docs/customizing.md):

    EW_RATE_SAFETY=2                                      use at most 1/2 of each limit
    EW_RATE_LIMITS=civicapi.org=600/60,api.votehub.com=1000/3600   requests/seconds per host
    EW_INTERVALS=odds=120,polls=1800,polymarket=300       seconds, by kind or source name

An interval set in EW_INTERVALS replaces the computed one; the hourly allowance still applies.
"""
from __future__ import annotations

import contextvars
import math
import time
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

WINDOW = 3600.0   # the allowance is per hour


@dataclass(frozen=True)
class Limit:
    requests: int
    seconds: float
    note: str = ""

    @property
    def per_hour(self) -> float:
        return self.requests * WINDOW / self.seconds

    def __str__(self) -> str:
        return f"{self.requests}/{self.seconds:g}s"


#: Published limits (researched 2026-10-28). Hosts not listed use DEFAULT_LIMIT; a host's
#: parent domain matches too (www.cnn.com -> cnn.com).
HOST_LIMITS: dict[str, Limit] = {
    "gamma-api.polymarket.com": Limit(300, 10, "Polymarket docs: /markets 300 per 10 s per IP"),
    "api.elections.kalshi.com": Limit(10, 1, "Kalshi publishes 20/s for its basic keyed tier only; "
                                             "keyless calls are unpublished, so half of that"),
    "en.wikipedia.org": Limit(200, 60, "Wikimedia: 200/min for a bot whose User-Agent has contact info"),
    "api.ap.org": Limit(10, 1, "elex docs: most AP keys allow 10/s; check your key's quota"),
    # Nothing published: a request a second for civicAPI and VoteHub (free, keyless), ten a
    # minute for the news outlets' undocumented feeds, which block this bot after bursts.
    "civicapi.org": Limit(1, 1, "none published"),
    "api.votehub.com": Limit(1, 1, "none published"),
    "www.270towin.com": Limit(10, 60, "none published (HTML pages)"),
    "cnn.io": Limit(10, 60, "undocumented feed; CNN blocks bursts"),
    "cnn.com": Limit(10, 60, "undocumented feed; CNN blocks bursts"),
    "nbcnews.com": Limit(10, 60, "undocumented feed; NBC blocks bursts"),
    "foxnews.com": Limit(10, 60, "undocumented feed"),
    "cbsnews.com": Limit(10, 60, "undocumented feed"),
    "abcnews.go.com": Limit(10, 60, "undocumented feed"),
}
DEFAULT_LIMIT = Limit(1, 1, "none published: a request a second")

#: The fastest each kind of source is refreshed, whatever its budget allows.
FLOORS: dict[str, float] = {
    "results": 60, "calls": 60, "exitpolls": 60, "odds": 60,
    "polls": 30 * 60,        # polls are published a few times a day
    "earlyvote": 60 * 60,    # civicAPI updates early-vote numbers about once a day
}
DEFAULT_FLOOR = 60.0

current_source: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_source", default=None)


def parse_limits(raw: str) -> dict[str, Limit]:
    """"civicapi.org=600/60,api.votehub.com=1000/3600" -> {host: Limit}. Bad parts are skipped."""
    out: dict[str, Limit] = {}
    for part in raw.split(","):
        host, sep, value = part.partition("=")
        count, slash, seconds = value.partition("/")
        try:
            limit = Limit(int(count), float(seconds) if slash else WINDOW, "EW_RATE_LIMITS")
        except ValueError:
            continue
        if sep and host.strip() and limit.requests > 0 and limit.seconds > 0:
            out[host.strip().lower()] = limit
    return out


def parse_seconds(raw: str) -> dict[str, float]:
    """"odds=120,polymarket=300" -> {name: seconds}. Bad or negative parts are skipped."""
    out: dict[str, float] = {}
    for part in raw.split(","):
        name, sep, value = part.partition("=")
        try:
            seconds = float(value)
        except ValueError:
            continue
        if sep and name.strip() and seconds >= 0:
            out[name.strip()] = seconds
    return out


class RateBook:
    """Counts requests per host and decides when each source is due again."""

    def __init__(
        self,
        limits: Mapping[str, Limit] | None = None,
        *,
        safety: float = 2.0,
        intervals: Mapping[str, float] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = {**HOST_LIMITS, **(limits or {})}
        self.safety = max(1.0, float(safety))
        self.intervals = dict(intervals or {})
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)   # host -> request times
        self._cost: dict[str, Counter] = {}       # source -> requests per host in its last refresh
        self._running: dict[str, Counter] = {}
        self._next: dict[str, float] = {}         # source -> when it is due again
        self._last: dict[str, float] = {}         # source -> when its last refresh ended
        self._kind: dict[str, str] = {}

    @classmethod
    def from_env(cls, environ: Mapping[str, str], clock: Callable[[], float] = time.monotonic) -> "RateBook":
        try:
            safety = float(environ.get("EW_RATE_SAFETY", "") or 2)
        except ValueError:
            safety = 2.0
        return cls(parse_limits(environ.get("EW_RATE_LIMITS", "")), safety=safety,
                   intervals=parse_seconds(environ.get("EW_INTERVALS", "")), clock=clock)

    # ------------------------------------------------------------------ counting
    def limit(self, host: str) -> Limit:
        host = host.lower()
        while host:
            if host in self.limits:
                return self.limits[host]
            _, dot, host = host.partition(".")
            if not dot:
                break
        return DEFAULT_LIMIT

    def allowance(self, host: str) -> float:
        """Requests per hour this host may get: its limit divided by the safety factor."""
        return self.limit(host).per_hour / self.safety

    def used(self, host: str) -> int:
        """Requests to host in the last hour."""
        hits = self._hits.get(host.lower())
        if not hits:
            return 0
        cutoff = self.clock() - WINDOW
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return len(hits)

    def record(self, host: str) -> None:
        host = host.lower()
        self._hits[host].append(self.clock())
        source = current_source.get()
        if source is not None and source in self._running:
            self._running[source][host] += 1

    async def on_request(self, request) -> None:
        """httpx event hook: ``AsyncClient(event_hooks={"request": [book.on_request]})``."""
        self.record(request.url.host)

    # ------------------------------------------------------------------ scheduling
    def interval(self, name: str, kind: str) -> float:
        """Seconds between refreshes of source `name`: the operator's value, else computed."""
        for override in (name, kind):
            if override in self.intervals:
                return self.intervals[override]
        need = max((n * WINDOW / self.allowance(host) for host, n in self._cost.get(name, {}).items()),
                   default=0.0)
        return max(FLOORS.get(kind, DEFAULT_FLOOR), need)

    def due(self, name: str, kind: str) -> bool:
        """Whether `name` may refresh now: its interval passed and its hosts have room."""
        self._kind.setdefault(name, kind)
        if self.clock() < self._next.get(name, -math.inf):
            return False
        for host, cost in self._cost.get(name, {}).items():
            used = self.used(host)
            # A host nobody used in the last hour is always allowed one refresh, or a source
            # whose single refresh is bigger than the allowance would never run.
            if used and used + cost > self.allowance(host):
                return False
        return True

    @contextmanager
    def track(self, name: str, kind: str):
        """Attribute the requests made inside the block to `name`, then schedule it."""
        self._kind[name] = kind
        self._running[name] = Counter()
        token = current_source.set(name)
        try:
            yield
        finally:
            current_source.reset(token)
            self._cost[name] = self._running.pop(name)
            now = self.clock()
            self._last[name] = now
            self._next[name] = now + self.interval(name, kind)

    def force(self, kinds: set[str] | None = None) -> list[str]:
        """Make sources of these kinds (all if None) due now; the hourly allowance still holds."""
        forced = [name for name, kind in self._kind.items() if kinds is None or kind in kinds]
        for name in forced:
            self._next[name] = -math.inf
        return forced

    def last_run(self, name: str) -> float | None:
        """When `name` last finished a refresh (clock seconds), None if never."""
        return self._last.get(name)

    def wait(self, name: str) -> float:
        """Seconds until `name` is due by its interval (0 if due)."""
        return max(0.0, self._next.get(name, -math.inf) - self.clock())

    def kind_interval(self, kind: str) -> float:
        """The shortest interval among the known sources of a kind (its floor if none yet)."""
        names = [name for name, k in self._kind.items() if k == kind]
        if not names:
            return self.intervals.get(kind, FLOORS.get(kind, DEFAULT_FLOOR))
        return min(self.interval(name, kind) for name in names)

    def status(self) -> list[dict]:
        """One row per known source, for /limits."""
        rows = []
        for name, kind in sorted(self._kind.items(), key=lambda item: (item[1], item[0])):
            cost = self._cost.get(name, Counter())
            rows.append({
                "source": name, "kind": kind,
                "interval": self.interval(name, kind),
                "overridden": name in self.intervals or kind in self.intervals,
                "next_in": self.wait(name),
                "requests_per_refresh": sum(cost.values()),
                "hosts": {host: (self.used(host), self.allowance(host)) for host in sorted(cost)},
            })
        return rows
