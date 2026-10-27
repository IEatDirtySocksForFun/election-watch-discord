"""Fox race-call adapter (unverified scraper skeleton).

Fox results pages are believed to be backed by a JSON feed, but the endpoint
and schema are not verified for 2026. Disabled by default.
"""
from __future__ import annotations

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class FOXSource(make_skeleton_source("fox", "FOX")):
    pass


def parse_fox_calls(payload: dict) -> list:
    """Parse a Fox-style race-call fixture."""
    return parse_generic_calls(payload, "FOX")
