"""Kalshi public market-data adapter for election odds.

Discovers 2026 U.S. election events from series tickers and parses each market
as an OddsSnapshot. No API key is required for reads.

Each race's event (its market tickers) is looked up once an hour, and a race Kalshi has no
event for is not asked again for that hour. Every poll in between prices all known markets
with one batched ``/markets?tickers=`` request per 100 markets instead of one ``/events``
request per race. A market is priced at the midpoint of its yes bid and ask when the spread
is tight, so one stray overnight trade does not move the number.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, ClassVar

import httpx

from electionwatch.models import Level, OddsSnapshot, RaceKey
from electionwatch.sources.base import OddsSource, SourceUnavailable, register


@register
class KalshiSource(OddsSource):
    name = "kalshi"
    env: ClassVar[tuple[str, ...]] = ()
    scrape = False
    enabled_by_default = True

    BASE = "https://api.elections.kalshi.com/trade-api/v2"

    # Small override table for non-standard series names discovered via series listing.
    _GOV_OVERRIDES: ClassVar[dict[str, str]] = {
        "AK": "KXGOVAK",
    }

    #: How long an event's layout (or Kalshi's 404 for a race) is trusted before asking again.
    LAYOUT_TTL: ClassVar[float] = 3600.0
    #: Market tickers per batched /markets request.
    BATCH: ClassVar[int] = 100

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._layout: dict[RaceKey, tuple[float, dict]] = {}  # key -> (looked up, /events payload)
        self._missing: dict[RaceKey, float] = {}  # key -> when Kalshi answered 404
        self._series: list[RaceKey] = []  # every race Kalshi has a series for
        self._series_at: float | None = None

    #: Pause between two /events lookups, so the hourly layout pass stays under Kalshi's rate.
    LOOKUP_SPACING: ClassVar[float] = 0.15

    async def market_keys(self, client: httpx.AsyncClient) -> list[RaceKey]:
        """Every race Kalshi lists a series for (SENATE<ST>, HOUSE<ST><N>, GOVPARTY<ST>, ...),
        from one /series request an hour. Races without a 2026 event answer 404 once an hour."""
        now = self._clock()
        if self._series_at is None or now - self._series_at >= self.LAYOUT_TTL:
            try:
                resp = await client.get(f"{self.BASE}/series", params={"category": "Elections"})
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"kalshi unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceUnavailable(f"kalshi returned {resp.status_code} for the series list")
            try:
                rows = resp.json().get("series") or []
            except Exception as exc:
                raise SourceUnavailable(f"kalshi bad JSON: {exc}") from exc
            keys = []
            for row in rows:
                ticker = str(row.get("ticker") or "") if isinstance(row, dict) else ""
                if _RACE_SERIES.fullmatch(ticker) and (key := _event_ticker_to_key(f"{ticker}-26")) is not None:
                    keys.append(key)
            self._series = list(dict.fromkeys(keys))
            self._series_at = now
        return list(self._series)

    async def fetch_odds(self, keys: list[RaceKey], client: httpx.AsyncClient) -> list[OddsSnapshot]:
        if not keys:
            return []

        now = self._clock()
        payloads: dict[RaceKey, dict] = {}
        stale: list[RaceKey] = []  # layout known from an earlier poll: priced by the batch below
        for key in dict.fromkeys(keys):
            event_ticker = self._key_to_event_ticker(key)
            if not event_ticker:
                continue
            missing = self._missing.get(key)
            if missing is not None and now - missing < self.LAYOUT_TTL:
                continue
            known = self._layout.get(key)
            if known is not None and now - known[0] < self.LAYOUT_TTL:
                payloads[key] = known[1]
                stale.append(key)
                continue
            if self._layout or self._missing:
                await asyncio.sleep(self.LOOKUP_SPACING)
            payload = await self._event(client, event_ticker)
            if payload is None:
                self._missing[key] = now
                continue
            self._layout[key] = (now, payload)
            payloads[key] = payload

        if stale:
            tickers = [market["ticker"] for key in stale for market in _markets(payloads[key]) if market.get("ticker")]
            latest = await self._markets(client, tickers)
            for key in stale:
                payload = payloads[key]
                # A ticker the batch did not return (closed, delisted) has no current price.
                markets = [latest[market["ticker"]] for market in _markets(payload) if market.get("ticker") in latest]
                payloads[key] = {"event": payload.get("event") or {}, "markets": markets}

        snapshots: list[OddsSnapshot] = []
        # Stamp with the fetch time: updated_time is market *metadata* time (often weeks
        # old), which would put every price outside the swing detector's look-back window.
        fetched = datetime.now(timezone.utc)
        for key, payload in payloads.items():
            try:
                parsed = parse_kalshi_event(payload)
            except Exception:
                continue  # never raise on one bad race
            snapshots.extend(replace(snapshot, ts=fetched) for snapshot in parsed if snapshot.key == key)
        return snapshots

    async def _event(self, client: httpx.AsyncClient, event_ticker: str) -> dict | None:
        """One race's /events payload, or None when Kalshi has no such event."""
        try:
            resp = await client.get(f"{self.BASE}/events/{event_ticker}")
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"kalshi unreachable: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise SourceUnavailable(f"kalshi returned {resp.status_code} for {event_ticker}")
        try:
            return resp.json()
        except Exception as exc:
            raise SourceUnavailable(f"kalshi bad JSON: {exc}") from exc

    async def _markets(self, client: httpx.AsyncClient, tickers: list[str]) -> dict[str, dict]:
        """Current market rows for many tickers, BATCH per request: {ticker: market}."""
        latest: dict[str, dict] = {}
        for start in range(0, len(tickers), self.BATCH):
            chunk = tickers[start : start + self.BATCH]
            try:
                resp = await client.get(
                    f"{self.BASE}/markets", params={"tickers": ",".join(chunk), "limit": len(chunk)}
                )
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"kalshi unreachable: {exc}") from exc
            if resp.status_code >= 400:
                raise SourceUnavailable(f"kalshi returned {resp.status_code} for a market batch")
            try:
                rows = resp.json().get("markets") or []
            except Exception as exc:
                raise SourceUnavailable(f"kalshi bad JSON: {exc}") from exc
            latest.update({row["ticker"]: row for row in rows if isinstance(row, dict) and row.get("ticker")})
        return latest

    def _key_to_event_ticker(self, key: RaceKey) -> str | None:
        state = key.state
        if key.level is Level.SENATE:
            # Special elections have their own series: SENATEOHS-26 is Ohio's 2026 special.
            series = f"SENATE{state}S" if key.special else f"SENATE{state}"
        elif key.level is Level.GOVERNOR and key.office == "GOV":
            series = self._GOV_OVERRIDES.get(state, f"GOVPARTY{state}")
        elif key.level is Level.HOUSE and key.district is not None:
            if key.district == "AL":
                # Kalshi spells the single statewide seat out: HOUSEAKAL-26.
                series = f"HOUSE{state}AL"
            elif key.district.isdigit():
                # Kalshi House series omit leading zeros in the district.
                series = f"HOUSE{state}{int(key.district)}"
            else:
                return None
        else:
            return None
        return f"{series}-26"


#: Series tickers of single races: SENATETX, SENATEOHS, HOUSEPA1, HOUSEAKAL, GOVPARTYGA, KXGOVAK.
_RACE_SERIES = re.compile(r"SENATE[A-Z]{2}S?|HOUSE[A-Z]{2}(?:\d{1,2}|AL)|GOVPARTY[A-Z]{2}|KXGOV[A-Z]{2}")


def _markets(payload: dict) -> list[dict]:
    event = payload.get("event") or {}
    return payload.get("markets") or event.get("markets") or []


def parse_kalshi_event(payload: dict) -> list[OddsSnapshot]:
    """Parse a Kalshi /events/{event_ticker} response into OddsSnapshot rows."""
    event = payload.get("event") or {}
    markets = _markets(payload)
    event_ticker = event.get("event_ticker", "")
    series_ticker = event.get("series_ticker", "")
    key = _event_ticker_to_key(event_ticker)
    if key is None:
        return []
    url = f"https://kalshi.com/markets/{series_ticker.lower()}"

    snapshots: list[OddsSnapshot] = []
    for market in markets:
        try:
            outcome = _market_outcome(market)
            if outcome is None:
                continue
            prob = _market_prob(market)
            if prob is None:
                continue
            ts = _market_ts(market, event)
            snapshots.append(
                OddsSnapshot(
                    key=key,
                    market="kalshi",
                    outcome=outcome,
                    prob=prob,
                    ts=ts,
                    market_id=market.get("ticker"),
                    url=url,
                    candidate=_market_candidate(market),
                )
            )
        except Exception:
            continue
    return snapshots


def _event_ticker_to_key(event_ticker: str) -> RaceKey | None:
    """Map a Kalshi event ticker to a RaceKey, or None if it is not a race.

    Supports SENATE<ST>-26, SENATE<ST>S-26 (special), GOVPARTY<ST>-26 / KXGOV<ST>-26, HOUSE<ST><DIST>-26 and
    HOUSE<ST>AL-26 (e.g. HOUSEAKAL-26 is the at-large seat, not district "AL").
    Unknown tickers (presidential, control-of-chamber, ...) return None rather
    than being mislabelled as a Senate race.
    """
    parts = event_ticker.rsplit("-", 1)
    base = parts[0]
    try:
        if base.startswith("SENATE"):
            tail = base[6:]
            if len(tail) == 3 and tail.endswith("S"):  # SENATEOHS: the special election
                return RaceKey(tail[:2], Level.SENATE, "SEN", special=True)
            return RaceKey(tail, Level.SENATE, "SEN")
        if base.startswith("GOVPARTY"):
            return RaceKey(base[8:], Level.GOVERNOR, "GOV")
        if base.startswith("KXGOV"):
            return RaceKey(base[5:], Level.GOVERNOR, "GOV")
        if base.startswith("HOUSE"):
            tail = base[5:]
            seat = tail[2:]
            if seat.upper() == "AL":
                return RaceKey(tail[:2], Level.HOUSE, "HOUSE", "AL")
            return RaceKey(tail[:2], Level.HOUSE, "HOUSE", f"{int(seat):02d}")
    except (ValueError, TypeError):
        return None
    return None


def _market_outcome(market: dict) -> str | None:
    # The ticker suffix is Kalshi's canonical outcome; rules text often names
    # the opposing party too ("if the Republican is certified...").
    suffix = (market.get("ticker") or "").rsplit("-", 1)[-1].upper()
    by_suffix = {"R": "REP", "D": "DEM", "I": "IND", "L": "LIB", "G": "GRN"}
    if suffix in by_suffix:
        return by_suffix[suffix]

    stem = f"{market.get('title', '')} {market.get('subtitle', '')}".lower()
    for text in (stem, market.get("rules_primary", "").lower()):
        if "republican" in text:
            return "REP"
        if "democrat" in text:
            return "DEM"
        if "libertarian" in text:
            return "LIB"
        if "green party" in text:
            return "GRN"
    return None


def _market_candidate(market: dict) -> str | None:
    """The candidate a party market stands for: Kalshi puts the name in ``yes_sub_title``
    ("Ken Paxton"); older markets repeat the party there instead, which isn't a name."""
    name = " ".join(str(market.get("yes_sub_title") or "").split())
    if not name or "party" in name.casefold() or name.casefold() in ("yes", "no", "democratic", "republican"):
        return None
    return name[:100]


#: Widest yes bid/ask spread still priced at its midpoint; a wider book uses the last trade.
MAX_SPREAD = 0.10


def _price(market: dict, field: str) -> float | None:
    """A yes price in dollars: the ``*_dollars`` string, or older payloads' integer cents."""
    raw = market.get(f"{field}_dollars")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    raw = market.get(field)
    if raw is None:
        return None
    try:
        return float(raw) / 100.0
    except (TypeError, ValueError):
        return None


def _market_prob(market: dict) -> float | None:
    """Midpoint of a tight yes book; otherwise the last trade, then the bid, then the ask."""
    bid, ask = _price(market, "yes_bid"), _price(market, "yes_ask")
    if bid and ask and bid <= ask and ask - bid <= MAX_SPREAD:
        return _unit_interval(round((bid + ask) / 2, 4))
    for field in ("last_price", "yes_bid", "yes_ask"):
        price = _price(market, field)
        if price:
            return _unit_interval(price)
    return None


def _unit_interval(value: float) -> float | None:
    if value != value or not (0.0 <= value <= 1.0):  # NaN / out of range
        return None
    return value


def _market_ts(market: dict, event: dict) -> datetime:
    raw = market.get("updated_time") or event.get("last_updated_ts")
    if raw:
        try:
            # Kalshi emits ISO-8601 with Z.
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)
