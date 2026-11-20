"""Full-system replay on historical (synthetic) data: every day passes through the real service."""
import pandas as pd

from goldpulse.config import Settings
from scripts.backtest import replay


def test_full_system_backtest_runs_without_leakage_and_reports(synth):
    s = Settings()
    res, summ = replay(synth, n_days=12, thr=0.001, settings=s, synthetic=True, retrain_every=6)
    assert len(res) == 12 and summ["n"] == 12
    assert res["date"].is_monotonic_increasing
    # every evaluated prediction used the previous close as base, never the day's own close
    prev = synth.set_index("date")["mcx_close"].shift(1)
    for _, r in res.iterrows():
        assert r["actual_close"] == synth.set_index("date").loc[r["date"], "mcx_close"]
        assert abs(r["predicted_price"] / prev.loc[r["date"]] - 1) < 0.1
    assert 0 <= summ["directional_accuracy"] <= 1 and summ["mape_pct"] < 3
    assert "meaningful" in summ["note"]
