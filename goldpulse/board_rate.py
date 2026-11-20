"""Tamil Nadu jeweller board rate: 22K, 1 gram, before GST.

History comes from BankBazaar's Chennai page and from rates the user types in.
A typed rate wins over the fetched one for the same day.

Tomorrow's board is estimated from today's board and the move in international
gold in rupees (COMEX x USD/INR) since the reference time, scaled by how much of
that move the board has actually passed on in the saved history.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, time
from pathlib import Path

STATE = "Tamil Nadu"
PURITY_22K, PURITY_24K = 0.916, 0.999
PAVUN_GRAMS = 8
# Day-to-day board changes in Nov 2026 matched international prices at 9:00 IST more closely than
# any other hour from 3:00 to 10:00.
BOARD_SET_AT = time(9, 0)
MIN_FIT_DAYS = 6
PASS_THROUGH_LIMITS = (0.5, 1.2)
BAND_Z = 1.28  # 80% of outcomes, if errors are roughly normal
USER = "you"
# A typed rate further than this from the exchange-implied 22K rate is almost always a typo or the wrong unit.
MAX_GAP_PCT = 15.0

UNITS = {
    "gram": (1, "1 gram"),
    "pavun": (PAVUN_GRAMS, "1 pavun (8 g)"),
    "10g": (10, "10 grams"),
    "100g": (100, "100 grams"),
}


def default_path() -> Path:
    return Path(os.environ.get("BOARD_RATE_FILE", "data/board_rates.json"))


def load_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    for r in rows:
        r.setdefault("source", USER)
    return sorted(rows, key=lambda r: r["board_date"])


def _write(path: Path, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r["board_date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=2))
    tmp.replace(path)


def save_manual(path: Path, *, board_date: date, g22: float, saved_at: datetime) -> dict:
    rows = [r for r in load_all(path) if r["board_date"] != board_date.isoformat()]
    entry = {"board_date": board_date.isoformat(), "g22": round(float(g22), 2), "source": USER,
             "saved_at": saved_at.isoformat(timespec="seconds")}
    _write(path, rows + [entry])
    return entry


def merge_fetched(path: Path, fetched: list[tuple[date, float]], source: str, saved_at: datetime) -> int:
    """Add or update fetched days. Days the user typed in are left alone. Returns rows changed."""
    by_day = {r["board_date"]: r for r in load_all(path)}
    changed = 0
    for day, g22 in fetched:
        key = day.isoformat()
        old = by_day.get(key)
        if old and (old["source"] == USER or old["g22"] == round(g22, 2)):
            continue
        by_day[key] = {"board_date": key, "g22": round(g22, 2), "source": source,
                       "saved_at": saved_at.isoformat(timespec="seconds")}
        changed += 1
    if changed:
        _write(path, list(by_day.values()))
    return changed


def latest(rows: list[dict], on_or_before: date) -> dict | None:
    eligible = [r for r in rows if r["board_date"] <= on_or_before.isoformat()]
    return eligible[-1] if eligible else None


def previous(rows: list[dict], before: date) -> dict | None:
    eligible = [r for r in rows if r["board_date"] < before.isoformat()]
    return eligible[-1] if eligible else None


def extremes(rows: list[dict]) -> tuple[dict, dict] | None:
    if not rows:
        return None
    return max(rows, key=lambda r: r["g22"]), min(rows, key=lambda r: r["g22"])


def parse_rupees(text: str) -> float:
    cleaned = (text or "").replace("₹", "").replace(",", "").replace("Rs", "").replace("rs", "").strip()
    if not cleaned:
        raise ValueError("Type the 22 karat rate for 1 gram, for example 11980.")
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError("Use numbers only, for example 11980.") from None
    if value <= 0:
        raise ValueError("The rate must be more than zero.")
    return value


def exchange_g22(mcx_per_10g: float) -> float:
    """MCX 999 gold per 10 g converted to 22K per gram, before GST."""
    return mcx_per_10g / 10 * PURITY_22K / PURITY_24K


def check_rate(g22: float, mcx_per_10g: float) -> None:
    ref = exchange_g22(mcx_per_10g)
    if abs(g22 / PAVUN_GRAMS / ref - 1) * 100 <= MAX_GAP_PCT:
        raise ValueError(f"That looks like the price of 1 pavun (8 grams). Enter the 1 gram rate, "
                         f"about {round(g22 / PAVUN_GRAMS):,}.")
    if abs(g22 / 10 / ref - 1) * 100 <= MAX_GAP_PCT:
        raise ValueError(f"That looks like the price of 10 grams. Enter the 1 gram rate, about {round(g22 / 10):,}.")
    gap = (g22 / ref - 1) * 100
    if abs(gap) > MAX_GAP_PCT:
        raise ValueError(f"₹{g22:,.0f} is {gap:+.0f}% away from the exchange right now (about ₹{ref:,.0f}). "
                         "Check the number and that it is 22 karat for 1 gram.")


def international_move(comex_then: float, fx_then: float, comex_now: float, fx_now: float) -> float:
    """Fractional change in the rupee price of international gold."""
    return (comex_now * fx_now) / (comex_then * fx_then) - 1


def expected_g22(board_g22: float, move: float, pass_through: float = 1.0) -> float:
    return board_g22 * (1 + pass_through * move)


def fit_pass_through(pairs: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares slope through the origin of board change on international change.

    ``pairs`` are (board change, international change) as fractions for consecutive board days.
    Returns (pass-through, residual standard deviation), or None with too little history.
    """
    usable = [(b, i) for b, i in pairs if i != 0]
    if len(usable) < MIN_FIT_DAYS:
        return None
    slope = sum(b * i for b, i in usable) / sum(i * i for _, i in usable)
    lo, hi = PASS_THROUGH_LIMITS
    slope = min(max(slope, lo), hi)
    resid = [b - slope * i for b, i in usable]
    sd = math.sqrt(sum(r * r for r in resid) / (len(resid) - 1))
    return slope, sd


def error_band(expected: float, past_errors: list[float]) -> tuple[float, float] | None:
    """Range from how far the same method missed on past days, made the same number of hours before the board.

    ``past_errors`` are actual / estimate - 1. Overnight market moves dominate these misses, so this is
    wider than a band built from board noise alone.
    """
    if len(past_errors) < MIN_FIT_DAYS:
        return None
    rms = math.sqrt(sum(e * e for e in past_errors) / len(past_errors))
    return expected * (1 - BAND_Z * rms), expected * (1 + BAND_Z * rms)


def estimates_path() -> Path:
    return Path(os.environ.get("ESTIMATE_FILE", "data/board_estimates.json"))


def load_estimates(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text()) if path.exists() else {}


def record_estimate(path: Path, *, target: date, g22: float, low: float | None, high: float | None,
                    made_at: datetime, cutoff: datetime) -> None:
    """Keep the latest estimate for ``target`` made before ``cutoff``; later ones would already know the answer."""
    if made_at >= cutoff:
        return
    data = load_estimates(path)
    data[target.isoformat()] = {"g22": round(g22, 2),
                                "low": None if low is None else round(low, 2),
                                "high": None if high is None else round(high, 2),
                                "made_at": made_at.isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def per_unit(g22_per_gram: float, grams: int) -> float:
    """Boards print the 1 gram rate in whole rupees and multiply up, so round first."""
    return round(g22_per_gram) * grams


def g24_from_g22(g22: float) -> float:
    return g22 * PURITY_24K / PURITY_22K
