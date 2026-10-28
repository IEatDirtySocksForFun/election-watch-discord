"""CNN exit-poll adapter (URL + optional JSON sidecar, unverified)."""

from __future__ import annotations

from electionwatch.sources.base import register

from ._common import condense_exit_poll, make_exit_poll_source


@register
class CNNExitPollSource(make_exit_poll_source("cnn_exit_polls", "CNN")):
    pass


__all__ = ["CNNExitPollSource", "condense_exit_poll"]
