"""Fox exit-poll adapter (URL + optional JSON sidecar, unverified)."""

from __future__ import annotations

from electionwatch.sources.base import register

from ._common import condense_exit_poll, make_exit_poll_source


@register
class FOXExitPollSource(make_exit_poll_source("fox_exit_polls", "FOX")):
    pass


__all__ = ["FOXExitPollSource", "condense_exit_poll"]
