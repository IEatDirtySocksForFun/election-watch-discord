"""Swing detection for prediction-market odds."""

from __future__ import annotations

from datetime import timedelta

from electionwatch.models import OddsSnapshot


def detect_swing(
    history: list[OddsSnapshot],
    pts: float,
    window: timedelta,
) -> tuple[float, OddsSnapshot, OddsSnapshot] | None:
    """Return the largest percentage-point move between any snapshot inside ``window``
    and the latest snapshot.

    ``pts`` is expressed in whole percentage points (e.g. 5 means a 5-point move).
    ``prob`` on the snapshots is 0-1, so the returned swing is ``(latest - earlier)*100``.
    Returns ``None`` when no move is at least ``pts``.
    """
    if len(history) < 2:
        return None
    latest = max(history, key=lambda snapshot: snapshot.ts)
    cutoff = latest.ts - window
    best: tuple[float, OddsSnapshot, OddsSnapshot] | None = None
    for earlier in history:
        if earlier is latest:
            continue
        if earlier.ts > latest.ts or earlier.ts < cutoff:
            continue
        swing = round((latest.prob - earlier.prob) * 100.0, 2)
        if abs(swing) + 1e-9 < pts:
            continue
        if best is None or abs(swing) > abs(best[0]):
            best = (swing, earlier, latest)
        elif abs(swing) == abs(best[0]) and earlier.ts < best[1].ts:
            best = (swing, earlier, latest)
    return best
