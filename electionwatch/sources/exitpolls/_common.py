"""Shared helpers for exit-poll adapters."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import ExitPoll, Level, RaceKey
from electionwatch.sources.base import ExitPollSource

URL_TEMPLATES = {
    # CNN and CBS give each state/race its own exit-poll page (verified 2024).
    "CNN": "https://www.cnn.com/election/{year}/exit-polls/{state}/{race}",
    "CBS": "https://www.cbsnews.com/elections/{year}/{state}/{race}/exit-poll/",
    # NBC and Fox only publish a national voter-analysis page; per-state URLs 404.
    "NBC": "https://www.nbcnews.com/politics/{year}-elections/exit-polls",
    "FOX": "https://www.foxnews.com/elections/{year}/general-results/voter-analysis",
}

_RACES = {
    Level.SENATE: "senate",
    Level.GOVERNOR: "governor",
    Level.HOUSE: "house",
}


def exit_poll_url(outlet: str, key: RaceKey, year: int = 2026) -> str | None:
    template = URL_TEMPLATES.get(outlet)
    race = _RACES.get(key.level)
    if template is None or race is None:
        return None
    return template.format(year=year, state=key.state.lower(), race=race)


def condense_exit_poll(payload: dict) -> str | None:
    """Build a 1-3 line summary from a JSON exit-poll payload.

    Supports payloads shaped like::

        {"demographics": {"Gender": {"Men": "55% Rep", "Women": "52% Dem"}, ...}}

    or::

        {"topics": [{"name": "Gender", "rows": [{"label": "Men", "Dem": 45, "Rep": 53}]}]}
    """
    demos = payload.get("demographics")
    if isinstance(demos, dict) and demos:
        lines = []
        for name, groups in list(demos.items())[:3]:
            if isinstance(groups, dict):
                parts = [f"{k} {v}" for k, v in list(groups.items())[:2]]
                lines.append(f"{name}: {', '.join(parts)}")
        return " | ".join(lines) if lines else None

    topics = payload.get("topics")
    if isinstance(topics, list) and topics:
        lines = []
        for topic in topics[:3]:
            name = topic.get("name", "Topic")
            rows = topic.get("rows") or []
            parts = []
            for row in rows[:2]:
                if isinstance(row, dict):
                    label = row.get("label") or row.get("group") or ""
                    vals = [f"{k}={v}" for k, v in row.items() if k not in ("label", "group")][:2]
                    parts.append(f"{label} {' '.join(vals)}".strip())
            if parts:
                lines.append(f"{name}: {', '.join(parts)}")
        return " | ".join(lines) if lines else None

    return None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _before_election_day() -> bool:
    """Exit polls only exist from election day on (ELECTION_DATE, default 2026-11-03)."""
    day = date.fromisoformat(os.environ.get("ELECTION_DATE", "2026-11-03"))
    return _utcnow().date() < day


def make_exit_poll_source(source_name: str, source_outlet: str):
    """Factory for an ExitPollSource that generates outlet page URLs."""

    class _Source(ExitPollSource):
        env: ClassVar[tuple[str, ...]] = ()
        scrape = True
        enabled_by_default = False
        YEAR = 2026

        async def fetch_exit_polls(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[ExitPoll]:
            polls: list[ExitPoll] = []
            if _before_election_day():
                return polls
            for key in keys:
                url = exit_poll_url(self.outlet, key, self.YEAR)
                if not url:
                    continue
                summary: str | None = None
                # Attempt to fetch a JSON sidecar. Most outlets do not expose one;
                # failure is silent and leaves summary=None.
                try:
                    json_url = url + ".json"
                    resp = await client.get(json_url, timeout=10)
                    if resp.status_code == 200:
                        summary = condense_exit_poll(resp.json())
                except Exception:
                    pass
                polls.append(
                    ExitPoll(
                        key=key,
                        outlet=self.outlet,
                        url=url,
                        summary=summary,
                        fetched_at=_utcnow(),
                    )
                )
            return polls

    _Source.__name__ = f"{source_outlet}ExitPollSource"
    _Source.name = source_name
    _Source.outlet = source_outlet
    return _Source
