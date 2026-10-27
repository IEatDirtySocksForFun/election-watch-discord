"""Polymarket prediction-market adapter for election odds.

Discovers active U.S. election events via the Gamma API and reads each market's
CLOB-derived yes price as the implied probability. No API key is required.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import Level, OddsSnapshot, RaceKey
from electionwatch.racekeys import STATES
from electionwatch.sources.base import OddsSource, SourceUnavailable, register

# Longest state names first so "New York" beats "York".
_STATE_BY_NAME = {name.lower(): code for code, name in sorted(STATES.items(), key=lambda kv: -len(kv[1]))}


@register
class PolymarketSource(OddsSource):
    name = "polymarket"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = False
    enabled_by_default = True

    BASE = "https://gamma-api.polymarket.com"
    # Gamma rejects offsets past ~2500 with 422 ("use /events/keyset"); the midterms tag keeps
    # the Senate/Governor markets on the first page, so a low cap is plenty.
    _MAX_DISCOVERY_OFFSET: ClassVar[int] = 1000
    TAG = "midterms"

    async def fetch_odds(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[OddsSnapshot]:
        requested = set(keys)
        if not requested:
            return []

        snapshots: list[OddsSnapshot] = []
        found_keys: set[RaceKey] = set()
        offset = 0
        limit = 100
        while offset <= self._MAX_DISCOVERY_OFFSET:
            try:
                resp = await client.get(
                    f"{self.BASE}/events",
                    params={"closed": "false", "limit": limit, "offset": offset,
                            "tag_slug": self.TAG},
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"polymarket unreachable: {exc}") from exc
            if resp.status_code == 422 and offset:
                break  # past Gamma's offset limit: keep what earlier pages found
            if resp.status_code >= 400:
                raise SourceUnavailable(
                    f"polymarket returned {resp.status_code}"
                )
            try:
                events = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"polymarket bad JSON: {exc}") from exc
            if not isinstance(events, list) or not events:
                break

            for event in events:
                try:
                    snaps = parse_polymarket_event(event)
                except Exception:
                    continue
                fetched = datetime.now(timezone.utc)   # updatedAt is metadata time, not price time
                for snap in snaps:
                    if snap.key in requested:
                        snapshots.append(replace(snap, ts=fetched))
                        found_keys.add(snap.key)

            if found_keys >= requested:
                break
            offset += limit

        return snapshots


def parse_polymarket_event(event: dict) -> list[OddsSnapshot]:
    """Parse a single Polymarket event object into OddsSnapshot rows."""
    key = _event_title_to_key(event.get("title", ""))
    if key is None:
        return []

    url = f"https://polymarket.com/event/{event.get('slug', '')}"
    snapshots: list[OddsSnapshot] = []
    for market in event.get("markets") or []:
        try:
            outcome = _market_outcome(market)
            if outcome is None:
                continue
            prob = _market_prob(market)
            if prob is None:
                continue
            ts = _parse_ts(market.get("updatedAt") or event.get("updatedAt"))
            snapshots.append(
                OddsSnapshot(
                    key=key,
                    market="polymarket",
                    outcome=outcome,
                    prob=prob,
                    ts=ts,
                    market_id=str(market.get("id")),
                    url=url,
                )
            )
        except Exception:
            continue
    return snapshots


def _event_title_to_key(title: str) -> RaceKey | None:
    low = title.lower().strip()
    for name, code in _STATE_BY_NAME.items():
        if not low.startswith(name + " "):
            continue
        tail = low[len(name) + 1 :].strip()
        if re.fullmatch(r"senate election winner", tail):
            return RaceKey(code, Level.SENATE, "SEN")
        if re.fullmatch(r"governor election winner", tail):
            return RaceKey(code, Level.GOVERNOR, "GOV")
        m = re.fullmatch(r"house election winner district (\d{1,2})", tail)
        if m:
            return RaceKey(code, Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
        m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? congressional district election winner", tail)
        if m:
            return RaceKey(code, Level.HOUSE, "HOUSE", f"{int(m.group(1)):02d}")
    return None


def _market_outcome(market: dict) -> str | None:
    title = market.get("groupItemTitle", "") or ""
    # Extract a party letter in parentheses, e.g. "James Talarico (D)".
    m = re.search(r"\(([a-zA-Z])\)\s*$", title)
    if m:
        party = m.group(1).upper()
        mapping = {
            "D": "DEM",
            "R": "REP",
            "I": "IND",
            "L": "LIB",
            "G": "GRN",
        }
        if party in mapping:
            return mapping[party]
    # No party: use the candidate/display name.
    name = title.strip()
    if name:
        return name
    # Fallback to the market question.
    question = market.get("question", "")
    m = re.search(r"the (democrats|republicans)", question, re.I)
    if m:
        return "DEM" if m.group(1).lower() == "democrats" else "REP"
    return None


def _market_prob(market: dict) -> float | None:
    """Probability that the market's outcome resolves yes.

    ``outcomes``/``outcomePrices`` are parallel JSON arrays; prices[0] is only
    the yes price when outcomes[0] is "Yes". Polymarket currently always emits
    ["Yes", "No"], but do not rely on that ordering silently.
    """
    raw = market.get("outcomePrices")
    if not raw:
        return None
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(prices, list) or not prices:
        return None

    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except Exception:
            outcomes = None
    index = 0
    if isinstance(outcomes, list) and len(outcomes) == len(prices):
        index = next(
            (i for i, name in enumerate(outcomes) if str(name).strip().lower() == "yes"),
            0,
        )
    try:
        value = float(prices[index])
    except (IndexError, TypeError, ValueError):
        return None
    if value != value or not (0.0 <= value <= 1.0):  # NaN / outside probability
        return None
    return value


def _parse_ts(raw: str | None) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)
