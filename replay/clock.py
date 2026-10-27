"""A settable clock. Pass the instance wherever a `clock: Callable[[], datetime]` is expected."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, when: datetime) -> None:
        self.now = when

    def advance(self, minutes: float = 1) -> datetime:
        self.now += timedelta(minutes=minutes)
        return self.now
