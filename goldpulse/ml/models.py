"""Models. XGBoost is used when installed; otherwise scikit-learn HistGradientBoosting
is used and the backend is recorded in the model metadata (never hidden)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

from goldpulse.ml.features import FEATURE_COLUMNS

try:  # pragma: no cover - depends on environment
    import xgboost as xgb
    HAVE_XGB = True
except Exception:  # pragma: no cover
    xgb = None
    HAVE_XGB = False

CLASSES = ["DOWN", "NEUTRAL", "UP"]


class ReturnModel:
    name = "base"
    backend = "numpy"
    def fit(self, X: pd.DataFrame, y: np.ndarray): return self
    def predict(self, X: pd.DataFrame) -> np.ndarray: raise NotImplementedError
    def params(self) -> dict: return {}


class ParityBaseline(ReturnModel):
    """No fitting: expected INR move = (1+COMEX overnight)(1+USDINR overnight)-1."""
    name = "baseline_parity"
    def predict(self, X): return X["parity_ret"].to_numpy(dtype=float)


class RidgeModel(ReturnModel):
    name = "linear_ridge"
    backend = "sklearn"
    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.pipe = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
    def fit(self, X, y):
        self.pipe.fit(X[FEATURE_COLUMNS], y); return self
    def predict(self, X): return self.pipe.predict(X[FEATURE_COLUMNS])
    def params(self): return {"alpha": self.alpha}


class AutoRegressive(ReturnModel):
    """AR(p) on MCX returns only (time-series baseline, no exogenous info)."""
    name = "ar3"
    backend = "sklearn"
    cols = ["mcx_ret_lag1", "mcx_ret_lag2", "mcx_ret_lag3"]
    def __init__(self, alpha: float = 1.0):
        self.m = Ridge(alpha=alpha)
    def fit(self, X, y):
        self.m.fit(X[self.cols], y); return self
    def predict(self, X): return self.m.predict(X[self.cols])
    def params(self): return {"p": 3}


class GBMRegressor(ReturnModel):
    name = "gbm_regressor"
    def __init__(self, n_estimators=200, max_depth=3, learning_rate=0.05, seed=42):
        self.hp = dict(n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate)
        if HAVE_XGB:
            self.backend = "xgboost"
            self.m = xgb.XGBRegressor(**self.hp, subsample=0.8, colsample_bytree=0.8,
                                      random_state=seed, n_jobs=1, verbosity=0)
        else:
            self.backend = "sklearn-HistGradientBoosting (xgboost not installed)"
            self.m = HistGradientBoostingRegressor(max_iter=n_estimators, max_depth=max_depth,
                                                   learning_rate=learning_rate, random_state=seed)
    def fit(self, X, y):
        self.m.fit(X[FEATURE_COLUMNS], y); return self
    def predict(self, X): return self.m.predict(X[FEATURE_COLUMNS])
    def params(self): return self.hp


class GBMDirection:
    """3-class UP/DOWN/NEUTRAL classifier with probabilities."""
    name = "gbm_direction"
    def __init__(self, n_estimators=150, max_depth=3, learning_rate=0.05, seed=42):
        self.hp = dict(n_estimators=n_estimators, max_depth=max_depth, learning_rate=learning_rate)
        self.seed = seed
        self.classes_: list[str] = []
        self.m = None
        self.backend = "xgboost" if HAVE_XGB else "sklearn-HistGradientBoosting (xgboost not installed)"

    def fit(self, X, y_labels: np.ndarray):
        self.classes_ = [c for c in CLASSES if c in set(y_labels)]
        if len(self.classes_) < 2:
            self.m = None
            return self
        idx = {c: i for i, c in enumerate(self.classes_)}
        y = np.array([idx[v] for v in y_labels])
        if HAVE_XGB:
            self.m = xgb.XGBClassifier(**self.hp, subsample=0.8, colsample_bytree=0.8,
                                       random_state=self.seed, n_jobs=1, verbosity=0)
        else:
            self.m = HistGradientBoostingClassifier(
                max_iter=self.hp["n_estimators"], max_depth=self.hp["max_depth"],
                learning_rate=self.hp["learning_rate"], random_state=self.seed)
        self.m.fit(X[FEATURE_COLUMNS], y)
        return self

    def predict_proba(self, X) -> pd.DataFrame:
        if self.m is None:
            return pd.DataFrame(np.nan, index=range(len(X)), columns=CLASSES)
        p = self.m.predict_proba(X[FEATURE_COLUMNS])
        out = pd.DataFrame(0.0, index=range(len(X)), columns=CLASSES)
        for j, c in enumerate(self.classes_):
            out[c] = p[:, j]
        return out

    def predict(self, X) -> np.ndarray:
        p = self.predict_proba(X)
        if p.isna().all().all():
            return np.array(["NEUTRAL"] * len(X))
        return p.idxmax(axis=1).to_numpy()

    def params(self): return self.hp


def make_return_models() -> dict[str, callable]:
    return {
        "baseline_parity": ParityBaseline,
        "linear_ridge": RidgeModel,
        "ar3": AutoRegressive,
        "gbm_regressor": GBMRegressor,
    }
