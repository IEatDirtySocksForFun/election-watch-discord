from datetime import datetime, timedelta, timezone

from electionwatch.models import OddsSnapshot
from electionwatch.racekeys import parse
from electionwatch.swings import detect_swing

TZ = timezone.utc
KEY = parse("TX SEN")


def _snap(prob: float, offset_min: int, outcome: str = "REP") -> OddsSnapshot:
    return OddsSnapshot(
        key=KEY,
        market="kalshi",
        outcome=outcome,
        prob=prob,
        ts=datetime(2026, 11, 3, 18, 0, tzinfo=TZ) + timedelta(minutes=offset_min),
        market_id="txsen-rep",
    )


def test_no_swing_below_threshold():
    hist = [_snap(0.52, 0), _snap(0.53, 30)]
    assert detect_swing(hist, pts=5, window=timedelta(hours=2)) is None


def test_swing_inside_window():
    hist = [_snap(0.48, 0), _snap(0.61, 45)]
    swing, earlier, latest = detect_swing(hist, pts=10, window=timedelta(hours=2))
    assert swing == 13.0
    assert earlier.prob == 0.48
    assert latest.prob == 0.61


def test_outside_window_ignored():
    hist = [_snap(0.30, 0), _snap(0.60, 130)]
    assert detect_swing(hist, pts=10, window=timedelta(hours=2)) is None


def test_largest_swing_chosen():
    hist = [
        _snap(0.50, 0),
        _snap(0.55, 20),
        _snap(0.40, 40),
        _snap(0.62, 60),
    ]
    swing, earlier, latest = detect_swing(hist, pts=5, window=timedelta(hours=2))
    # largest move is 0.40 -> 0.62 = 22 pts
    assert swing == 22.0
    assert earlier.prob == 0.40
    assert latest.prob == 0.62


def test_exact_threshold_returns():
    hist = [_snap(0.50, 0), _snap(0.55, 30)]
    swing = detect_swing(hist, pts=5, window=timedelta(hours=2))
    assert swing is not None
    assert swing[0] == 5.0


def test_negative_swing_detected():
    hist = [_snap(0.70, 0), _snap(0.55, 30)]
    swing, earlier, latest = detect_swing(hist, pts=10, window=timedelta(hours=2))
    assert swing == -15.0
    assert earlier.prob == 0.70
    assert latest.prob == 0.55


def test_tie_earliest_anchor():
    hist = [
        _snap(0.50, 0),
        _snap(0.55, 20),
        _snap(0.55, 40),
        _snap(0.60, 60),
    ]
    swing, earlier, latest = detect_swing(hist, pts=5, window=timedelta(hours=2))
    # moves: 0.50->0.60 = 10, 0.55->0.60 = 5; pick largest = 10 anchored at earliest 0.50
    assert swing == 10.0
    assert earlier.prob == 0.50
