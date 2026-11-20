"""Two estimates of the next Tamil Nadu 22K board, plus the range around the champion.

Beta model:  today's board x (1 + beta x international move since 09:00 IST).
Premium model: latest international rupee price x (1 + local premium).

beta is a rolling ridge regression with a prior of 1. The champion is whichever
model has the lower mean absolute error over the last scored window. Until both
models have a full window, the beta model is the champion.

The range is split-conformal: the finite-sample 80% quantile of past absolute
errors, widened when recent overnight moves have been larger than usual, and
never narrower than the configured minimum.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP


def round_rupees(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def units_from_1g(rate_1g: float) -> dict:
    """1 gram rounded to a rupee, then pavun (8 g), 10 g and 100 g from that rounded rate."""
    gram = round_rupees(rate_1g)
    return {"g1": gram, "pavun": gram * 8, "g10": gram * 10, "g100": gram * 100}


def ridge_beta(xs: list[float], ys: list[float], lam: float, prior: float = 1.0) -> float:
    """beta = (sum(x*y) + lam*prior) / (sum(x*x) + lam). Empty history returns the prior."""
    num = sum(x * y for x, y in zip(xs, ys)) + lam * prior
    den = sum(x * x for x in xs) + lam
    if den == 0:
        return prior
    return num / den


def fit_beta(pairs: list[tuple[float, float]], lam: float, prior: float, window: int) -> float:
    """``pairs`` are (board change, international change), oldest first."""
    windowed = [pair for pair in pairs[-window:] if pair[1] != 0]
    return ridge_beta([p[1] for p in windowed], [p[0] for p in windowed], lam, prior)


def beta_estimate(today_board: float, move: float, beta: float) -> float:
    return today_board * (1 + beta * move)


def ewm_mean(values: list[float], half_life: float) -> float:
    """Exponentially weighted mean. ``values`` are oldest first. Half the weight is ``half_life`` steps back."""
    if not values:
        return 0.0
    if half_life <= 0:
        return values[-1]
    total_w = 0.0
    total = 0.0
    last = len(values) - 1
    for i, value in enumerate(values):
        weight = 0.5 ** ((last - i) / half_life)
        total_w += weight
        total += weight * value
    return total / total_w


def fit_premium(premiums: list[float], half_life: float) -> float:
    return ewm_mean(premiums, half_life)


def premium_estimate(latest_intl: float, premium: float) -> float:
    return latest_intl * (1 + premium)


def choose_champion(errors_by_model: dict[str, list[float]], window: int, default: str = "beta") -> str:
    """Lowest MAE over the last ``window`` scored days. Beta until every model has a full window."""
    maes = {}
    for name, errors in errors_by_model.items():
        tail = [abs(e) for e in errors[-window:]]
        if len(tail) < window:
            return default
        maes[name] = sum(tail) / len(tail)
    if not maes:
        return default
    return min(maes, key=maes.get)


def conformal_quantile(abs_errors: list[float], coverage: float) -> float | None:
    """Finite-sample quantile. Rank is ceil((n+1)*coverage); above n, the largest error is used."""
    if not abs_errors:
        return None
    ordered = sorted(abs(e) for e in abs_errors)
    n = len(ordered)
    rank = math.ceil((n + 1) * coverage)
    if rank > n:
        return ordered[-1]
    return ordered[rank - 1]


def volatility_ratio(recent: list[float], baseline: list[float], low: float, high: float) -> float:
    """RMS of recent absolute overnight moves divided by the longer baseline, clipped."""
    def rms(values: list[float]) -> float | None:
        if not values:
            return None
        return math.sqrt(sum(v * v for v in values) / len(values))

    a, b = rms(recent), rms(baseline)
    if not a or not b:
        return 1.0
    return min(high, max(low, a / b))


def range_around(estimate: float, abs_errors: list[float], cfg_range: dict,
                 recent_moves: list[float] | None = None, baseline_moves: list[float] | None = None) -> tuple[float, float]:
    """Full interval. Width is at least ``min_width_inr`` and scales with recent volatility."""
    recent_n = cfg_range["volatility_recent_days"]
    base_n = cfg_range["volatility_baseline_days"]
    moves = [abs(m) for m in (baseline_moves or [])]
    ratio = volatility_ratio(moves[-recent_n:], moves[-base_n:],
                             cfg_range["volatility_ratio_min"], cfg_range["volatility_ratio_max"])
    if recent_moves is not None:
        ratio = volatility_ratio([abs(m) for m in recent_moves], [abs(m) for m in (baseline_moves or recent_moves)],
                                 cfg_range["volatility_ratio_min"], cfg_range["volatility_ratio_max"])
    q = conformal_quantile(abs_errors[-cfg_range["window_days"]:], cfg_range["coverage"])
    half = (q if q is not None else cfg_range["min_width_inr"] / 2) * ratio
    half = max(half, cfg_range["min_width_inr"] / 2)
    return estimate - half, estimate + half


def walk_forward(history: list[tuple], moves: dict, cfg: dict) -> list[dict]:
    """Predict each board day using only earlier board days.

    ``history`` is (date, rate) oldest first. ``moves[date]`` is the international
    move known before that board. A later board rate cannot change an earlier prediction.
    """
    out = []
    for i in range(1, len(history)):
        past = history[:i]
        target, _actual = history[i]
        pairs = []
        premiums = []
        for (d0, r0), (d1, r1) in zip(past, past[1:]):
            m = moves.get(d1)
            if m is None or r0 == 0:
                continue
            pairs.append((r1 / r0 - 1, m))
        beta = fit_beta(pairs, cfg["ridge"]["lambda"], cfg["ridge"]["prior"], cfg["ridge"]["window_days"])
        estimate = beta_estimate(past[-1][1], moves.get(target, 0.0), beta)
        out.append({"target": target, "estimate": estimate, "beta": beta, "using_days": len(past)})
    return out
