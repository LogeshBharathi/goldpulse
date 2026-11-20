"""Walk-forward forecasts for the saved board days, scored before any live run.

Each day is predicted from earlier boards only, and from international prices
at or before 05:45 IST on that day (the final forecast). Both models are
written as kind ``backtest`` with the actual and the error filled in, so the
page has an MAE, a range hit-rate and a direction score immediately.
"""
from __future__ import annotations

import sys
from datetime import datetime, time as dtime

from src.config import load_config, repo_path
from src.forecast import (FORECAST_FIELDS, board_history, change_pairs, premiums_at_open)
from src.market import IST, ensure_hourly, last_price, move_since_reference, reference_time_on
from src.models import beta_estimate, fit_beta, fit_premium, premium_estimate, range_around, units_from_1g
from src.score_and_learn import refit
from src.storage import read_csv, write_csv

ASOF = dtime(5, 45)
KIND = "backtest"


def backtest_rows(history, points, cfg: dict) -> list[dict]:
    """One scored row per model for every board day after the first."""
    prior = {"beta": [], "premium": []}
    rows = []
    for i in range(1, len(history)):
        past = history[:i]
        target, actual = history[i]
        asof = datetime.combine(target, ASOF, IST)
        visible = [(ts, value) for ts, value in points if ts <= asof]
        board_day, board_rate = past[-1]
        move = move_since_reference(visible, reference_time_on(board_day, cfg), asof)
        latest = last_price(visible, asof)
        if move is None or latest is None:
            continue
        beta = fit_beta(change_pairs(past, visible, cfg), cfg["ridge"]["lambda"],
                        cfg["ridge"]["prior"], cfg["ridge"]["window_days"])
        premium = fit_premium(premiums_at_open(past, visible, cfg), cfg["premium"]["half_life_days"])
        estimates = {"beta": beta_estimate(board_rate, move, beta),
                     "premium": premium_estimate(latest[1], premium)}
        overnight = [pair[1] for pair in change_pairs(past, visible, cfg)]
        made = asof.isoformat(timespec="seconds")
        for model, estimate in estimates.items():
            lo, hi = range_around(estimate, prior[model], cfg["range"],
                                  recent_moves=overnight[-cfg["range"]["volatility_recent_days"]:],
                                  baseline_moves=overnight)
            gram = units_from_1g(estimate)["g1"]
            error = actual - gram
            rows.append({
                "target_date": target.isoformat(), "kind": KIND, "made_at_ist": made, "model": model,
                "estimate": f"{gram}", "lo": f"{units_from_1g(lo)['g1']}", "hi": f"{units_from_1g(hi)['g1']}",
                "actual": f"{actual:.2f}", "error": f"{error:.2f}",
            })
            prior[model].append(abs(error))
    return rows


def polluted(row: dict) -> bool:
    """The midnight test rows written on 21 Nov 2026."""
    return (row.get("kind") in {"evening", "final"}
            and str(row.get("made_at_ist", "")).startswith("2026-11-21T00:01"))


def seed(cfg: dict | None = None, points=None) -> list[dict]:
    """Replace backtest rows, drop the midnight test rows, and refit parameters."""
    cfg = cfg or load_config()
    points = points if points is not None else ensure_hourly(cfg)
    history = board_history(cfg)
    fresh = backtest_rows(history, points, cfg)
    path = repo_path(cfg, "forecasts")
    kept = [row for row in read_csv(path) if row.get("kind") != KIND and not polluted(row)]
    write_csv(path, kept + fresh, FORECAST_FIELDS)
    refit(cfg, points)
    return fresh


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        rows = seed()
    except Exception as exc:  # noqa: BLE001
        print(f"backtest failed: {exc}", file=sys.stderr)
        return 1
    for model in ("beta", "premium"):
        errors = [abs(float(row["error"])) for row in rows if row["model"] == model]
        if not errors:
            continue
        print(f"{model} MAE {sum(errors) / len(errors):.1f} on {len(errors)} days")
    print(f"wrote {len(rows)} backtest rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
