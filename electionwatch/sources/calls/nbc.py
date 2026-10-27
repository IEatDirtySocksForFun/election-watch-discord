"""NBC race-call adapter (unverified scraper skeleton).

NBC results pages are believed to be backed by a JSON feed, but the endpoint
and schema are not verified for 2026. Disabled by default.
"""
from __future__ import annotations

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class NBCSource(make_skeleton_source("nbc", "NBC")):
    pass


def parse_nbc_calls(payload: dict) -> list:
    """Parse an NBC-style race-call fixture."""
    return parse_generic_calls(payload, "NBC")
