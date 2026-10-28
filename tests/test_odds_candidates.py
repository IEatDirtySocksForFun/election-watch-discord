"""/odds names the candidates: Kalshi's yes_sub_title and Polymarket's "Name (D)" titles."""

from datetime import datetime, timezone

from electionwatch.bot.command_data import format_odds, format_odds_overview
from electionwatch.models import OddsSnapshot
from electionwatch.racekeys import parse
from electionwatch.sources.odds.kalshi import parse_kalshi_event
from electionwatch.sources.odds.polymarket import parse_polymarket_event
from electionwatch.store.memory import MemoryStore

NOW = datetime(2026, 10, 28, 23, 0, tzinfo=timezone.utc)
PA = parse("PA-08")


def kalshi_market(side: str, name: str, price: float) -> dict:
    party = {"D": "Democratic", "R": "Republican"}[side]
    return {
        "ticker": f"HOUSEPA8-26-{side}",
        "title": f"Will {party} win the House race for PA-8?",
        "subtitle": f"{party} party:: {party} party",
        "yes_sub_title": name,
        "last_price_dollars": str(price),
        "updated_time": "2026-10-28T22:00:00Z",
    }


def test_kalshi_markets_carry_the_candidates_name():
    payload = {
        "event": {"event_ticker": "HOUSEPA8-26", "series_ticker": "HOUSEPA8"},
        "markets": [
            kalshi_market("D", "Paige Cognetti", 0.55),
            kalshi_market("R", "Rob Bresnahan", 0.45),
            kalshi_market("D", "Democratic party", 0.5) | {"ticker": "HOUSEPA8-26-X"},
        ],
    }
    snapshots = parse_kalshi_event(payload)
    names = {
        snapshot.outcome: snapshot.candidate for snapshot in snapshots if snapshot.market_id.endswith(("-D", "-R"))
    }
    assert names == {"DEM": "Paige Cognetti", "REP": "Rob Bresnahan"}
    assert all(snapshot.candidate != "Democratic party" for snapshot in snapshots)


def test_polymarket_titles_give_the_name_without_the_party_letter():
    event = {
        "title": "Texas Senate Election Winner",
        "slug": "texas-senate",
        "markets": [
            {
                "id": 1,
                "groupItemTitle": "James Talarico (D)",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.68","0.32"]',
            },
            {
                "id": 2,
                "groupItemTitle": "Ken Paxton (R)",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.32","0.68"]',
            },
        ],
    }
    snapshots = parse_polymarket_event(event)
    assert {snapshot.outcome: snapshot.candidate for snapshot in snapshots} == {
        "DEM": "James Talarico",
        "REP": "Ken Paxton",
    }


def test_odds_embeds_show_name_and_party():
    snapshots = [
        OddsSnapshot(
            PA, "kalshi", "DEM", 0.55, NOW, "HOUSEPA8-26-D", "https://kalshi.com/markets/housepa8", "Paige Cognetti"
        ),
        OddsSnapshot(PA, "kalshi", "REP", 0.45, NOW, "HOUSEPA8-26-R", None, "Rob Bresnahan"),
        OddsSnapshot(PA, "polymarket", "Jane Doe (D)", 0.5, NOW, "3", None, "Jane Doe"),  # same-party pair
    ]
    text = "\n".join(field.value for field in format_odds(PA, snapshots).fields)
    assert "[Paige Cognetti (DEM)](https://kalshi.com/markets/housepa8) · 55.0%" in text
    assert "Rob Bresnahan (REP) · 45.0%" in text
    assert "Jane Doe (D) · 50.0%" in text  # the label already names her
    assert "Paige Cognetti (DEM)" in format_odds_overview([(PA, snapshots)]).description


async def test_snapshots_without_a_name_still_load_and_render():
    store = MemoryStore()
    await store.add_odds(OddsSnapshot(PA, "kalshi", "DEM", 0.55, NOW))
    [snapshot] = await store.latest_odds(PA)
    assert snapshot.candidate is None
    assert "DEM · 55.0%" in format_odds(PA, [snapshot]).fields[0].value
