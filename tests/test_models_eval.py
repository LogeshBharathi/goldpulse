import numpy as np
import pandas as pd
import pytest

from goldpulse.ml.evaluation import (compute_metrics, expected_calibration_error, interval_coverage,
                                     residual_interval, walk_forward)
from goldpulse.ml.features import build_features, usable_rows
from goldpulse.ml.models import GBMDirection, ParityBaseline, RidgeModel
from goldpulse.ml.registry import load_bundle, save_bundle, train

THR = 0.001


@pytest.fixture(scope="module")
def frame(synth):
    return usable_rows(build_features(synth))


def test_metrics_hand_computed():
    res = pd.DataFrame({
        "pred_ret": [0.01, -0.01, 0.0], "actual_ret": [0.02, -0.02, 0.0005],
        "prev_close": [100.0, 100.0, 100.0], "actual_close": [102.0, 98.0, 100.05],
        "high_vol": [0.0, 0.0, 0.0],
        "pred_dir": ["UP", "DOWN", "NEUTRAL"], "actual_dir": ["UP", "DOWN", "NEUTRAL"]})
    m = compute_metrics(res)
    # predicted prices 101, 99, 100 -> errors -1, +1, -0.05
    assert m["mae"] == pytest.approx((1 + 1 + 0.05) / 3)
    assert m["rmse"] == pytest.approx(np.sqrt((1 + 1 + 0.0025) / 3))
    assert m["mape_pct"] == pytest.approx(np.mean([1 / 102, 1 / 98, 0.05 / 100.05]) * 100)
    assert m["directional_accuracy"] == 1.0 and m["precision_up"] == 1.0 and m["recall_down"] == 1.0
    assert m["high_vol_mae"] is None  # too few observations to report, not invented


def test_precision_recall():
    res = pd.DataFrame({
        "pred_ret": [0.01] * 4, "actual_ret": [0.01, -0.01, 0.01, 0.01], "prev_close": [100.0] * 4,
        "actual_close": [101, 99, 101, 101], "high_vol": [0.0] * 4,
        "pred_dir": ["UP", "UP", "DOWN", "UP"], "actual_dir": ["UP", "DOWN", "UP", "UP"]})
    m = compute_metrics(res)
    assert m["precision_up"] == pytest.approx(2 / 3) and m["recall_up"] == pytest.approx(2 / 3)
    assert m["precision_down"] == 0.0 and m["recall_down"] == 0.0


def test_walk_forward_trains_only_on_past(frame):
    fits = []          # (last training date, n training rows)
    checks = []        # True per predict call if the test block is strictly after training
    class Spy(ParityBaseline):
        def fit(self, X, y):
            fits.append((X["date"].max(), len(X))); return self
        def predict(self, X):
            checks.append(X["date"].min() > fits[-1][0])
            return super().predict(X)
    res = walk_forward(frame, Spy, min_train=200, step=50, neutral_threshold=THR)
    assert len(res) == len(frame) - 200 and res["date"].is_monotonic_increasing
    assert checks and all(checks)
    sizes = [n for _, n in fits]
    assert sizes == sorted(sizes) and sizes[0] == 200   # expanding window


def test_walk_forward_requires_enough_data(frame):
    with pytest.raises(ValueError):
        walk_forward(frame.iloc[:100], ParityBaseline, min_train=200)


def test_baseline_has_real_skill_on_synthetic_parity_structure(frame):
    res = walk_forward(frame, ParityBaseline, 250, 50, THR)
    m = compute_metrics(res)
    # Synthetic data is built so parity explains most of the move -> this validates the plumbing only.
    assert m["directional_accuracy"] > 0.5 and m["mape_pct"] < 2


def test_direction_classifier_and_calibration(frame):
    res = walk_forward(frame, RidgeModel, 250, 50, THR, direction=True)
    assert {"p_UP", "p_DOWN", "p_NEUTRAL"} <= set(res.columns)
    np.testing.assert_allclose(res[["p_UP", "p_DOWN", "p_NEUTRAL"]].sum(axis=1), 1.0, atol=1e-6)
    ece = expected_calibration_error(res)
    assert ece is not None and 0 <= ece <= 1
    assert expected_calibration_error(res.drop(columns=["p_UP"])) is None


def test_interval_empirical_coverage(frame):
    res = walk_forward(frame, RidgeModel, 250, 50, THR)
    iv = residual_interval(res, 0.8)
    assert iv["low"] < 0 < iv["high"]
    assert interval_coverage(res, iv) == pytest.approx(0.8, abs=0.05)   # in-sample for the OOS residuals
    assert residual_interval(res.iloc[:10], 0.8) is None                 # refuses tiny samples


def test_single_class_classifier_does_not_crash(frame):
    clf = GBMDirection().fit(frame.iloc[:50], np.array(["UP"] * 50))
    assert clf.predict(frame.iloc[:3]).tolist() == ["NEUTRAL", "NEUTRAL", "NEUTRAL"]


def test_insufficient_data_falls_back_to_baseline_with_limitation(synth):
    b = train(synth.iloc[:150], THR, min_train_rows=250, is_synthetic=True)
    assert b.meta["chosen_model"] == "baseline_parity"
    assert any("Insufficient history" in s for s in b.meta["limitations"])
    assert b.meta["calibrated"] is False and b.meta["interval"] is None


def test_full_training_selection_versioning_and_roundtrip(synth, tmp_path):
    b = train(synth, THR, min_train_rows=250, is_synthetic=True)
    m = b.meta
    assert m["synthetic"] and any("SYNTHETIC" in s for s in m["limitations"])
    cands = m["metrics_all_candidates"]
    assert set(cands) >= {"baseline_parity", "linear_ridge", "ar3", "gbm_regressor"}
    # chosen model never worse than baseline out-of-sample (by construction of selection)
    assert cands[m["chosen_model"]]["mae"] <= cands["baseline_parity"]["mae"]
    assert b.version.startswith(m["chosen_model"]) and m["data_fingerprint"]
    p = save_bundle(b, tmp_path)
    b2 = load_bundle(p)
    X = usable_rows(build_features(synth)).tail(5)
    np.testing.assert_allclose(b.return_model.predict(X), b2.return_model.predict(X))
    assert m["backend"]  # records xgboost vs sklearn fallback
    # data fingerprint changes when data changes (reproducibility tracking)
    s2 = synth.copy(); s2.loc[10, "mcx_close"] *= 1.01
    assert train(s2.iloc[:200], THR, is_synthetic=True).meta["data_fingerprint"] != \
           train(synth.iloc[:200], THR, is_synthetic=True).meta["data_fingerprint"]
