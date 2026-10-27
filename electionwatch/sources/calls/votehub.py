"""VoteHub race-call adapter (unverified scraper skeleton).

VoteHub does not publish a documented public API for race calls, so this adapter
is disabled by default and consumes a simple JSON fixture schema.
"""
from __future__ import annotations

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class VoteHubSource(make_skeleton_source("votehub", "VOTEHUB")):
    pass


def parse_votehub_calls(payload: dict) -> list:
    """Parse a VoteHub-style race-call fixture."""
    return parse_generic_calls(payload, "VOTEHUB")
