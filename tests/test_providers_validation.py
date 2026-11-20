from datetime import datetime, timedelta, timezone

import pytest

from goldpulse.config import IST
from goldpulse.providers.base import (Instrument, ProviderError, ProviderNotConfigured, Quality, Quote)
from goldpulse.providers.rest import PRESET_TWELVEDATA, QuoteMapping, RestQuoteProvider
from goldpulse.validation import check_all, validate_quote

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)  # 08:30 IST


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}
    def json(self): return self._b


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def mkq(inst=Instrument.COMEX_GC, price=4000.0, age_h=1.0, **kw):
    return Quote(inst, price, "USD", "test", NOW - timedelta(hours=age_h), **kw)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        Quote(Instrument.XAUUSD, 1.0, "USD", "t", datetime(2026, 1, 1))


def test_timezone_normalisation():
    q = mkq()
    assert q.ts_utc.tzinfo is not None and q.ts_ist.utcoffset() == timedelta(hours=5, minutes=30)
    assert q.ts_ist.tzinfo == IST


def test_fresh_stale_missing_suspect_delayed():
    assert validate_quote(mkq(age_h=1), NOW, 3)[0] == Quality.OK
    assert validate_quote(mkq(age_h=5), NOW, 3)[0] == Quality.STALE
    assert validate_quote(None, NOW, 3)[0] == Quality.MISSING
    assert validate_quote(mkq(price=-1), NOW, 3)[0] == Quality.SUSPECT
    assert validate_quote(mkq(age_h=-2), NOW, 3)[0] == Quality.SUSPECT         # future timestamp
    assert validate_quote(mkq(price=5000), NOW, 3, previous_price=4000)[0] == Quality.SUSPECT
    s, notes = validate_quote(mkq(is_delayed=True, delay_minutes=10), NOW, 3)
    assert s == Quality.DELAYED and "delayed" in notes[0]


def test_check_all_reports_problem_per_instrument():
    limits = {i.value: 3 for i in Instrument}
    quotes = {i: mkq(i) for i in Instrument}
    assert check_all(quotes, NOW, limits)["ok"]
    quotes[Instrument.USDINR] = mkq(Instrument.USDINR, age_h=10)
    quotes[Instrument.MCX_GOLD] = None
    r = check_all(quotes, NOW, limits)
    assert not r["ok"] and len(r["problems"]) == 2
    assert r["per_instrument"]["MCX_GOLD"]["status"] == "MISSING"


def _provider(responses, **kw):
    return RestQuoteProvider("twelvedata", PRESET_TWELVEDATA, api_key="KEY", session=FakeSession(responses),
                             sleep=lambda s: None, **kw)


def test_rest_provider_parses_quote():
    body = {"close": "4012.5", "timestamp": 1_795_000_000, "previous_close": "4000", "open": "4001"}
    p = _provider([FakeResp(200, body)])
    q = p.fetch_latest(Instrument.XAUUSD)
    assert q.price == 4012.5 and q.prev_close == 4000.0 and q.market_ts.tzinfo is not None
    assert p.session.calls[0][1]["apikey"] == "KEY" and p.session.calls[0][2] == 10.0  # timeout used


def test_rest_provider_retries_on_5xx_then_succeeds():
    ok = FakeResp(200, {"close": "83.5", "timestamp": 1_795_000_000})
    p = _provider([FakeResp(503), RuntimeError("conn reset"), ok])
    assert p.fetch_latest(Instrument.USDINR).price == 83.5
    assert len(p.session.calls) == 3


def test_rest_provider_gives_up_and_does_not_retry_4xx():
    with pytest.raises(ProviderError):
        _provider([FakeResp(503)] * 3).fetch_latest(Instrument.XAUUSD)
    p = _provider([FakeResp(401)])
    with pytest.raises(ProviderError):
        p.fetch_latest(Instrument.XAUUSD)
    assert len(p.session.calls) == 1


def test_rest_provider_vendor_error_and_missing_timestamp():
    with pytest.raises(ProviderError):
        _provider([FakeResp(200, {"status": "error", "message": "limit"})]).fetch_latest(Instrument.XAUUSD)
    with pytest.raises(ProviderError):       # never substitutes fetch time for market time
        _provider([FakeResp(200, {"close": "1"})]).fetch_latest(Instrument.XAUUSD)


def test_unconfigured_cases():
    p = RestQuoteProvider("x", PRESET_TWELVEDATA, api_key="", session=FakeSession([]))
    with pytest.raises(ProviderNotConfigured):
        p.fetch_latest(Instrument.XAUUSD)                 # no key
    with pytest.raises(ProviderNotConfigured):
        _provider([]).fetch_latest(Instrument.COMEX_GC)   # no mapping: COMEX has no preset
    assert not _provider([]).supports(Instrument.MCX_GOLD)


def test_naive_vendor_datetime_uses_declared_tz():
    m = QuoteMapping(url="u", params={}, price="p", timestamp=None, datetime_str="t", source_tz="Asia/Kolkata")
    p = RestQuoteProvider("v", {Instrument.MCX_GOLD: m}, session=FakeSession(
        [FakeResp(200, {"p": "100000", "t": "2026-11-19 08:00:00"})]), sleep=lambda s: None)
    q = p.fetch_latest(Instrument.MCX_GOLD)
    assert q.ts_utc == datetime(2026, 11, 19, 2, 30, tzinfo=timezone.utc)
