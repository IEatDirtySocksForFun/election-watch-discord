"""NBC exit-poll adapter (URL + optional JSON sidecar, unverified)."""
from __future__ import annotations

from electionwatch.sources.base import register

from ._common import condense_exit_poll, make_exit_poll_source


@register
class NBCExitPollSource(make_exit_poll_source("NBC", "nbc_exit_polls")):
    pass


__all__ = ["NBCExitPollSource", "condense_exit_poll"]
