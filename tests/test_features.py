import numpy as np
import pandas as pd
import pytest

from goldpulse.ml.evaluation import chronological_split
from goldpulse.ml.features import FEATURE_COLUMNS, build_features, label_direction, live_feature_row, usable_rows
from goldpulse.providers.csv_history import DatasetError, validate_history


def test_no_future_information_in_features(synth):
    """Perturb everything that is unknown at 08:30 on row k (its closes + all later rows).
    Features for rows <= k must be unchanged."""
    k = 400
    base = build_features(synth)
    pert = synth.copy()
    for c in ["mcx_close", "comex_close", "xau_close", "usdinr_close"]:
        pert.loc[k:, c] = pert.loc[k:, c] * 1.37
    pert.loc[k + 1:, ["comex_morning", "xau_morning", "usdinr_morning"]] *= 1.21
    alt = build_features(pert)
    pd.testing.assert_frame_equal(base.loc[:k, FEATURE_COLUMNS + ["prev_mcx_close", "high_vol"]],
                                  alt.loc[:k, FEATURE_COLUMNS + ["prev_mcx_close", "high_vol"]])
    # sanity: the perturbation really did change something (the target of row k)
    assert not np.isclose(base.loc[k, "target_ret"], alt.loc[k, "target_ret"])


def test_target_is_next_session_return(synth):
    f = build_features(synth)
    i = 100
    assert f.loc[i, "target_ret"] == pytest.approx(synth.mcx_close[i] / synth.mcx_close[i - 1] - 1)
    assert f.loc[i, "prev_mcx_close"] == synth.mcx_close[i - 1]


def test_live_row_matches_historical_row(synth):
    i = 300
    hist = synth.iloc[:i].copy()
    live = live_feature_row(hist, synth.date[i], synth.comex_morning[i], synth.xau_morning[i],
                            synth.usdinr_morning[i])
    full = build_features(synth).iloc[[i]].reset_index(drop=True)
    pd.testing.assert_frame_equal(live[FEATURE_COLUMNS], full[FEATURE_COLUMNS])


def test_direction_labels_and_neutral_band():
    lab = label_direction([0.002, -0.002, 0.0005, -0.0009, 0.001], 0.001)
    assert list(lab) == ["UP", "DOWN", "NEUTRAL", "NEUTRAL", "NEUTRAL"]


def test_split_is_chronological_not_shuffled(synth):
    f = usable_rows(build_features(synth))
    tr, te = chronological_split(f, 0.2)
    assert tr["date"].max() < te["date"].min() and len(tr) + len(te) == len(f)
    with pytest.raises(ValueError):
        chronological_split(f.sample(frac=1, random_state=1), 0.2)


def test_dataset_validation(synth):
    ok = synth.copy()
    with pytest.raises(DatasetError):
        validate_history(ok)                                # SYNTHETIC refused by default
    assert len(validate_history(ok, allow_synthetic=True)) == len(ok)
    with pytest.raises(DatasetError):
        validate_history(ok.drop(columns=["comex_close"]), allow_synthetic=True)
    bad = ok.copy(); bad.loc[5, "mcx_close"] = np.nan
    with pytest.raises(DatasetError):
        validate_history(bad, allow_synthetic=True)
    bad = ok.copy(); bad.loc[5, "date"] = bad.loc[4, "date"]
    with pytest.raises(DatasetError):
        validate_history(bad, allow_synthetic=True)
