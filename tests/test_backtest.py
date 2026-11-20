from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.backtest import backtest_rows, polluted
from src.config import load_config

IST = ZoneInfo("Asia/Kolkata")


def _points():
    return [
        (datetime(2026, 11, 13, 9, 0, tzinfo=IST), 100.0),
        (datetime(2026, 11, 13, 22, 0, tzinfo=IST), 110.0),
        (datetime(2026, 11, 14, 2, 0, tzinfo=IST), 120.0),
        (datetime(2026, 11, 14, 8, 0, tzinfo=IST), 200.0),  # after the 05:45 cutoff
        (datetime(2026, 11, 16, 5, 0, tzinfo=IST), 90.0),
    ]


def test_backtest_uses_only_earlier_boards_and_prices_before_0545():
    history = [
        (date(2026, 11, 13), 10000.0),
        (date(2026, 11, 14), 11000.0),
        (date(2026, 11, 15), 11000.0),
    ]
    rows = backtest_rows(history, _points(), load_config())
    beta = {row["target_date"]: row for row in rows if row["model"] == "beta"}
    # Saturday sees the 02:00 close (120), not the 08:00 bar. Prior beta is 1.
    assert beta["2026-11-14"]["estimate"] == "12000"
    assert beta["2026-11-14"]["actual"] == "11000.00"
    # Sunday has no newer print than Saturday's reference, so the board is repeated.
    assert beta["2026-11-15"]["estimate"] == "11000"
    assert beta["2026-11-15"]["error"] == "0.00"
    assert all(row["kind"] == "backtest" for row in rows)


def test_changing_a_later_board_does_not_change_an_earlier_backtest():
    cfg = load_config()
    history = [
        (date(2026, 11, 13), 10000.0),
        (date(2026, 11, 14), 11000.0),
        (date(2026, 11, 15), 11000.0),
    ]
    original = backtest_rows(history, _points(), cfg)
    changed = history[:-1] + [(history[-1][0], 1.0)]
    assert backtest_rows(changed, _points(), cfg)[:-2] == original[:-2]


def test_midnight_test_rows_are_the_ones_removed():
    assert polluted({"kind": "final", "made_at_ist": "2026-11-21T00:01:15+05:30"})
    assert polluted({"kind": "evening", "made_at_ist": "2026-11-21T00:01:15+05:30"})
    assert not polluted({"kind": "backtest", "made_at_ist": "2026-11-14T05:45:00+05:30"})
    assert not polluted({"kind": "final", "made_at_ist": "2026-11-21T05:45:00+05:30"})
