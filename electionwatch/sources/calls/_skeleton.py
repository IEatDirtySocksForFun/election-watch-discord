"""Skeleton helpers for unverified race-call adapters."""

from __future__ import annotations

import httpx

from electionwatch.models import RaceCall, RaceKey
from electionwatch.racekeys import RaceKeyError, parse
from electionwatch.sources.base import CallSource

from ._common import normalize_party, parse_iso_ts


def parse_generic_calls(payload: dict, outlet: str) -> list[RaceCall]:
    """Parse a simple common call schema used by unverified outlet fixtures.

    Expected payload::

        {
          "races": [
            {"key": "TX SEN", "winner": "Ted Cruz", "party": "REP",
             "called_at": "2024-11-06T05:30:00Z", "url": "..."}
          ]
        }
    """
    calls: list[RaceCall] = []
    for race in payload.get("races") or []:
        key_text = race.get("key")
        if not key_text:
            continue
        try:
            key = parse(key_text)
        except RaceKeyError:
            continue
        winner = race.get("winner")
        if not winner:
            continue
        calls.append(
            RaceCall(
                key=key,
                outlet=outlet,
                winner=winner,
                party=normalize_party(race.get("party")),
                called_at=parse_iso_ts(race.get("called_at")),
                url=race.get("url"),
            )
        )
    return calls


def make_skeleton_source(
    source_name: str,
    source_outlet: str,
    env: tuple[str, ...] = (),
    scrape: bool = True,
    enabled_by_default: bool = False,
):
    """Factory for a minimal, unverified CallSource subclass."""

    class _Skeleton(CallSource):
        async def fetch_calls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[RaceCall]:
            # Unverified skeleton: the parse function is exercised against fixtures.
            return []

    _Skeleton.__name__ = f"{source_outlet}Source"
    _Skeleton.name = source_name
    _Skeleton.outlet = source_outlet
    _Skeleton.env = env
    _Skeleton.scrape = scrape
    _Skeleton.enabled_by_default = enabled_by_default
    return _Skeleton
