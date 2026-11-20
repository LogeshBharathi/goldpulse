"""Estimate the next Tamil Nadu board with every model and record the champion.

``--kind evening`` is the 22:00 IST run and targets the next calendar day.
``--kind final`` is the 05:45 IST run and targets today's board. The same
(target date, kind, model) is overwritten, never duplicated, and an actual that
was already scored is kept.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, date

from src.config import load_config, repo_path
from src.market import IST, ensure_hourly, level, move_between, reference_time_on
from src.models import (beta_estimate, choose_champion, fit_beta, fit_premium,
                        premium_estimate, range_around, units_from_1g)
from src.storage import read_csv, read_json, upsert_csv, write_json

FORECAST_FIELDS = ["target_date", "kind", "made_at_ist", "model", "estimate", "lo", "hi", "actual", "error"]
MODELS = ("beta", "premium")


def target_date(now: datetime, kind: str) -> date:
    local = now.astimezone(IST)
    if kind == "final" and local.hour < 9:
        return local.date()
    return local.date() + timedelta(days=1)


def board_history(cfg: dict) -> list[tuple[date, float]]:
    rows = []
    for row in read_csv(repo_path(cfg, "board")):
        rows.append((date.fromisoformat(row["date"]), float(row["rate_22k_1g"])))
    rows.sort(key=lambda item: item[0])
    return rows


def change_pairs(history: list[tuple[date, float]], points, cfg: dict) -> list[tuple[float, float]]:
    """(board change, international change) between consecutive saved boards, using 09:00 IST."""
    pairs = []
    for (d0, r0), (d1, r1) in zip(history, history[1:]):
        if d1.weekday() == 6 and cfg["weekend"].get("sunday_repeats_saturday"):
            continue
        move = move_between(points, reference_time_on(d0, cfg), reference_time_on(d1, cfg), cfg,
                            reference_time_on(d1, cfg))
        if move is None or r0 == 0:
            continue
        pairs.append((r1 / r0 - 1, move))
    return pairs


def premiums_at_open(history, points, cfg) -> list[float]:
    out = []
    for day, rate in history:
        intl = level(points, reference_time_on(day, cfg), cfg)
        if intl:
            out.append(rate / intl - 1)
    return out


def past_abs_errors(cfg: dict, model: str) -> list[float]:
    errors = []
    for row in read_csv(repo_path(cfg, "forecasts")):
        if row.get("model") == model and row.get("error") not in ("", None):
            errors.append(abs(float(row["error"])))
    return errors


def champion_name(cfg: dict) -> str:
    """One error per board day (the evening forecast when both exist), oldest first."""
    picked: dict[tuple, float] = {}
    for row in read_csv(repo_path(cfg, "forecasts")):
        if row.get("error") in ("", None) or row.get("model") not in MODELS:
            continue
        key = (row["model"], row["target_date"])
        if key not in picked or row["kind"] == "evening":
            picked[key] = float(row["error"])
    by_model = {name: [] for name in MODELS}
    for (model, _day), error in sorted(picked.items(), key=lambda item: item[0][1]):
        by_model[model].append(error)
    return choose_champion(by_model, cfg["champion"]["window_days"], cfg["champion"]["default_model"])


def build(cfg: dict, now: datetime, kind: str, points) -> list[dict]:
    history = board_history(cfg)
    if not history:
        raise RuntimeError("no saved board rate to start from")
    board_day, board_rate = history[-1]
    target = target_date(now, kind)
    start = reference_time_on(board_day, cfg)
    move = move_between(points, start, now, cfg, now)
    if move is None:
        raise RuntimeError("international prices are missing for the move since 09:00 IST")
    latest = level(points, now, cfg)
    if latest is None:
        raise RuntimeError("no recent international price")

    beta = fit_beta(change_pairs(history, points, cfg), cfg["ridge"]["lambda"],
                    cfg["ridge"]["prior"], cfg["ridge"]["window_days"])
    premium = fit_premium(premiums_at_open(history, points, cfg), cfg["premium"]["half_life_days"])
    estimates = {
        "beta": beta_estimate(board_rate, move, beta),
        "premium": premium_estimate(latest, premium),
    }
    overnight = [pair[1] for pair in change_pairs(history, points, cfg)]
    made = now.astimezone(IST).isoformat(timespec="seconds")
    rows = []
    for model, estimate in estimates.items():
        lo, hi = range_around(estimate, past_abs_errors(cfg, model), cfg["range"],
                              recent_moves=overnight[-cfg["range"]["volatility_recent_days"]:],
                              baseline_moves=overnight)
        gram = units_from_1g(estimate)["g1"]
        rows.append({
            "target_date": target.isoformat(), "kind": kind, "made_at_ist": made, "model": model,
            "estimate": f"{gram}", "lo": f"{units_from_1g(lo)['g1']}", "hi": f"{units_from_1g(hi)['g1']}",
            "actual": "", "error": "",
        })
    params = read_json(repo_path(cfg, "params"), {})
    params.update({"beta": round(beta, 6), "premium": round(premium, 6), "champion": champion_name(cfg),
                   "last_move": round(move, 6), "updated_at_ist": made})
    write_json(repo_path(cfg, "params"), params)
    return rows


def run(kind: str, cfg: dict | None = None, now: datetime | None = None, points=None) -> list[dict]:
    cfg = cfg or load_config()
    now = now or datetime.now(IST)
    points = points if points is not None else ensure_hourly(cfg)
    rows = build(cfg, now, kind, points)
    upsert_csv(repo_path(cfg, "forecasts"), rows, key=["target_date", "kind", "model"], fieldnames=FORECAST_FIELDS)
    for row in rows:
        print(f"{row['target_date']} {row['kind']} {row['model']} {row['estimate']} [{row['lo']}, {row['hi']}]")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forecast the next Tamil Nadu board")
    parser.add_argument("--kind", required=True, choices=["evening", "final"])
    args = parser.parse_args(argv)
    try:
        run(args.kind)
    except Exception as exc:  # noqa: BLE001
        print(f"forecast failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
