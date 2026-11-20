"""Write docs/latest.json and docs/history.json for the static page."""
from __future__ import annotations

import sys
from datetime import datetime, date

from src.config import load_config, repo_path
from src.forecast import board_history, champion_name, change_pairs, past_abs_errors
from src.market import IST, last_price, load_hourly, reference_time_on
from src.models import beta_estimate, premium_estimate, range_around, units_from_1g
from src.storage import read_csv, read_json, write_json


def _num(value):
    if value in ("", None):
        return None
    return float(value)


def _units(rate):
    if rate is None:
        return None
    return units_from_1g(rate)


def accuracy(forecasts: list[dict], champion: str, window: int) -> dict:
    scored = [row for row in forecasts if row.get("model") == champion and row.get("error") not in ("", None)]
    tail = scored[-window:]
    if not tail:
        return {"mae_30": None, "range_hit_rate": None, "direction_accuracy": None, "scored_days": 0}
    errors = [abs(float(row["error"])) for row in tail]
    hits = 0
    for row in tail:
        actual, lo, hi = _num(row["actual"]), _num(row["lo"]), _num(row["hi"])
        if None not in (actual, lo, hi) and lo <= actual <= hi:
            hits += 1
    return {
        "mae_30": round(sum(errors) / len(errors), 1),
        "range_hit_rate": round(hits / len(tail), 3),
        "direction_accuracy": _direction(tail),
        "scored_days": len(tail),
    }


def _direction(rows: list[dict]) -> float | None:
    """Share of days where the estimate and the board moved the same way versus the previous board."""
    boards = {}
    for row in rows:
        if row.get("actual") not in ("", None):
            boards[row["target_date"]] = float(row["actual"])
    ordered = sorted(boards)
    correct = 0
    total = 0
    by_target = {}
    for row in rows:
        by_target.setdefault(row["target_date"], row)
    for prev, cur in zip(ordered, ordered[1:]):
        row = by_target.get(cur)
        if row is None or row.get("estimate") in ("", None):
            continue
        est_move = float(row["estimate"]) - boards[prev]
        act_move = boards[cur] - boards[prev]
        same = (est_move == 0 and act_move == 0) or est_move * act_move > 0
        correct += int(same)
        total += 1
    if not total:
        return None
    return round(correct / total, 3)


def history_series(forecasts: list[dict], boards: list[tuple[date, float]], champion: str) -> list[dict]:
    estimate_for = {}
    for row in forecasts:
        if row.get("model") != champion or row.get("estimate") in ("", None):
            continue
        # Prefer the final estimate, which is the last one made before the board.
        if row["target_date"] not in estimate_for or row["kind"] == "final":
            estimate_for[row["target_date"]] = row
    series = []
    for day, rate in boards:
        row = estimate_for.get(day.isoformat())
        units = units_from_1g(rate)
        item = {"date": day.isoformat(), "board": units["g1"]}
        if row:
            item.update({"estimate": int(float(row["estimate"])),
                         "lo": None if _num(row["lo"]) is None else int(float(row["lo"])),
                         "hi": None if _num(row["hi"]) is None else int(float(row["hi"]))})
        series.append(item)
    return series


def calculation(cfg: dict, boards, params: dict, champion: str, points, asof: datetime | None) -> dict | None:
    """The champion estimate, spelled out so the page can show the arithmetic.

    None when there is no board or no international price at the reference.
    """
    if not boards or not points:
        return None
    board_day, board_rate = boards[-1]
    when = asof or datetime.now(IST)
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    ref_time = reference_time_on(board_day, cfg)
    ref = last_price(points, ref_time)
    end = last_price(points, when)
    if ref is None or end is None or ref[1] == 0:
        return None
    closed = end[0] <= ref[0]
    move = 0.0 if closed else end[1] / ref[1] - 1
    model = champion if champion in ("beta", "premium") else "beta"
    beta = float(params.get("beta", cfg["ridge"]["prior"]))
    premium = float(params.get("premium", 0.0))
    raw = premium_estimate(end[1], premium) if model == "premium" else beta_estimate(board_rate, move, beta)
    errors = past_abs_errors(cfg, model)
    window = cfg["range"]["window_days"]
    used = errors[-window:]
    overnight = [pair[1] for pair in change_pairs(boards, points, cfg)]
    lo, hi = range_around(raw, errors, cfg["range"],
                          recent_moves=overnight[-cfg["range"]["volatility_recent_days"]:],
                          baseline_moves=overnight)
    coverage = int(round(cfg["range"]["coverage"] * 100))
    return {
        "reference_time_ist": ref_time.isoformat(timespec="seconds"),
        "reference_intl_inr_per_g": round(ref[1], 4),
        "latest_intl_inr_per_g": round(end[1], 4),
        "latest_price_time_ist": end[0].astimezone(IST).isoformat(timespec="seconds"),
        "move_pct": round(move * 100, 4),
        "beta": None if model != "beta" else round(beta, 6),
        "premium": None if model != "premium" else round(premium, 6),
        "model": model,
        "board_today_1g": units_from_1g(board_rate)["g1"],
        "raw_estimate": round(raw, 4),
        "rounded_estimate": units_from_1g(raw)["g1"],
        "range_lo": units_from_1g(lo)["g1"],
        "range_hi": units_from_1g(hi)["g1"],
        "range_method": f"{coverage}% of recent misses, {len(used)} days",
        "market_closed": closed,
    }


def latest_payload(cfg: dict, points=None) -> tuple[dict, list]:
    forecasts = read_csv(repo_path(cfg, "forecasts"))
    boards = board_history(cfg)
    params = read_json(repo_path(cfg, "params"), {})
    champion = params.get("champion") or champion_name(cfg)
    today = None
    if boards:
        day, rate = boards[-1]
        units = units_from_1g(rate)
        source = ""
        for row in read_csv(repo_path(cfg, "board")):
            if row["date"] == day.isoformat():
                source = row.get("source", "")
        today = {"date": day.isoformat(), "source": source, **units}

    def pack(row):
        if row is None:
            return None
        units = units_from_1g(float(row["estimate"]))
        return {"date": row["target_date"], "kind": row["kind"], "model": row["model"],
                "made_at_ist": row["made_at_ist"], "estimate": units["g1"],
                "lo": int(float(row["lo"])) if row.get("lo") not in ("", None) else None,
                "hi": int(float(row["hi"])) if row.get("hi") not in ("", None) else None,
                **{k: units[k] for k in ("pavun", "g10", "g100")}}

    by_key = {(row["target_date"], row["kind"], row["model"]): row for row in forecasts}
    target = None
    evening = final = None
    if today:
        for row in forecasts:
            if row["model"] == champion and row["target_date"] > today["date"]:
                target = row["target_date"]
        if target:
            evening = pack(by_key.get((target, "evening", champion)))
            final = pack(by_key.get((target, "final", champion)))

    check = None
    if today:
        candidates = [row for row in forecasts
                      if row["target_date"] == today["date"] and row.get("actual") not in ("", None)
                      and row["model"] == champion]
        if candidates:
            chosen = next((row for row in candidates if row["kind"] == "evening"), candidates[0])
            check = {"date": chosen["target_date"], "kind": chosen["kind"], "model": chosen["model"],
                     "estimate": int(float(chosen["estimate"])), "actual": int(round(float(chosen["actual"]))),
                     "error": round(float(chosen["error"]), 1)}

    shown = final or evening
    asof = None
    if shown and shown.get("made_at_ist"):
        asof = datetime.fromisoformat(shown["made_at_ist"])
    if points is None:
        points = load_hourly(cfg)
    payload = {
        "updated_at_ist": datetime.now(IST).isoformat(timespec="seconds"),
        "champion": champion,
        "today": today,
        "tomorrow": {"evening": evening, "final": final},
        "yesterday_check": check,
        "accuracy": accuracy(forecasts, champion, cfg["range"]["window_days"]),
        "calc": calculation(cfg, boards, params, champion, points, asof),
    }
    return payload, history_series(forecasts, boards, champion)


def run(cfg: dict | None = None, points=None) -> dict:
    cfg = cfg or load_config()
    payload, series = latest_payload(cfg, points)
    write_json(repo_path(cfg, "latest"), payload)
    write_json(repo_path(cfg, "history"), series)
    print(f"wrote {repo_path(cfg, 'latest')} and {repo_path(cfg, 'history')}")
    return payload


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"site build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
