"""Fill in the actual board and the error, then refit beta and the premium.

An error larger than 4 times the rolling error standard deviation is recorded
but does not move the parameters. With too few scored days to measure that
standard deviation, the update goes ahead.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime

from src.config import load_config, repo_path
from src.forecast import FORECAST_FIELDS, change_pairs, premiums_at_open, champion_name, board_history
from src.market import IST, ensure_hourly
from src.models import fit_beta, fit_premium
from src.storage import read_csv, read_json, upsert_csv, write_json


def rolling_std(errors: list[float]) -> float | None:
    if len(errors) < 2:
        return None
    mean = sum(errors) / len(errors)
    return math.sqrt(sum((e - mean) ** 2 for e in errors) / (len(errors) - 1))


def score(forecasts: list[dict], boards: dict[str, float], multiple: float, min_samples: int) -> tuple[list[dict], bool]:
    """Return updated forecast rows and whether parameters may be refit.

    The standard deviation uses errors that were already on the file, so the
    row being scored is not part of its own threshold.
    """
    prior = [float(row["error"]) for row in forecasts if row.get("error") not in ("", None)]
    std = rolling_std(prior) if len(prior) >= min_samples else None
    learn = True
    updated = []
    for row in forecasts:
        actual = boards.get(row["target_date"])
        if actual is None or row.get("estimate") in ("", None):
            updated.append(row)
            continue
        error = actual - float(row["estimate"])
        already = row.get("error") not in ("", None)
        row = dict(row)
        row["actual"] = f"{actual:.2f}"
        row["error"] = f"{error:.2f}"
        if not already and std is not None and abs(error) > multiple * std:
            print(f"outlier {row['target_date']} {row['model']}: error {error:.0f} is more than "
                  f"{multiple}x the rolling std {std:.0f}; recorded, parameters not updated", file=sys.stderr)
            learn = False
        updated.append(row)
    return updated, learn


def refit(cfg: dict, points) -> dict:
    history = board_history(cfg)
    params = read_json(repo_path(cfg, "params"), {})
    params.update({
        "beta": round(fit_beta(change_pairs(history, points, cfg), cfg["ridge"]["lambda"],
                               cfg["ridge"]["prior"], cfg["ridge"]["window_days"]), 6),
        "premium": round(fit_premium(premiums_at_open(history, points, cfg), cfg["premium"]["half_life_days"]), 6),
        "champion": champion_name(cfg),
        "updated_at_ist": datetime.now(IST).isoformat(timespec="seconds"),
    })
    write_json(repo_path(cfg, "params"), params)
    return params


def run(cfg: dict | None = None, points=None) -> int:
    cfg = cfg or load_config()
    boards = {day.isoformat(): rate for day, rate in board_history(cfg)}
    forecasts = read_csv(repo_path(cfg, "forecasts"))
    updated, learn = score(forecasts, boards, cfg["scoring"]["outlier_std_multiple"],
                           cfg["scoring"]["min_std_samples"])
    upsert_csv(repo_path(cfg, "forecasts"), updated, key=["target_date", "kind", "model"],
               fieldnames=FORECAST_FIELDS)
    if not learn:
        print("parameters left unchanged")
        return 0
    points = points if points is not None else ensure_hourly(cfg)
    params = refit(cfg, points)
    print(f"refit beta={params['beta']} premium={params['premium']} champion={params['champion']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        return run()
    except Exception as exc:  # noqa: BLE001
        print(f"scoring failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
