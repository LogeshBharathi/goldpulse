from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.forecast import forecast_allowed, run
from src.storage import read_csv

IST = ZoneInfo("Asia/Kolkata")


def test_scheduled_windows_accept_a_late_cron_and_reject_midnight():
    assert forecast_allowed("final", datetime(2026, 11, 21, 5, 0, tzinfo=IST))
    assert forecast_allowed("final", datetime(2026, 11, 21, 5, 45, tzinfo=IST))
    assert forecast_allowed("final", datetime(2026, 11, 21, 6, 30, tzinfo=IST))
    assert not forecast_allowed("final", datetime(2026, 11, 21, 6, 31, tzinfo=IST))
    assert forecast_allowed("evening", datetime(2026, 11, 21, 21, 0, tzinfo=IST))
    assert forecast_allowed("evening", datetime(2026, 11, 21, 22, 0, tzinfo=IST))
    assert forecast_allowed("evening", datetime(2026, 11, 21, 23, 59, tzinfo=IST))
    assert not forecast_allowed("evening", datetime(2026, 11, 22, 0, 0, tzinfo=IST))
    assert not forecast_allowed("final", datetime(2026, 11, 21, 0, 1, tzinfo=IST))


def test_midnight_run_writes_nothing_unless_forced(tmp_path):
    cfg = load_config()
    cfg["_root"] = str(tmp_path)
    cfg["paths"] = {**cfg["paths"], "board": "board.csv", "forecasts": "forecasts.csv", "params": "params.json"}
    (tmp_path / "board.csv").write_text(
        "date,source,rate_22k_1g,scraped_at_ist\n"
        "2026-11-20,legacy,11510.00,2026-11-20T10:24:41+05:30\n"
    )
    points = [
        (datetime(2026, 11, 20, 9, 0, tzinfo=IST), 100.0),
        (datetime(2026, 11, 20, 22, 0, tzinfo=IST), 101.0),
    ]
    midnight = datetime(2026, 11, 21, 0, 1, tzinfo=IST)
    with pytest.raises(RuntimeError, match="05:00-06:30"):
        run("final", cfg=cfg, now=midnight, points=points)
    assert not (tmp_path / "forecasts.csv").exists()
    rows = run("final", cfg=cfg, now=midnight, points=points, force=True)
    assert rows and all(row["kind"] == "final" for row in rows)
    assert read_csv(tmp_path / "forecasts.csv")
