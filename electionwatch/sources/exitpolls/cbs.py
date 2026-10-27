"""CBS exit-poll adapter (URL + optional JSON sidecar, unverified)."""
from __future__ import annotations

from electionwatch.sources.base import register

from ._common import condense_exit_poll, make_exit_poll_source


@register
class CBSExitPollSource(make_exit_poll_source("CBS", "cbs_exit_polls")):
    pass


__all__ = ["CBSExitPollSource", "condense_exit_poll"]
