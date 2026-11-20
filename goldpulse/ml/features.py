"""Leak-free feature engineering.

Row D describes Indian trading date D. At the 08:30 IST prediction time on D we know:
  * everything up to the MCX close of D-1 (the previous row's *_close and mcx_close)
  * the morning snapshots of D (comex/xau/usdinr _morning)
We do NOT know mcx_close[D] (the target), nor comex_close[D].  Every feature below is
built only from `shift(>=1)` of close columns and same-row *_morning columns.
`tests/test_features.py` perturbs future values and asserts features do not change.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TROY_OZ_G = 31.1034768

FEATURE_COLUMNS = [
    "comex_overnight", "xau_overnight", "fx_overnight", "parity_ret",
    "mcx_ret_lag1", "mcx_ret_lag2", "mcx_ret_lag3",
    "comex_ret_lag1", "vol_10", "mom_5", "premium_prev",
]


def build_features(df: pd.DataFrame, with_target: bool = True) -> pd.DataFrame:
    d = df.copy().reset_index(drop=True)
    f = pd.DataFrame({"date": d["date"]})
    f["comex_overnight"] = d["comex_morning"] / d["comex_close"].shift(1) - 1
    f["xau_overnight"] = d["xau_morning"] / d["xau_close"].shift(1) - 1
    f["fx_overnight"] = d["usdinr_morning"] / d["usdinr_close"].shift(1) - 1
    f["parity_ret"] = (1 + f["comex_overnight"]) * (1 + f["fx_overnight"]) - 1

    mcx_ret = d["mcx_close"].pct_change()          # ret[D] uses close[D]; we only use it lagged
    f["mcx_ret_lag1"] = mcx_ret.shift(1)
    f["mcx_ret_lag2"] = mcx_ret.shift(2)
    f["mcx_ret_lag3"] = mcx_ret.shift(3)
    f["comex_ret_lag1"] = d["comex_close"].pct_change().shift(1)
    f["vol_10"] = mcx_ret.rolling(10, min_periods=10).std().shift(1)
    f["mom_5"] = d["mcx_close"].shift(1) / d["mcx_close"].shift(6) - 1
    parity_prev = d["comex_close"] * d["usdinr_close"] / TROY_OZ_G * 10
    f["premium_prev"] = np.log(d["mcx_close"] / parity_prev).shift(1)

    f["prev_mcx_close"] = d["mcx_close"].shift(1)
    # high-vol regime: vol above the *expanding* median of past vol (no look-ahead)
    f["high_vol"] = (f["vol_10"] > f["vol_10"].expanding(min_periods=30).median().shift(1)).astype(float)
    if with_target and "mcx_close" in d:
        f["target_ret"] = d["mcx_close"] / d["mcx_close"].shift(1) - 1
        f["target_close"] = d["mcx_close"]
    return f


def label_direction(ret: pd.Series | np.ndarray, neutral_threshold: float) -> np.ndarray:
    """ret and threshold as fractions (0.001 = 0.1%). Returns 'UP'/'DOWN'/'NEUTRAL'."""
    r = np.asarray(ret, dtype=float)
    return np.where(r > neutral_threshold, "UP", np.where(r < -neutral_threshold, "DOWN", "NEUTRAL"))


def usable_rows(f: pd.DataFrame, need_target: bool = True) -> pd.DataFrame:
    cols = FEATURE_COLUMNS + (["target_ret"] if need_target else [])
    return f.dropna(subset=cols).reset_index(drop=True)


def live_feature_row(history: pd.DataFrame, target_date, comex_morning: float,
                     xau_morning: float, usdinr_morning: float) -> pd.DataFrame:
    """Features for a not-yet-traded date by appending a placeholder row."""
    nxt = {c: np.nan for c in history.columns}
    nxt.update(date=target_date, comex_morning=comex_morning, xau_morning=xau_morning,
               usdinr_morning=usdinr_morning)
    ext = pd.concat([history, pd.DataFrame([nxt])], ignore_index=True)
    return build_features(ext, with_target=False).iloc[[-1]].reset_index(drop=True)
