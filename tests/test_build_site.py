from datetime import datetime
from zoneinfo import ZoneInfo

from src.build_site import latest_payload
from src.config import load_config
from src.models import beta_estimate, units_from_1g
from src.storage import write_json

IST = ZoneInfo("Asia/Kolkata")
CALC_FIELDS = {
    "reference_time_ist", "reference_intl_inr_per_g", "latest_intl_inr_per_g",
    "latest_price_time_ist", "move_pct", "beta", "premium", "model",
    "board_today_1g", "raw_estimate", "rounded_estimate", "range_lo", "range_hi",
    "range_method", "market_closed",
}


def _cfg(tmp_path):
    cfg = load_config()
    cfg["_root"] = str(tmp_path)
    cfg["paths"] = {**cfg["paths"], "board": "board.csv", "forecasts": "forecasts.csv", "params": "params.json"}
    write_json(tmp_path / "params.json", {"beta": 0.8, "premium": 0.07, "champion": "beta"})
    return cfg


def test_calc_shows_the_champion_arithmetic(tmp_path):
    cfg = _cfg(tmp_path)
    (tmp_path / "board.csv").write_text(
        "date,source,rate_22k_1g,scraped_at_ist\n"
        "2026-11-20,legacy,11510.00,2026-11-20T10:24:41+05:30\n"
    )
    (tmp_path / "forecasts.csv").write_text(
        "target_date,kind,made_at_ist,model,estimate,lo,hi,actual,error\n"
        "2026-11-20,backtest,2026-11-20T05:45:00+05:30,beta,11500,11400,11600,11510.00,10.00\n"
    )
    points = [
        (datetime(2026, 11, 20, 9, 0, tzinfo=IST), 100.0),
        (datetime(2026, 11, 20, 22, 0, tzinfo=IST), 110.0),
    ]
    payload, _series = latest_payload(cfg, points)
    calc = payload["calc"]
    assert CALC_FIELDS <= set(calc)
    raw = beta_estimate(11510, 0.10, 0.8)
    assert calc["model"] == "beta"
    assert calc["premium"] is None
    assert calc["beta"] == 0.8
    assert calc["board_today_1g"] == 11510
    assert calc["reference_intl_inr_per_g"] == 100
    assert calc["latest_intl_inr_per_g"] == 110
    assert calc["move_pct"] == 10
    assert calc["market_closed"] is False
    assert calc["raw_estimate"] == round(raw, 4)
    assert calc["rounded_estimate"] == units_from_1g(raw)["g1"]
    assert calc["range_lo"] < calc["rounded_estimate"] < calc["range_hi"]
    assert calc["range_method"] == "80% of recent misses, 1 days"
    assert "2026-11-20T09:00:00" in calc["reference_time_ist"]
    assert "2026-11-20T22:00:00" in calc["latest_price_time_ist"]


def test_calc_is_null_without_a_price_and_zero_when_the_market_is_closed(tmp_path):
    cfg = _cfg(tmp_path)
    (tmp_path / "board.csv").write_text(
        "date,source,rate_22k_1g,scraped_at_ist\n"
        "2026-11-15,manual,12615.00,2026-11-15T10:00:00+05:30\n"
    )
    assert latest_payload(cfg, [])[0]["calc"] is None
    points = [(datetime(2026, 11, 14, 2, 0, tzinfo=IST), 120.0)]
    calc = latest_payload(cfg, points)[0]["calc"]
    assert calc["market_closed"] is True
    assert calc["move_pct"] == 0
    assert calc["rounded_estimate"] == 12615
    assert calc["reference_intl_inr_per_g"] == 120
    assert calc["latest_intl_inr_per_g"] == 120
