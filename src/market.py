"""International gold in rupees per gram, from COMEX GC=F and USDINR.

Price = USD per troy ounce / 31.1034768 x rupees per dollar. Hourly bars are
aligned on the IST hour. COMEX is shut on weekends and configured holidays, and
a forecast made then keeps a move of 0 rather than inventing one.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from src.storage import read_csv, write_csv

IST = ZoneInfo("Asia/Kolkata")
HOURLY_FIELDS = ["timestamp_utc", "inr_per_gram"]


def inr_per_gram(usd_per_oz: float, usdinr: float, troy_oz_grams: float) -> float:
    if usd_per_oz <= 0 or usdinr <= 0 or troy_oz_grams <= 0:
        raise ValueError("prices and the ounce weight must be positive")
    return usd_per_oz / troy_oz_grams * usdinr


def is_closed(when: datetime, cfg: dict) -> bool:
    local = when.astimezone(IST)
    holidays = set(cfg["weekend"].get("holidays") or [])
    if local.date().isoformat() in holidays:
        return True
    return local.weekday() in cfg["weekend"]["closed_weekdays"]


def effective_move(move: float, when: datetime, cfg: dict) -> float:
    """No new international move is applied while gold is closed."""
    if cfg["weekend"].get("treat_move_as_zero", True) and is_closed(when, cfg):
        return 0.0
    return move


def reference_time_on(day, cfg: dict) -> datetime:
    hh, mm = cfg["market"]["reference_time"].split(":")
    return datetime.combine(day, dtime(int(hh), int(mm)), IST)


def value_at(points: list[tuple[datetime, float]], when: datetime, max_gap_hours: float) -> float | None:
    """Last price at or before ``when``, if it is within ``max_gap_hours``. Oldest first."""
    best = None
    for ts, value in points:
        if ts > when:
            break
        best = (ts, value)
    if best is None or when - best[0] > timedelta(hours=max_gap_hours):
        return None
    return best[1]


def align_hourly(comex: list[tuple[datetime, float]], fx: list[tuple[datetime, float]],
                 troy_oz_grams: float) -> list[tuple[datetime, float]]:
    """Inner-join the two series on the IST hour and convert to INR per gram."""
    def by_hour(rows):
        out = {}
        for ts, value in rows:
            if value is None:
                continue
            hour = ts.astimezone(IST).replace(minute=0, second=0, microsecond=0)
            out[hour] = float(value)
        return out

    gold, rupee = by_hour(comex), by_hour(fx)
    hours = sorted(set(gold) & set(rupee))
    return [(hour, inr_per_gram(gold[hour], rupee[hour], troy_oz_grams)) for hour in hours]


def load_hourly(cfg: dict) -> list[tuple[datetime, float]]:
    rows = read_csv(Path_cache(cfg))
    out = []
    for row in rows:
        ts = datetime.fromisoformat(row["timestamp_utc"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=ZoneInfo("UTC"))
        out.append((ts.astimezone(IST), float(row["inr_per_gram"])))
    out.sort(key=lambda item: item[0])
    return out


def Path_cache(cfg: dict):
    from pathlib import Path
    return Path(cfg["_root"]) / cfg["market"]["cache_file"]


def save_hourly(cfg: dict, points: list[tuple[datetime, float]]) -> None:
    rows = [{"timestamp_utc": ts.astimezone(ZoneInfo("UTC")).isoformat(), "inr_per_gram": f"{value:.6f}"}
            for ts, value in points]
    write_csv(Path_cache(cfg), rows, HOURLY_FIELDS)


def _download(symbol: str, period: str):
    import yfinance as yf
    frame = yf.download(symbol, period=period, interval="1h", progress=False, auto_adjust=False)
    if frame is None or frame.empty:
        raise RuntimeError(f"yfinance returned no rows for {symbol}")
    close = frame["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    points = []
    for ts, value in close.items():
        if value != value:  # NaN
            continue
        when = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if when.tzinfo is None:
            when = when.replace(tzinfo=ZoneInfo("UTC"))
        points.append((when, float(value)))
    if not points:
        raise RuntimeError(f"yfinance close column was empty for {symbol}")
    return points


def fetch_hourly(cfg: dict, period: str | None = None) -> list[tuple[datetime, float]]:
    """Download both symbols with retries and cache the aligned INR-per-gram series."""
    period = period or cfg["market"]["history_period"]
    retries = cfg["sources"]["retries"]
    delay = cfg["sources"]["backoff_seconds"]
    last = None
    for attempt in range(retries):
        try:
            comex = _download(cfg["market"]["comex_symbol"], period)
            fx = _download(cfg["market"]["fx_symbol"], period)
            points = align_hourly(comex, fx, cfg["market"]["troy_oz_grams"])
            if not points:
                raise RuntimeError("no overlapping hourly bars")
            save_hourly(cfg, points)
            return points
        except Exception as exc:  # noqa: BLE001 - retry any vendor failure, then raise
            last = exc
            time.sleep(delay * (2 ** attempt))
    raise RuntimeError(f"international prices unavailable: {last}")


def ensure_hourly(cfg: dict) -> list[tuple[datetime, float]]:
    cached = load_hourly(cfg)
    if cached:
        return cached
    return fetch_hourly(cfg)


def level(points: list[tuple[datetime, float]], when: datetime, cfg: dict) -> float | None:
    return value_at(points, when, cfg["market"]["max_gap_hours"])


def move_between(points, start: datetime, end: datetime, cfg: dict, asof: datetime) -> float | None:
    """Fractional change from ``start`` to ``end``. Zero when ``asof`` is a closed session."""
    a, b = level(points, start, cfg), level(points, end, cfg)
    if a is None or b is None or a == 0:
        return None
    return effective_move(b / a - 1, asof, cfg)
