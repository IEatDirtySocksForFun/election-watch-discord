"""Adapter contracts. One module per adapter; register it with @register.

Rules for every adapter:
- Pure parsing is separated from fetching: `parse_*(payload) -> list[...]` is a plain function
  tested against a trimmed real response saved under tests/fixtures/<adapter name>/.
- Fetch with the shared httpx.AsyncClient passed in (the poller owns timeouts/retries/UA).
- Never raise on one bad race: log and skip it. Raise SourceUnavailable for a dead endpoint.
- Paid / keyed APIs read their key from the env var named in `env`; if unset the adapter
  reports itself unavailable instead of failing.
"""
from __future__ import annotations

import os
from typing import ClassVar

import httpx

from electionwatch.models import ExitPoll, OddsSnapshot, RaceCall, RaceKey, RaceResult


class SourceUnavailable(RuntimeError):
    pass


class Source:
    name: ClassVar[str]                      # registry key, e.g. "tx_sos", "kalshi"
    kind: ClassVar[str]                      # "results" | "calls" | "odds" | "exitpolls"
    env: ClassVar[tuple[str, ...]] = ()      # required env vars (API keys)
    scrape: ClassVar[bool] = False           # True = HTML scrape of a site without an API
    enabled_by_default: ClassVar[bool] = True

    @classmethod
    def available(cls) -> bool:
        return all(os.environ.get(v) for v in cls.env)


class ResultsSource(Source):
    kind = "results"
    states: ClassVar[frozenset[str]] = frozenset()   # USPS codes this adapter covers

    async def fetch(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceResult]:
        """Current results for the requested keys this adapter covers (skip the rest)."""
        raise NotImplementedError

    async def list_races(self, state: str, client: httpx.AsyncClient) -> list[tuple[RaceKey, str]]:
        """All races the source publishes for a state, as (key, title). Feeds the navigator."""
        return []


class CallSource(Source):
    kind = "calls"
    outlet: ClassVar[str]                    # "AP", "CBS", ...

    async def fetch_calls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceCall]:
        raise NotImplementedError


class OddsSource(Source):
    kind = "odds"

    async def fetch_odds(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[OddsSnapshot]:
        raise NotImplementedError


class ExitPollSource(Source):
    kind = "exitpolls"

    async def fetch_exit_polls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[ExitPoll]:
        raise NotImplementedError


REGISTRY: dict[str, dict[str, type[Source]]] = {
    "results": {}, "calls": {}, "odds": {}, "exitpolls": {},
}


def register(cls: type[Source]) -> type[Source]:
    REGISTRY[cls.kind][cls.name] = cls
    return cls


def load_all() -> dict[str, dict[str, type[Source]]]:
    """Import every adapter module so its @register runs."""
    import importlib
    import pkgutil

    import electionwatch.sources as pkg
    for sub in ("results", "calls", "odds", "exitpolls"):
        mod = importlib.import_module(f"{pkg.__name__}.{sub}")
        for info in pkgutil.iter_modules(mod.__path__):
            importlib.import_module(f"{mod.__name__}.{info.name}")
    return REGISTRY
