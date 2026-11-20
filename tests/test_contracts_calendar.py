from datetime import date, timedelta

import pandas as pd
import pytest

from goldpulse.contracts import (assert_single_contract, build_continuous, contract_key,
                                 format_mcx_contract, parse_mcx_contract)
from goldpulse.trading_calendar import load_india_calendar, load_us_calendar


def test_parse_and_format_contract():
    assert parse_mcx_contract("GOLD04DEC2026") == ("GOLD", date(2026, 12, 4))
    assert parse_mcx_contract("04DEC2026")[1] == date(2026, 12, 4)
    assert format_mcx_contract("GOLD", date(2026, 12, 4)) == "GOLD04DEC2026"
    assert contract_key(None) == "SPOT"
    with pytest.raises(ValueError):
        parse_mcx_contract("GOLD99XXX2026")


def test_mixed_expiry_guard():
    with pytest.raises(ValueError):
        assert_single_contract([date(2026, 12, 4), date(2027, 2, 5)])
    assert assert_single_contract([date(2026, 12, 4)] * 3) == date(2026, 12, 4)


def _two_contracts():
    """Contract A expires 2026-02-05 (cheaper), B expires 2026-04-03 with a +3% premium.
    Both follow the same +0.5%/day trend, so a correct continuous series has constant 0.5% returns."""
    rows, d, n = [], date(2026, 1, 1), 0
    while d < date(2026, 2, 20):
        if d.weekday() < 5:
            base = 100000 * 1.005 ** n
            rows.append(dict(date=d, expiry=date(2026, 2, 5), close=base))
            rows.append(dict(date=d, expiry=date(2026, 4, 3), close=base * 1.03))
            n += 1
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def test_continuous_series_has_no_roll_jump():
    cont = build_continuous(_two_contracts(), roll_days_before_expiry=5)
    assert cont["roll_day"].sum() == 1
    rets = cont["close_adj"].pct_change().dropna()
    assert rets.max() - rets.min() < 1e-9          # constant 0.5% daily
    raw_rets = cont["close_raw"].pct_change().dropna()
    assert raw_rets.max() > 0.03                    # the naive series WOULD show the ~3% roll jump


def test_roll_without_both_quotes_raises():
    df = _two_contracts()
    roll_day = build_continuous(df)["date"][build_continuous(df)["roll_day"]].iloc[0]
    df = df[~((df["date"] == roll_day) & (df["expiry"] == date(2026, 2, 5)))]
    with pytest.raises(ValueError):
        build_continuous(df)


def test_india_calendar_weekends_and_holidays():
    cal = load_india_calendar("data/holidays")
    assert not cal.is_trading_day(date(2026, 11, 21))      # Saturday
    assert not cal.is_trading_day(date(2026, 1, 26))       # Republic Day (Monday)
    assert cal.is_trading_day(date(2026, 11, 19))          # Thursday
    assert cal.next_trading_day(date(2026, 11, 20)) == date(2026, 11, 23)  # Fri -> Mon
    assert cal.next_trading_day(date(2026, 12, 24)) == date(2026, 12, 28)  # skips Christmas + weekend
    assert cal.covers(date(2026, 5, 1)) and not cal.covers(date(2027, 1, 4))


def test_us_calendar():
    us = load_us_calendar("data/holidays")
    assert not us.is_trading_day(date(2026, 11, 26))       # Thanksgiving
    assert us.previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)
