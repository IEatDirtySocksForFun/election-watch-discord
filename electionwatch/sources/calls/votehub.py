"""VoteHub race-call adapter (unverified scraper skeleton).

VoteHub exposes a public polling API (``api.votehub.com/polls``) but no
results/race-call endpoint: ``/results`` returns 404 and the site is behind a
Cloudflare challenge. This adapter therefore remains a fixture-only skeleton,
disabled by default.
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
