"""ABC race-call adapter (unverified scraper skeleton).

ABC results pages are believed to be backed by a JSON feed, but the endpoint
and schema are not verified for 2026. Disabled by default.
"""
from __future__ import annotations

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class ABCSource(make_skeleton_source("abc", "ABC")):
    pass


def parse_abc_calls(payload: dict) -> list:
    """Parse an ABC-style race-call fixture."""
    return parse_generic_calls(payload, "ABC")
