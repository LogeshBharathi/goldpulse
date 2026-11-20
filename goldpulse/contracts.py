"""Futures contract normalisation and rollover handling.

Never mix contracts with different expiries in one price series without
accounting for the roll: the price gap between contracts is not a market move.
`build_continuous` produces a ratio back-adjusted continuous series.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_RE = re.compile(r"^(?:(?P<sym>[A-Z]+))?(?P<d>\d{2})(?P<m>[A-Z]{3})(?P<y>\d{4})$")


def parse_mcx_contract(code: str) -> tuple[str, date]:
    """'GOLD04DEC2026' or '04DEC2026' -> ('GOLD', date(2026,12,4))."""
    m = _RE.match(code.strip().upper())
    if not m or m["m"] not in _MONTHS:
        raise ValueError(f"Unrecognised MCX contract code: {code!r}")
    return (m["sym"] or "GOLD"), date(int(m["y"]), _MONTHS[m["m"]], int(m["d"]))


def format_mcx_contract(symbol: str, expiry: date) -> str:
    mon = [k for k, v in _MONTHS.items() if v == expiry.month][0]
    return f"{symbol.upper()}{expiry.day:02d}{mon}{expiry.year}"


def contract_key(expiry: date | None, symbol: str = "GOLD") -> str:
    return "SPOT" if expiry is None else format_mcx_contract(symbol, expiry)


def build_continuous(long_df: pd.DataFrame, roll_days_before_expiry: int = 5) -> pd.DataFrame:
    """Build a back-adjusted continuous series from per-contract daily closes.

    long_df columns: date, expiry (date), close.  One row per (date, expiry).
    Front contract on date d = earliest expiry with (expiry - d).days > roll_days_before_expiry.
    On a roll date the cumulative adjustment factor f = P_new(d) / P_old(d) is applied to
    all earlier history, so the roll-day return equals the OLD contract's return.
    Raises if a roll date lacks quotes for both contracts (we refuse to guess).
    """
    df = long_df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    px = df.pivot_table(index="date", columns="expiry", values="close", aggfunc="last").sort_index()

    fronts: list[date] = []
    for d in px.index:
        cands = [e for e in px.columns if (e - d).days > roll_days_before_expiry and pd.notna(px.at[d, e])]
        if not cands:
            raise ValueError(f"No tradable front contract on {d}")
        fronts.append(min(cands))

    level = pd.Series([px.at[d, e] for d, e in zip(px.index, fronts)], index=px.index, dtype=float)
    adj = pd.Series(1.0, index=px.index)
    roll_flag = pd.Series(False, index=px.index)
    for i in range(1, len(px.index)):
        if fronts[i] != fronts[i - 1]:
            d = px.index[i]
            old, new = fronts[i - 1], fronts[i]
            p_old, p_new = px.at[d, old], px.at[d, new]
            if pd.isna(p_old) or pd.isna(p_new):
                raise ValueError(f"Roll on {d}: need both {old} and {new} quotes to compute adjustment")
            adj.iloc[:i] *= p_new / p_old
            roll_flag.iloc[i] = True
    out = pd.DataFrame({
        "date": px.index,
        "close_adj": (level * adj).values,
        "close_raw": level.values,
        "contract_expiry": fronts,
        "roll_day": roll_flag.values,
    })
    return out.reset_index(drop=True)


def assert_single_contract(expiries) -> date | None:
    """Guard used before computing returns on raw prices."""
    s = {e for e in expiries if e is not None}
    if len(s) > 1:
        raise ValueError(f"Mixed contract expiries without rollover handling: {sorted(s)}")
    return next(iter(s)) if s else None
