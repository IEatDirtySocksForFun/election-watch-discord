"""Request budgets: how often each source may be asked, from its host's published rate limit.

Every request on the bot's shared HTTP client is counted per host (an httpx event hook) and
attributed to the source that made it (a context variable set around each fetch). Sources that
share a host share its allowance evenly:

    allowance per hour = host limit per hour / EW_RATE_SAFETY      (default 2: half the limit)
    demand             = sum over the host's sources of requests per refresh / their floor
    stretch            = demand / (90% of the allowance), at least 1
    interval           = floor for the source's kind x the largest stretch of its hosts

so a bigger watch list (or another heavy source on the same host) slows every source on that
host by the same factor instead of spending the hour's allowance early and then stalling. As
a safety net a refresh only starts while its hosts still have room in the last hour, counting
the requests that refreshes already running are expected to make. Every number can be
overridden by the operator (docs/customizing.md):

    EW_RATE_SAFETY=2                                      use at most 1/2 of each limit
    EW_RATE_LIMITS=civicapi.org=600/60,api.votehub.com=1000/3600   requests/seconds per host
    EW_INTERVALS=odds=120,polls=1800,polymarket=300,polymarket:odds=600
                                    seconds, by kind, source, or source:kind (most specific wins)

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
TARGET = 0.9      # plan for 90% of the allowance; the hourly check catches the rest


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
    # Clarity's ENR files are static JSON on a CDN; nothing published. Many county adapters
    # share it (TX, NJ, MI, AL, CO, IA, SC: ~160 requests a round), so it gets more than the
    # generic default.
    "results.enr.clarityelections.com": Limit(4, 1, "none published: static files on a CDN"),
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
        self.limits = {k.lower(): v for k, v in {**HOST_LIMITS, **(limits or {})}.items()}
        self.safety = max(1.0, float(safety))
        self.intervals = dict(intervals or {})
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)   # bucket -> request times
        self._cost: dict[str, Counter] = {}       # source -> requests per bucket in its last refresh
        self._running: dict[str, Counter] = {}
        self._held: dict[str, Counter] = {}       # source -> requests it is still expected to make
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
    def bucket(self, host: str) -> str:
        """The limit a host counts against: the listed host or parent domain, else itself.
        api.cnn.io and politics.api.cnn.io share the cnn.io bucket."""
        host = host.lower()
        probe = host
        while probe:
            if probe in self.limits:
                return probe
            _, dot, probe = probe.partition(".")
            if not dot:
                break
        return host

    def limit(self, host: str) -> Limit:
        return self.limits.get(self.bucket(host), DEFAULT_LIMIT)

    def allowance(self, host: str) -> float:
        """Requests per hour this host may get: its limit divided by the safety factor."""
        return self.limit(host).per_hour / self.safety

    def used(self, host: str) -> int:
        """Requests to host (its bucket) in the last hour."""
        hits = self._hits.get(self.bucket(host))
        if not hits:
            return 0
        cutoff = self.clock() - WINDOW
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return len(hits)

    def reserved(self, host: str) -> int:
        """Requests running refreshes are still expected to make to host."""
        bucket = self.bucket(host)
        return sum(held[bucket] for held in self._held.values())

    def record(self, host: str) -> None:
        bucket = self.bucket(host)
        self._hits[bucket].append(self.clock())
        source = current_source.get()
        if source is not None and source in self._running:
            self._running[source][bucket] += 1
            held = self._held.get(source)
            if held and held[bucket] > 0:
                held[bucket] -= 1

    async def on_request(self, request) -> None:
        """httpx event hook: ``AsyncClient(event_hooks={"request": [book.on_request]})``."""
        self.record(request.url.host)

    # ------------------------------------------------------------------ scheduling
    def _override(self, name: str, kind: str) -> float | None:
        source = name.split(":", 1)[0]
        for key in (name, source, kind):
            if key in self.intervals:
                return self.intervals[key]
        return None

    def _base(self, name: str, kind: str) -> float:
        override = self._override(name, kind)
        return override if override is not None else FLOORS.get(kind, DEFAULT_FLOOR)

    def stretch(self, bucket: str) -> float:
        """How much slower than their floors the sources on a host must run to share it."""
        demand = sum(cost[bucket] / max(self._base(name, self._kind.get(name, "")), 1.0)
                     for name, cost in self._cost.items() if cost.get(bucket))
        return max(1.0, demand * WINDOW / (TARGET * self.allowance(bucket)))

    def interval(self, name: str, kind: str) -> float:
        """Seconds between refreshes of source `name`: the operator's value, else computed."""
        override = self._override(name, kind)
        if override is not None:
            return override
        stretch = max((self.stretch(bucket) for bucket in self._cost.get(name, {})), default=1.0)
        return FLOORS.get(kind, DEFAULT_FLOOR) * stretch

    def due(self, name: str, kind: str) -> bool:
        """Whether `name` may refresh now: its interval passed and its hosts have room."""
        self._kind.setdefault(name, kind)
        if self.clock() < self._next.get(name, -math.inf):
            return False
        for bucket, cost in self._cost.get(name, {}).items():
            used = self.used(bucket) + self.reserved(bucket)
            # A host nobody used in the last hour is always allowed one refresh, or a source
            # whose single refresh is bigger than the allowance would never run.
            if used and used + cost > self.allowance(bucket):
                return False
        return True

    @contextmanager
    def track(self, name: str, kind: str):
        """Attribute the requests made inside the block to `name`, then schedule it. Entering
        reserves the requests its last refresh made, so sources started in the same tick see
        each other's (call due() and enter with no await in between)."""
        self._kind[name] = kind
        self._running[name] = Counter()
        self._held[name] = Counter(self._cost.get(name, {}))
        token = current_source.set(name)
        try:
            yield
        finally:
            current_source.reset(token)
            self._held.pop(name, None)
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
            override = self.intervals.get(kind)
            return override if override is not None else FLOORS.get(kind, DEFAULT_FLOOR)
        return min(self.interval(name, kind) for name in names)

    def status(self) -> list[dict]:
        """One row per known source, for /limits."""
        rows = []
        for name, kind in sorted(self._kind.items(), key=lambda item: (item[1], item[0])):
            cost = self._cost.get(name, Counter())
            rows.append({
                "source": name, "kind": kind,
                "interval": self.interval(name, kind),
                "floor": FLOORS.get(kind, DEFAULT_FLOOR),
                "overridden": self._override(name, kind) is not None,
                "next_in": self.wait(name),
                "requests_per_refresh": sum(cost.values()),
                "hosts": {bucket: (self.used(bucket), self.allowance(bucket)) for bucket in sorted(cost)},
            })
        return rows
