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


def _event_ticker_to_key(event_ticker: str) -> RaceKey:
    """Best-effort mapping from a Kalshi event ticker to a RaceKey.

    Supports SENATE<ST>-26, GOVPARTY<ST>-26 / KXGOV<ST>-26, HOUSE<ST><DIST>-26.
    Falls back to a placeholder key if the pattern is unknown.
    """
    parts = event_ticker.rsplit("-", 1)
    base = parts[0]
    if base.startswith("SENATE"):
        state = base[6:]
        return RaceKey(state, Level.SENATE, "SEN")
    if base.startswith("GOVPARTY"):
        state = base[8:]
        return RaceKey(state, Level.GOVERNOR, "GOV")
    if base.startswith("KXGOV"):
        state = base[5:]
        return RaceKey(state, Level.GOVERNOR, "GOV")
    if base.startswith("HOUSE"):
        tail = base[5:]
        state = tail[:2]
        district = tail[2:]
        return RaceKey(state, Level.HOUSE, "HOUSE", f"{int(district):02d}")
    return RaceKey("US", Level.SENATE, "SEN")


def _market_outcome(market: dict) -> str | None:
    title = market.get("title", "")
    subtitle = market.get("subtitle", "")
    rules = market.get("rules_primary", "")
    text = f"{title} {subtitle} {rules}".lower()
    if "republican" in text:
        return "REP"
    if "democrat" in text:
        return "DEM"
    # Fallback to ticker suffix (e.g. SENATETX-26-R)
    ticker = market.get("ticker", "")
    suffix = ticker.rsplit("-", 1)[-1].upper()
    return {"R": "REP", "D": "DEM", "I": "IND", "L": "LIB", "G": "GRN"}.get(suffix)


def _market_prob(market: dict) -> float | None:
    for field in ("last_price_dollars", "yes_bid_dollars", "yes_ask_dollars"):
        val = market.get(field)
        if val:
            try:
                return float(val)
            except Exception:
                pass
    return None


def _market_ts(market: dict, event: dict) -> datetime:
    raw = market.get("updated_time") or event.get("last_updated_ts")
    if raw:
        try:
            # Kalshi emits ISO-8601 with Z.
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)
