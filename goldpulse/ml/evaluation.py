"""Chronological evaluation: split by date, walk-forward validation, metrics, calibration."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_fscore_support

from goldpulse.ml.features import FEATURE_COLUMNS, label_direction
from goldpulse.ml.models import CLASSES, GBMDirection


def chronological_split(f: pd.DataFrame, test_fraction: float = 0.2):
    """Split by position in time order. Never shuffles."""
    if not f["date"].is_monotonic_increasing:
        raise ValueError("frame must be date-sorted")
    cut = int(len(f) * (1 - test_fraction))
    return f.iloc[:cut].reset_index(drop=True), f.iloc[cut:].reset_index(drop=True)


def walk_forward(f: pd.DataFrame, model_factory, min_train: int, step: int = 20,
                 neutral_threshold: float = 0.001, direction: bool = False) -> pd.DataFrame:
    """Expanding-window walk-forward. Models are refit every `step` rows on strictly earlier rows.

    Returns a frame (one row per out-of-sample date) with pred_ret, actual_ret, prev_close,
    pred_dir, actual_dir, high_vol, and (if direction=True) classifier probabilities.
    """
    if len(f) <= min_train:
        raise ValueError(f"need > {min_train} usable rows for walk-forward, have {len(f)}")
    rows = []
    for start in range(min_train, len(f), step):
        train, test = f.iloc[:start], f.iloc[start:start + step]
        m = model_factory().fit(train, train["target_ret"].to_numpy())
        pred = m.predict(test)
        out = pd.DataFrame({
            "date": test["date"].to_numpy(), "pred_ret": pred, "actual_ret": test["target_ret"].to_numpy(),
            "prev_close": test["prev_mcx_close"].to_numpy(), "actual_close": test["target_close"].to_numpy(),
            "high_vol": test["high_vol"].to_numpy(),
        })
        out["pred_dir"] = label_direction(pred, neutral_threshold)
        if direction:
            clf = GBMDirection().fit(train, label_direction(train["target_ret"], neutral_threshold))
            proba = clf.predict_proba(test).reset_index(drop=True)
            for c in CLASSES:
                out[f"p_{c}"] = proba[c].to_numpy()
            out["clf_dir"] = clf.predict(test)
        rows.append(out)
    res = pd.concat(rows, ignore_index=True)
    res["actual_dir"] = label_direction(res["actual_ret"], neutral_threshold)
    return res


def compute_metrics(res: pd.DataFrame, dir_col: str = "pred_dir") -> dict:
    """All metrics from out-of-sample frame. Price metrics are on INR/10g price level."""
    if res.empty:
        raise ValueError("empty evaluation frame")
    pred_price = res["prev_close"] * (1 + res["pred_ret"])
    err = pred_price - res["actual_close"]
    out = {
        "n": int(len(res)),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "mape_pct": float((np.abs(err) / res["actual_close"]).mean() * 100),
        "directional_accuracy": float((res[dir_col] == res["actual_dir"]).mean()),
        "directional_accuracy_updown_only": _updown_acc(res, dir_col),
    }
    p, r, _, sup = precision_recall_fscore_support(
        res["actual_dir"], res[dir_col], labels=["UP", "DOWN"], zero_division=0)
    out.update(precision_up=float(p[0]), recall_up=float(r[0]), precision_down=float(p[1]),
               recall_down=float(r[1]), support_up=int(sup[0]), support_down=int(sup[1]))
    hv = res[res["high_vol"] == 1.0]
    out["high_vol_n"] = int(len(hv))
    if len(hv) >= 10:
        e = hv["prev_close"] * (1 + hv["pred_ret"]) - hv["actual_close"]
        out["high_vol_mae"] = float(np.abs(e).mean())
        out["high_vol_directional_accuracy"] = float((hv[dir_col] == hv["actual_dir"]).mean())
    else:
        out["high_vol_mae"] = None
        out["high_vol_directional_accuracy"] = None
    return out


def _updown_acc(res, dir_col):
    m = res["actual_dir"] != "NEUTRAL"
    return float((res.loc[m, dir_col] == res.loc[m, "actual_dir"]).mean()) if m.any() else None


def expected_calibration_error(res: pd.DataFrame, bins: int = 10) -> float | None:
    """ECE of top-class probability from walk-forward classifier output. None if unavailable."""
    cols = [f"p_{c}" for c in CLASSES]
    if not all(c in res for c in cols) or res[cols].isna().all().all():
        return None
    probs = res[cols].to_numpy()
    conf = probs.max(axis=1)
    pred = np.array(CLASSES)[probs.argmax(axis=1)]
    correct = (pred == res["actual_dir"].to_numpy()).astype(float)
    edges = np.linspace(conf.min(), conf.max() + 1e-9, bins + 1)
    ece, n = 0.0, len(res)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf >= lo) & (conf < hi)
        if m.any():
            ece += m.sum() / n * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def residual_interval(res: pd.DataFrame, level: float) -> dict | None:
    """Empirical quantiles of out-of-sample return residuals -> prediction interval for returns."""
    resid = (res["actual_ret"] - res["pred_ret"]).to_numpy()
    if len(resid) < 30:
        return None
    a = (1 - level) / 2
    return {"low": float(np.quantile(resid, a)), "high": float(np.quantile(resid, 1 - a)),
            "n": int(len(resid)), "level": level}


def interval_coverage(res: pd.DataFrame, iv: dict) -> float:
    lo = res["pred_ret"] + iv["low"]; hi = res["pred_ret"] + iv["high"]
    return float(((res["actual_ret"] >= lo) & (res["actual_ret"] <= hi)).mean())
