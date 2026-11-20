"""Generate a SYNTHETIC daily dataset for pipeline testing.

This is NOT market data. It exists so the training / walk-forward / backtest code can be
exercised end-to-end without a licensed dataset. Metrics computed on it say nothing about
real forecasting skill. The output carries data_source='SYNTHETIC'.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd

TROY = 31.1034768


def generate(n_days: int = 900, seed: int = 7, start: date = date(2022, 1, 3)) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days, d = [], start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    # volatility clustering
    vol = np.empty(n_days); vol[0] = 0.007
    for i in range(1, n_days):
        vol[i] = max(0.003, 0.9 * vol[i - 1] + 0.1 * 0.007 + 0.002 * abs(rng.normal()))
    comex_c, fx_c, mcx_c = 1900.0, 78.0, 1900.0 * 78.0 / TROY * 10 * 1.06
    rows = []
    for i, day in enumerate(days):
        g = rng.normal(0, vol[i])                    # overnight COMEX move
        fx = rng.normal(0, 0.0015)                   # overnight USD/INR move
        later = rng.normal(0, vol[i] * 0.6)          # rest-of-day COMEX move
        e = rng.normal(0, 0.003)                     # MCX-specific noise
        comex_m, fx_m = comex_c * (1 + g), fx_c * (1 + fx)
        mcx_ret = (1 + g) * (1 + fx) - 1 + 0.5 * later + e
        comex_new = comex_m * (1 + later)
        fx_new = fx_m * (1 + rng.normal(0, 0.0007))
        mcx_new = mcx_c * (1 + mcx_ret)
        rows.append(dict(date=day, mcx_close=mcx_new, comex_close=comex_new, xau_close=comex_new * 0.998,
                         usdinr_close=fx_new, comex_morning=comex_m, xau_morning=comex_m * 0.998,
                         usdinr_morning=fx_m, data_source="SYNTHETIC"))
        comex_c, fx_c, mcx_c = comex_new, fx_new, mcx_new
    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic_history.csv")
    ap.add_argument("--days", type=int, default=900)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    generate(a.days, a.seed).to_csv(a.out, index=False)
    print(f"wrote {a.out} (SYNTHETIC)")
