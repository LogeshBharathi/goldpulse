"""Training pipeline, model selection, versioned artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from goldpulse.ml.evaluation import (compute_metrics, expected_calibration_error, interval_coverage,
                                     residual_interval, walk_forward)
from goldpulse.ml.features import FEATURE_COLUMNS, build_features, label_direction, usable_rows
from goldpulse.ml.models import GBMDirection, ParityBaseline, make_return_models

ECE_MAX_FOR_CONFIDENCE = 0.05
MIN_OOS_FOR_CONFIDENCE = 200


@dataclass
class TrainedBundle:
    version: str
    return_model: object
    direction_model: GBMDirection | None
    meta: dict = field(default_factory=dict)


def data_fingerprint(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()[:12]


def train(df: pd.DataFrame, neutral_threshold: float, min_train_rows: int = 250,
          interval_level: float = 0.8, step: int = 20, is_synthetic: bool = False) -> TrainedBundle:
    """Walk-forward-select the best return model; fall back to the parity baseline.

    A candidate must beat the baseline's out-of-sample MAE to be chosen. With fewer than
    `min_train_rows + 60` usable rows we use the baseline only and record the limitation.
    """
    f = usable_rows(build_features(df))
    factories = make_return_models()
    limitations: list[str] = []
    if is_synthetic:
        limitations.append("TRAINED ON SYNTHETIC DATA – pipeline test only, not market evidence.")

    min_oos = 60
    if len(f) < min_train_rows + min_oos:
        limitations.append(
            f"Insufficient history ({len(f)} usable rows; need >= {min_train_rows + min_oos}). "
            "Using parity baseline; no ML model trained, no calibrated interval/confidence.")
        chosen, bundle_dir = "baseline_parity", None
        wf_results = {}
    else:
        wf_results = {}
        for name, factory in factories.items():
            wf_results[name] = walk_forward(f, factory, min_train_rows, step, neutral_threshold)
        metrics_by_model = {n: compute_metrics(r) for n, r in wf_results.items()}
        base_mae = metrics_by_model["baseline_parity"]["mae"]
        eligible = {n: m for n, m in metrics_by_model.items()
                    if n == "baseline_parity" or m["mae"] < base_mae}
        chosen = min(eligible, key=lambda n: eligible[n]["mae"])
        if chosen == "baseline_parity":
            limitations.append("No ML/statistical model beat the parity baseline out-of-sample; baseline selected.")

    rm = factories[chosen]()
    rm.fit(f, f["target_ret"].to_numpy()) if len(f) else None

    meta: dict = {"limitations": limitations, "chosen_model": chosen, "n_rows": int(len(f)),
                  "neutral_threshold": neutral_threshold, "interval_level": interval_level}
    clf = None
    interval = None
    if wf_results:
        res = wf_results[chosen]
        meta["metrics_walk_forward"] = compute_metrics(res)
        meta["metrics_all_candidates"] = {n: compute_metrics(r) for n, r in wf_results.items()}
        iv = residual_interval(res, interval_level)
        if iv:
            iv["oos_coverage"] = interval_coverage(res, iv)
            interval = iv
        # Direction classifier: evaluated independently, with calibration check.
        res_dir = walk_forward(f, factories[chosen], min_train_rows, step, neutral_threshold, direction=True)
        meta["metrics_direction_classifier"] = compute_metrics(res_dir, dir_col="clf_dir")
        ece = expected_calibration_error(res_dir)
        meta["direction_ece"] = ece
        meta["calibrated"] = bool(ece is not None and ece <= ECE_MAX_FOR_CONFIDENCE
                                  and len(res_dir) >= MIN_OOS_FOR_CONFIDENCE)
        clf = GBMDirection().fit(f, label_direction(f["target_ret"], neutral_threshold))
        meta["direction_source"] = "classifier"
    else:
        meta["calibrated"] = False
        meta["direction_source"] = "threshold_on_regression"
    meta["interval"] = interval
    meta["backend"] = getattr(rm, "backend", "n/a")
    meta["params"] = rm.params()
    meta["feature_columns"] = FEATURE_COLUMNS
    meta["trained_through"] = str(f["date"].iloc[-1]) if len(f) else None
    meta["data_fingerprint"] = data_fingerprint(df)
    meta["synthetic"] = is_synthetic
    version = f"{chosen}-{meta['trained_through']}-{meta['data_fingerprint'][:6]}"
    meta["version"] = version
    meta["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    return TrainedBundle(version, rm, clf, meta)


def save_bundle(b: TrainedBundle, model_dir: str | Path) -> Path:
    p = Path(model_dir) / b.version
    p.mkdir(parents=True, exist_ok=True)
    joblib.dump({"return_model": b.return_model, "direction_model": b.direction_model}, p / "model.joblib")
    (p / "meta.json").write_text(json.dumps(b.meta, indent=2, default=str))
    return p


def load_bundle(path: str | Path) -> TrainedBundle:
    p = Path(path)
    meta = json.loads((p / "meta.json").read_text())
    obj = joblib.load(p / "model.joblib")
    return TrainedBundle(meta["version"], obj["return_model"], obj["direction_model"], meta)
