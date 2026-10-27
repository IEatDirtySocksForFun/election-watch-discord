"""Kalshi public market-data adapter for election odds.

Discovers 2026 U.S. election events from series tickers and parses each market
as an OddsSnapshot. No API key is required for reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

import httpx

from electionwatch.models import Level, OddsSnapshot, RaceKey
from electionwatch.racekeys import STATES
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

    async def fetch_odds(
        self, keys: list[RaceKey], client: httpx.AsyncClient
    ) -> list[OddsSnapshot]:
        if not keys:
            return []

        snapshots: list[OddsSnapshot] = []
        for key in keys:
            event_ticker = self._key_to_event_ticker(key)
            if not event_ticker:
                continue
            url = f"{self.BASE}/events/{event_ticker}"
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise SourceUnavailable(f"kalshi unreachable: {exc}") from exc
            if resp.status_code == 404:
                continue
            if resp.status_code >= 400:
                raise SourceUnavailable(
                    f"kalshi returned {resp.status_code} for {event_ticker}"
                )
            try:
                payload = resp.json()
            except Exception as exc:
                raise SourceUnavailable(f"kalshi bad JSON: {exc}") from exc
            try:
                snaps = parse_kalshi_event(payload)
            except Exception:
                # Never raise on one bad race.
                continue
            snapshots.extend(s for s in snaps if s.key == key)
        return snapshots

    def _key_to_event_ticker(self, key: RaceKey) -> str | None:
        state = key.state
        if key.level is Level.SENATE:
            series = f"SENATE{state}"
        elif key.level is Level.GOVERNOR and key.office == "GOV":
            series = self._GOV_OVERRIDES.get(state, f"GOVPARTY{state}")
        elif key.level is Level.HOUSE and key.district is not None:
            # Kalshi House series omit leading zeros in the district.
            series = f"HOUSE{state}{int(key.district)}"
        else:
            return None
        return f"{series}-26"


def parse_kalshi_event(payload: dict) -> list[OddsSnapshot]:
    """Parse a Kalshi /events/{event_ticker} response into OddsSnapshot rows."""
    event = payload.get("event") or {}
    markets = payload.get("markets") or event.get("markets") or []
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
                )
            )
        except Exception:
            continue
    return snapshots


def _event_ticker_to_key(event_ticker: str) -> RaceKey | None:
    """Map a Kalshi event ticker to a RaceKey, or None if it is not a race.

    Supports SENATE<ST>-26, GOVPARTY<ST>-26 / KXGOV<ST>-26, HOUSE<ST><DIST>-26.
    Unknown tickers (presidential, control-of-chamber, ...) return None rather
    than being mislabelled as a Senate race.
    """
    parts = event_ticker.rsplit("-", 1)
    base = parts[0]
    try:
        if base.startswith("SENATE"):
            return RaceKey(base[6:], Level.SENATE, "SEN")
        if base.startswith("GOVPARTY"):
            return RaceKey(base[8:], Level.GOVERNOR, "GOV")
        if base.startswith("KXGOV"):
            return RaceKey(base[5:], Level.GOVERNOR, "GOV")
        if base.startswith("HOUSE"):
            tail = base[5:]
            return RaceKey(tail[:2], Level.HOUSE, "HOUSE", f"{int(tail[2:]):02d}")
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


def _market_prob(market: dict) -> float | None:
    for field in ("last_price_dollars", "yes_bid_dollars", "yes_ask_dollars"):
        val = market.get(field)
        if val:
            try:
                return _unit_interval(float(val))
            except (TypeError, ValueError):
                pass
    # Older payloads quote integer cents rather than *_dollars strings.
    for field in ("last_price", "yes_bid", "yes_ask"):
        val = market.get(field)
        if val is not None:
            try:
                return _unit_interval(float(val) / 100.0)
            except (TypeError, ValueError):
                pass
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
