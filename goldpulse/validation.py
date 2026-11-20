"""Quote validation: freshness, sanity, timestamps. Never silently repairs data."""
from __future__ import annotations

from datetime import datetime, timedelta

from goldpulse.providers.base import Instrument, Quality, Quote

MAX_JUMP_PCT = 15.0  # a >15% move vs the previous quote is treated as suspect


def validate_quote(q: Quote | None, now_utc: datetime, max_age_hours: float,
                   previous_price: float | None = None, allow_future_s: int = 120) -> tuple[Quality, list[str]]:
    if q is None:
        return Quality.MISSING, ["no quote available"]
    notes: list[str] = []
    status = Quality.OK
    if not (q.price > 0):
        return Quality.SUSPECT, [f"non-positive price {q.price}"]
    age = now_utc - q.ts_utc
    if age < -timedelta(seconds=allow_future_s):
        return Quality.SUSPECT, [f"timestamp {q.ts_utc.isoformat()} is in the future"]
    if age > timedelta(hours=max_age_hours):
        return Quality.STALE, [f"age {age.total_seconds()/3600:.1f}h exceeds limit {max_age_hours}h"]
    if q.high is not None and q.low is not None and q.high < q.low:
        return Quality.SUSPECT, ["high < low"]
    if previous_price and abs(q.price / previous_price - 1) * 100 > MAX_JUMP_PCT:
        return Quality.SUSPECT, [f"jump >{MAX_JUMP_PCT}% vs previous price"]
    if q.is_delayed:
        status = Quality.DELAYED
        notes.append(f"delayed by ~{q.delay_minutes} min")
    return status, notes


def usable(status: Quality) -> bool:
    return status in (Quality.OK, Quality.DELAYED)


def check_all(quotes: dict[Instrument, Quote | None], now_utc: datetime, max_age_hours: dict,
              previous: dict[Instrument, float] | None = None) -> dict:
    """Validate every required instrument. Returns {'ok': bool, 'per_instrument': {...}, 'problems': [...]}"""
    previous = previous or {}
    per: dict[str, dict] = {}
    problems: list[str] = []
    for inst in Instrument:
        q = quotes.get(inst)
        status, notes = validate_quote(q, now_utc, max_age_hours[inst.value], previous.get(inst))
        q_ts = q.ts_utc.isoformat() if q else None
        per[inst.value] = {"status": status.value, "notes": notes, "as_of_utc": q_ts,
                           "provider": q.provider if q else None}
        if not usable(status):
            problems.append(f"{inst.value}: {status.value} {'; '.join(notes)}")
        if q is not None:
            q.quality, q.quality_notes = status, notes
    return {"ok": not problems, "per_instrument": per, "problems": problems}
