"""NYT race-call adapter (unverified scraper skeleton).

No usable NYT results feed was found: the interactive results pages return 403
to non-browser clients and guessed ``static01.nyt.com/elections-assets`` paths
return 404. Fixture-only skeleton, disabled by default.
"""

from __future__ import annotations

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class NYTSource(make_skeleton_source("nyt", "NYT")):
    pass


def parse_nyt_calls(payload: dict) -> list:
    """Parse an NYT-style race-call fixture."""
    return parse_generic_calls(payload, "NYT")
