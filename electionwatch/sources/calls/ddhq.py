"""Decision Desk HQ race-call adapter (unverified skeleton).

DDHQ requires an API key (DDHQ_API_KEY). The real endpoint, authentication flow
and response envelope are client-specific; this module provides a parse function
against a representative fixture.
"""

from __future__ import annotations

from typing import ClassVar

from electionwatch.sources.base import register

from ._skeleton import make_skeleton_source, parse_generic_calls


@register
class DDHQSource(make_skeleton_source("ddhq", "DDHQ", env=("DDHQ_API_KEY",), scrape=False)):
    # Kept enabled by default so it appears in outlet lists once a key is supplied.
    enabled_by_default: ClassVar[bool] = True


def parse_ddhq_calls(payload: dict) -> list:
    """Parse a DDHQ-style race-call fixture."""
    return parse_generic_calls(payload, "DDHQ")
