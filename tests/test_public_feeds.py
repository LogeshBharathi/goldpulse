"""Parsers for the indicative public feeds. No network."""
from datetime import date, datetime, timezone

import pytest

from goldpulse.config import IST
from goldpulse.providers.base import ProviderError
from goldpulse.providers.public_feeds import parse_mcx_gold, parse_xaus_spot, parse_yahoo_last, xaus_daily_bars


def test_yahoo_last_uses_vendor_timestamp_and_prior_close():
    payload = {"chart": {"result": [{
        "meta": {"regularMarketPrice": 3715.2, "regularMarketTime": 1795096692,
                 "symbol": "GC=F", "shortName": "Gold Dec 26"},
        "indicators": {"quote": [{"close": [3741.9, 3715.2]}]},
    }]}}
    got = parse_yahoo_last(payload)
    assert got["price"] == 3715.2
    assert got["prev_close"] == 3741.9
    assert got["ts"] == datetime.fromtimestamp(1795096692, tz=timezone.utc)
    assert got["symbol"] == "GC=F"


def test_yahoo_empty_chart_is_an_error():
    with pytest.raises(ProviderError):
        parse_yahoo_last({"chart": {"result": None, "error": {"description": "No data found"}}})


def test_xaus_uses_inr_rate_and_timestamp():
    got = parse_xaus_spot({
        "spot_usd_oz": 3713.4,
        "fx_rate": 93.51,
        "updated_at": "2026-11-19T14:08:03.832Z",
        "data_state": {"status": "fresh"},
    })
    assert got["xau"] == 3713.4 and got["usdinr"] == 93.51
    assert got["ts"].tzinfo is not None
    assert got["state"] == "fresh"


def test_xaus_falls_back_to_fx_table_when_rate_is_usd():
    got = parse_xaus_spot({
        "spot_usd_oz": 3700.0, "fx_rate": 1, "fx_rates": {"INR": 93.2},
        "updated_at": "2026-11-19T14:08:03+00:00",
    })
    assert got["usdinr"] == 93.2


def test_mcx_gold_trade_time_is_ist_and_expiry_parses():
    got = parse_mcx_gold({"data": {"list": [
        {"cont_symbol": "GOLDM", "ltp": 1, "ltt": "2026-11-19 19:40:00", "cont_expiry": "04DEC2026"},
        {"cont_symbol": "GOLD", "ltp": 128639, "close": 129814,
         "ltt": "2026-11-19 19:40:00", "cont_expiry": "04DEC2026"},
    ]}})
    assert got["price"] == 128639 and got["prev_close"] == 129814
    assert got["ts"] == datetime(2026, 11, 19, 19, 40, tzinfo=IST)
    assert got["expiry"] == date(2026, 12, 4)


def test_xaus_daily_bars():
    bars = xaus_daily_bars({"points": [{"d": "2026-11-18", "c": 3740.0}, {"d": "2026-11-19", "c": 3725.6}]})
    assert bars[-1] == {"date": date(2026, 11, 19), "close": 3725.6}
