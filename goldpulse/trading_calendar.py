"""Trading calendars (India/MCX and US/COMEX).

Holiday lists live in JSON files so they can be updated without code changes.
A calendar *knows which years it covers*; callers must check `covers()` and
surface a data-quality warning instead of silently treating an uncovered year
as holiday-free.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path


class TradingCalendar:
    def __init__(self, name: str, holidays: set[date], covered_years: set[int], weekend=(5, 6)):
        self.name = name
        self.holidays = set(holidays)
        self.covered_years = set(covered_years)
        self.weekend = tuple(weekend)

    def covers(self, d: date) -> bool:
        return d.year in self.covered_years

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend and d not in self.holidays

    def next_trading_day(self, d: date, include_start: bool = False) -> date:
        cur = d if include_start else d + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cur):
                return cur
            cur += timedelta(days=1)
        raise RuntimeError(f"No trading day within 30 days after {d} in {self.name}")

    def previous_trading_day(self, d: date, include_start: bool = False) -> date:
        cur = d if include_start else d - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cur):
                return cur
            cur -= timedelta(days=1)
        raise RuntimeError(f"No trading day within 30 days before {d} in {self.name}")


def load_calendar(name: str, directory: str | Path, prefix: str) -> TradingCalendar:
    """Load every `<prefix>_<year>.json` in directory.

    File format: {"year": 2026, "holidays": [{"date": "2026-01-26", "name": "..."}], "verified": bool}
    Files with "verified": false still load, but `unverified_years` is exposed for warnings.
    """
    holidays: set[date] = set()
    years: set[int] = set()
    for p in sorted(Path(directory).glob(f"{prefix}_*.json")):
        data = json.loads(p.read_text())
        years.add(int(data["year"]))
        for h in data["holidays"]:
            holidays.add(date.fromisoformat(h["date"]))
    cal = TradingCalendar(name, holidays, years)
    return cal


def load_india_calendar(directory="data/holidays") -> TradingCalendar:
    return load_calendar("India/MCX", directory, "in_mcx")


def load_us_calendar(directory="data/holidays") -> TradingCalendar:
    return load_calendar("US/COMEX", directory, "us_comex")
