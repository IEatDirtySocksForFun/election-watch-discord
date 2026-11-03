"""Polymarket prediction-market adapter for election odds.

Discovers active U.S. election events via the Gamma API and reads each market's
CLOB-derived yes price as the implied probability. No API key is required.

The midterms tag is about 1,250 events and 60 MB, so it is read once an hour. Every poll in
between prices the requested races' markets with batched CLOB requests (midpoints, spreads and
last trades, 500 tokens each, a few kB per race). The price follows Polymarket's own display
rule: the midpoint, or the last trade when the spread is wider than 10 cents.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, ClassVar

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
    CLOB = "https://clob.polymarket.com"
    # Gamma rejects offsets past ~2500 with 422 ("use /events/keyset"); the midterms tag held
    # about 1,250 open events on 2026-11-03.
    _MAX_DISCOVERY_OFFSET: ClassVar[int] = 2400
    TAG = "midterms"
    #: How long one read of the whole tag (which event is which race) is trusted.
    DISCOVERY_TTL: ClassVar[float] = 3600.0
    #: Tokens per batched CLOB request (the endpoint refuses ~1,000).
    BATCH: ClassVar[int] = 500
    #: Widest spread still priced at its midpoint; Polymarket shows the last trade beyond it.
    MAX_SPREAD: ClassVar[float] = 0.10

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._events: dict[RaceKey, dict] = {}  # key -> Gamma event, from the last discovery
        self._discovered: float | None = None

    async def fetch_odds(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[OddsSnapshot]:
        requested = set(keys)
        if not requested:
            return []

        now = self._clock()
        fresh = self._discovered is None or now - self._discovered >= self.DISCOVERY_TTL
        if fresh:
            self._events = await self._discover(client)
            self._discovered = now
        events = {key: self._events[key] for key in requested if key in self._events}
        if not fresh and events:
            events = await self._reprice(client, events)

        snapshots: list[OddsSnapshot] = []
        fetched = datetime.now(timezone.utc)  # updatedAt is metadata time, not price time
        for key, event in events.items():
            try:
                snaps = parse_polymarket_event(event)
            except Exception:
                continue
            snapshots.extend(replace(snap, ts=fetched) for snap in snaps if snap.key == key)
        return snapshots

    async def _discover(self, client: httpx.AsyncClient) -> dict[RaceKey, dict]:
        """Every race event under the tag. A top-two race between two candidates of one party
        has a party event ("Democratic Party" at 99%) and a "(by individual)" one; the latter
        is the race."""
        found: dict[RaceKey, dict] = {}
        individual: set[RaceKey] = set()
        offset = 0
        limit = 100
        while offset <= self._MAX_DISCOVERY_OFFSET:
            try:
                resp = await client.get(
                    f"{self.BASE}/events",
                    params={"closed": "false", "limit": limit, "offset": offset, "tag_slug": self.TAG},
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"polymarket unreachable: {exc}") from exc
            if resp.status_code == 422 and offset:
                break  # past Gamma's offset limit: keep what earlier pages found
            if resp.status_code >= 400:
                raise SourceUnavailable(f"polymarket returned {resp.status_code}")
            try:
                events = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"polymarket bad JSON: {exc}") from exc
            if not isinstance(events, list) or not events:
                break
            for event in events:
                title = event.get("title", "") if isinstance(event, dict) else ""
                key = _event_title_to_key(title)
                if key is None:
                    continue
                by_individual = bool(_INDIVIDUAL.search(title))
                if key not in found or (by_individual and key not in individual):
                    found[key] = event
                    if by_individual:
                        individual.add(key)
            if len(events) < limit:
                break
            offset += limit
        return found

    async def _reprice(self, client: httpx.AsyncClient, events: dict[RaceKey, dict]) -> dict[RaceKey, dict]:
        """The same events with each market's yes price replaced by the current CLOB price."""
        tokens = [
            token for event in events.values() for market in event.get("markets") or [] if (token := _yes_token(market))
        ]
        mids = await self._clob(client, "midpoints", tokens)
        spreads = await self._clob(client, "spreads", tokens)
        wide = [token for token in tokens if _float(spreads.get(token), 0.0) > self.MAX_SPREAD]
        last = await self._clob(client, "last-trades-prices", wide) if wide else {}
        repriced: dict[RaceKey, dict] = {}
        for key, event in events.items():
            markets = []
            for market in event.get("markets") or []:
                token = _yes_token(market)
                price = _float((last if token in wide else mids).get(token)) if token else None
                if price is None:
                    continue  # no current price: leave the market out rather than repeat an old one
                markets.append(
                    {
                        **market,
                        "outcomes": '["Yes", "No"]',
                        "outcomePrices": json.dumps([str(price), str(round(1 - price, 4))]),
                    }
                )
            repriced[key] = {**event, "markets": markets}
        return repriced

    async def _clob(self, client: httpx.AsyncClient, endpoint: str, tokens: list[str]) -> dict[str, str]:
        """POST /midpoints, /spreads or /last-trades-prices for many tokens: {token: price}."""
        answers: dict[str, str] = {}
        for start in range(0, len(tokens), self.BATCH):
            body = [{"token_id": token} for token in tokens[start : start + self.BATCH]]
            try:
                resp = await client.post(f"{self.CLOB}/{endpoint}", json=body)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"polymarket CLOB unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceUnavailable(f"polymarket CLOB /{endpoint} returned {resp.status_code}")
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"polymarket CLOB bad JSON: {exc}") from exc
            if isinstance(payload, dict):  # midpoints, spreads: {token: "0.285"}
                answers.update({str(token): value for token, value in payload.items()})
            elif isinstance(payload, list):  # last trades: [{"token_id": ..., "price": ...}]
                answers.update({str(row.get("token_id")): row.get("price") for row in payload if isinstance(row, dict)})
        return answers


#: "CA-11 House Election Winner (Individual)", "CA-04 ... (by individual)".
_INDIVIDUAL = re.compile(r"\((?:by )?individual\)\s*$", re.I)


def _yes_token(market: dict) -> str | None:
    """The CLOB token of an open market's "Yes" side."""
    if market.get("closed") or market.get("active") is False:
        return None
    try:
        tokens = json.loads(market.get("clobTokenIds") or "[]")
        outcomes = market.get("outcomes")
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    except (TypeError, ValueError):
        return None
    if not isinstance(tokens, list) or not tokens:
        return None
    index = 0
    if isinstance(outcomes, list) and len(outcomes) == len(tokens):
        index = next((i for i, name in enumerate(outcomes) if str(name).strip().lower() == "yes"), 0)
    return str(tokens[index])


def _float(value, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and 0.0 <= number <= 1.0 else default


def parse_polymarket_event(event: dict) -> list[OddsSnapshot]:
    """Parse a single Polymarket event object into OddsSnapshot rows."""
    key = _event_title_to_key(event.get("title", ""))
    if key is None:
        return []

    url = f"https://polymarket.com/event/{event.get('slug', '')}"
    snapshots: list[OddsSnapshot] = []
    for market in event.get("markets") or []:
        if market.get("closed") or market.get("active") is False:
            continue  # resolved, or an unused placeholder slot ("Candidate A", "Other") at 50%
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
                    candidate=_market_candidate(market),
                )
            )
        except Exception:
            continue
    # Two candidates of one party (top-two primaries, D-vs-D seats) must not share the party
    # label: the swing detector would read the two prices as one series.
    labels = [snap.outcome for snap in snapshots]
    titles = {
        str(market_row.get("id")): (market_row.get("groupItemTitle") or "").strip()
        for market_row in event.get("markets") or []
    }
    return [
        replace(snap, outcome=titles.get(snap.market_id) or snap.outcome) if labels.count(snap.outcome) > 1 else snap
        for snap in snapshots
    ]


def _event_title_to_key(title: str) -> RaceKey | None:
    low = title.lower().strip()
    # House events are titled by seat: "PA-01 House Election Winner", "AK-AL ...", and for some
    # top-two races also "CA-11 House Election Winner (Individual)".
    match = re.fullmatch(r"([a-z]{2})-(\d{1,2}|al) house election winner(?: \((?:by )?individual\))?", low)
    if match and match.group(1).upper() in STATES:
        seat = match.group(2)
        district = "AL" if seat == "al" else f"{int(seat):02d}"
        return RaceKey(match.group(1).upper(), Level.HOUSE, "HOUSE", district)
    for name, code in _STATE_BY_NAME.items():
        if not low.startswith(name + " "):
            continue
        tail = low[len(name) + 1 :].strip()
        if re.fullmatch(r"senate election winner", tail):
            return RaceKey(code, Level.SENATE, "SEN")
        if re.fullmatch(r"governor election winner", tail):
            return RaceKey(code, Level.GOVERNOR, "GOV")
        match = re.fullmatch(r"house election winner district (\d{1,2})", tail)
        if match:
            return RaceKey(code, Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}")
        match = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? congressional district election winner", tail)
        if match:
            return RaceKey(code, Level.HOUSE, "HOUSE", f"{int(match.group(1)):02d}")
    return None


def _market_candidate(market: dict) -> str | None:
    """'James Talarico (D)' -> 'James Talarico'; None for a party market ('Democratic')."""
    name = re.sub(r"\s*\([A-Za-z]\)\s*$", "", market.get("groupItemTitle", "") or "").strip()
    if not name or name.casefold() in _PARTY_NAMES or name.casefold() in ("yes", "no"):
        return None
    return name[:100]


_PARTY_NAMES = {
    "democrat": "DEM",
    "democratic": "DEM",
    "democrats": "DEM",
    "democratic party": "DEM",
    "republican": "REP",
    "republicans": "REP",
    "republican party": "REP",
    "independent": "IND",
}


def _market_outcome(market: dict) -> str | None:
    title = market.get("groupItemTitle", "") or ""
    if title.strip().casefold() in _PARTY_NAMES:  # House party markets: "Republican Party"
        return _PARTY_NAMES[title.strip().casefold()]
    # Extract a party letter in parentheses, e.g. "James Talarico (D)".
    match = re.search(r"\(([a-zA-Z])\)\s*$", title)
    if match:
        party = match.group(1).upper()
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
    match = re.search(r"the (democrats|republicans)", question, re.I)
    if match:
        return "DEM" if match.group(1).lower() == "democrats" else "REP"
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
