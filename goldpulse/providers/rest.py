"""Config-driven REST quote provider with timeouts and retries.

Point it at a *licensed* vendor API by supplying a `QuoteMapping`. A preset for
Twelve Data (XAU/USD, USD/INR) is included; COMEX and MCX have no preset because
the right vendor depends on the licence you hold – supply your own mapping.

NOTE: field names in PRESET_TWELVEDATA follow the vendor's public docs at time of
writing and have NOT been exercised against the live API in this repository's
test-suite (tests use a fake HTTP session). Verify before relying on it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from goldpulse.providers.base import (Instrument, ProviderError, ProviderNotConfigured, Quote)


def _dig(obj, path: str):
    """Tiny dotted-path getter: 'data.0.price'."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        cur = cur[int(part)] if isinstance(cur, list) else cur.get(part)
    return cur


@dataclass
class QuoteMapping:
    url: str
    params: dict = field(default_factory=dict)       # may contain {symbol}
    symbol: str = ""
    price: str = "close"
    timestamp: str | None = None                      # epoch seconds path, preferred
    datetime_str: str | None = None                   # fallback ISO-ish string path
    source_tz: str = "UTC"                            # tz of datetime_str if it has no offset
    open: str | None = "open"
    high: str | None = "high"
    low: str | None = "low"
    prev_close: str | None = "previous_close"
    currency: str = "USD"
    market_id: str = ""
    is_delayed: bool = False
    delay_minutes: int = 0
    error_path: str | None = "status"                 # value 'error' => failure
    contract_expiry: date | None = None


PRESET_TWELVEDATA = {
    Instrument.XAUUSD: QuoteMapping(
        url="https://api.twelvedata.com/quote", params={"symbol": "{symbol}", "apikey": "{api_key}"},
        symbol="XAU/USD", timestamp="timestamp", datetime_str="datetime", currency="USD", market_id="SPOT"),
    Instrument.USDINR: QuoteMapping(
        url="https://api.twelvedata.com/quote", params={"symbol": "{symbol}", "apikey": "{api_key}"},
        symbol="USD/INR", timestamp="timestamp", datetime_str="datetime", currency="INR", market_id="FX"),
}


class RestQuoteProvider:
    licensed = True  # operator asserts this by configuring it; see docs/DATA_SOURCES.md

    def __init__(self, name: str, mappings: dict[Instrument, QuoteMapping], api_key: str = "",
                 session=None, timeout: float = 10.0, retries: int = 3, backoff: float = 1.0,
                 sleep=time.sleep):
        self.name = name
        self.mappings = mappings
        self.api_key = api_key
        self.timeout, self.retries, self.backoff, self._sleep = timeout, retries, backoff, sleep
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def supports(self, instrument: Instrument) -> bool:
        return instrument in self.mappings

    def _get_json(self, url: str, params: dict) -> dict:
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise ProviderError(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise ProviderError(f"HTTP {r.status_code} (not retried)")
                return r.json()
            except ProviderError as e:
                last = e
                if "not retried" in str(e):
                    break
            except Exception as e:  # network / JSON errors
                last = e
            if attempt < self.retries - 1:
                self._sleep(self.backoff * (2 ** attempt))
        raise ProviderError(f"{self.name}: request failed after {self.retries} attempts: {last}")

    def fetch_latest(self, instrument: Instrument) -> Quote:
        m = self.mappings.get(instrument)
        if m is None:
            raise ProviderNotConfigured(f"{self.name} has no mapping for {instrument.value}")
        if "{api_key}" in "".join(map(str, m.params.values())) and not self.api_key:
            raise ProviderNotConfigured(f"{self.name}: API key not configured")
        params = {k: str(v).format(symbol=m.symbol, api_key=self.api_key) for k, v in m.params.items()}
        data = self._get_json(m.url, params)
        if m.error_path and _dig(data, m.error_path) == "error":
            raise ProviderError(f"{self.name}: vendor reported error: {str(data)[:200]}")
        raw_price = _dig(data, m.price)
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            raise ProviderError(f"{self.name}: unusable price field {raw_price!r}")
        ts = self._parse_ts(data, m)

        def num(path):
            if not path:
                return None
            v = _dig(data, path)
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return Quote(instrument=instrument, price=price, currency=m.currency, provider=self.name,
                     market_ts=ts, market_id=m.market_id, contract_expiry=m.contract_expiry,
                     open=num(m.open), high=num(m.high), low=num(m.low), prev_close=num(m.prev_close),
                     is_delayed=m.is_delayed, delay_minutes=m.delay_minutes, raw=data)

    @staticmethod
    def _parse_ts(data: dict, m: QuoteMapping) -> datetime:
        if m.timestamp:
            v = _dig(data, m.timestamp)
            if v not in (None, ""):
                return datetime.fromtimestamp(float(v), tz=timezone.utc)
        if m.datetime_str:
            s = _dig(data, m.datetime_str)
            if s:
                dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(m.source_tz))
        raise ProviderError("No usable timestamp in vendor response (refusing to use fetch time)")
