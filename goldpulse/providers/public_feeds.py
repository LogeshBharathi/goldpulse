"""Indicative public quotes for a live run when no licensed vendor is configured.

Not an exchange license and not a substitute for one. Every quote is tagged
``feed=public`` and the forecast records that limitation.

* COMEX gold futures — Yahoo Finance chart ``GC=F`` (delayed).
* XAU/USD and USD/INR — xaus.com spot JSON (INR per USD from the same payload).
* MCX GOLD — Moneycontrol commodity futures list, front contract, last trade.
  MCX publishes GOLD in INR per 10 grams; the value is stored as published.
"""
from __future__ import annotations

import time
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import requests

from goldpulse.config import IST
from goldpulse.contracts import parse_mcx_contract
from goldpulse.providers.base import Instrument, ProviderError, Quality, Quote

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
XAUS_SPOT = "https://xaus.com/api/v1/spot"
XAUS_HISTORY = "https://xaus.com/api/v1/history"
MCX_FUTURES = "https://api.moneycontrol.com/mcapi/v1/commodity/futures"

XAUS_REUSE_SECONDS = 60

_HEADERS = {"User-Agent": "goldpulse/1.0 (local live test)", "Accept": "application/json"}


def parse_yahoo_last(payload: dict) -> dict:
    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        err = chart.get("error") or {}
        raise ProviderError(f"yahoo: {err.get('description') or 'empty chart'}")
    meta = result[0].get("meta") or {}
    if meta.get("regularMarketPrice") is None or meta.get("regularMarketTime") is None:
        raise ProviderError("yahoo: missing price or timestamp")
    closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    prev = None
    usable = [float(c) for c in closes if c is not None]
    if len(usable) >= 2:
        prev = usable[-2]
    return {
        "price": float(meta["regularMarketPrice"]),
        "ts": datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=timezone.utc),
        "prev_close": prev,
        "symbol": meta.get("symbol") or "GC=F",
        "name": meta.get("shortName") or meta.get("longName") or "",
    }


def parse_xaus_spot(payload: dict) -> dict:
    if payload.get("spot_usd_oz") is None or not payload.get("updated_at"):
        raise ProviderError("xaus: missing spot price or timestamp")
    ts = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
    if ts.tzinfo is None:
        raise ProviderError("xaus: timestamp has no timezone")
    fx = payload.get("fx_rate")
    rates = payload.get("fx_rates") or {}
    if fx is None or float(fx) < 2:
        if "INR" not in rates:
            raise ProviderError("xaus: missing USD/INR rate")
        fx = rates["INR"]
    state = (payload.get("data_state") or {}).get("status")
    return {"xau": float(payload["spot_usd_oz"]), "usdinr": float(fx), "ts": ts, "state": state}


def parse_mcx_gold(payload: dict) -> dict:
    rows = ((payload.get("data") or {}).get("list")) or []
    row = next((r for r in rows if r.get("cont_symbol") == "GOLD"), None)
    if row is None or row.get("ltp") is None or not row.get("ltt"):
        raise ProviderError("moneycontrol: GOLD futures quote missing")
    try:
        ts = datetime.strptime(row["ltt"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError as e:
        raise ProviderError(f"moneycontrol: unusable trade time {row.get('ltt')!r}") from e
    expiry = None
    code = row.get("cont_expiry") or ""
    if code:
        expiry = parse_mcx_contract(code)[1]
    prev = float(row["close"]) if row.get("close") not in (None, "") else None
    return {"price": float(row["ltp"]), "prev_close": prev, "ts": ts,
            "expiry": expiry, "expiry_code": code}


def yahoo_daily_bars(payload: dict) -> list[dict]:
    """Daily open/close bars. Date is the vendor bar timestamp's UTC date."""
    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        raise ProviderError("yahoo daily: empty chart")
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    opens, closes = quote.get("open") or [], quote.get("close") or []
    out = []
    for i, ts in enumerate(stamps):
        close = closes[i] if i < len(closes) else None
        open_ = opens[i] if i < len(opens) else None
        if close is None:
            continue
        when = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        out.append({"date": when.date(), "open": None if open_ is None else float(open_),
                    "close": float(close), "bar_ts": when})
    return out


def yahoo_intraday(payload: dict) -> list[tuple[datetime, float]]:
    """(UTC time, close) for every intraday bar that has a close, oldest first."""
    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        raise ProviderError("yahoo intraday: empty chart")
    block = result[0]
    closes = (((block.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    out = [(datetime.fromtimestamp(int(ts), tz=timezone.utc), float(c))
           for ts, c in zip(block.get("timestamp") or [], closes) if c is not None]
    if not out:
        raise ProviderError("yahoo intraday: no bars")
    return out


def value_at(points: list[tuple[datetime, float]], when: datetime,
             max_gap: timedelta = timedelta(minutes=30)) -> float | None:
    """Last close at or before ``when``, if one exists within ``max_gap``. ``points`` must be oldest first."""
    i = bisect_right(points, when, key=lambda p: p[0])
    if i == 0 or when - points[i - 1][0] > max_gap:
        return None
    return points[i - 1][1]


def xaus_daily_bars(payload: dict) -> list[dict]:
    points = payload.get("points") or []
    if not points:
        raise ProviderError("xaus history: no points")
    out = []
    for p in points:
        if p.get("c") is None or not p.get("d"):
            continue
        out.append({"date": datetime.fromisoformat(p["d"]).date(), "close": float(p["c"])})
    return out


class PublicFeedProvider:
    """Fallback for COMEX, XAU/USD, USD/INR and MCX GOLD. ``licensed`` is false."""

    name = "public-indicative"
    licensed = False

    def __init__(self, session: requests.Session | None = None, timeout: float = 20.0):
        self.timeout = timeout
        if session is None:
            session = requests.Session()
            session.headers.update(_HEADERS)
        self.session = session
        self._xaus: dict | None = None
        self._xaus_at = 0.0

    def supports(self, instrument: Instrument) -> bool:
        return instrument in (Instrument.COMEX_GC, Instrument.XAUUSD, Instrument.USDINR, Instrument.MCX_GOLD)

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        last = None
        for attempt in range(2):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code >= 400:
                    raise ProviderError(f"HTTP {r.status_code} from {url}")
                return r.json()
            except ProviderError as e:
                last = e
            except Exception as e:
                last = e
        raise ProviderError(f"{self.name}: request failed: {last}")

    def _xaus_payload(self) -> dict:
        # XAU/USD and USD/INR come from one payload; reuse it briefly, never for a whole server lifetime.
        now = time.monotonic()
        if self._xaus is None or now - self._xaus_at > XAUS_REUSE_SECONDS:
            self._xaus = self._get_json(XAUS_SPOT, {"currency": "INR"})
            self._xaus_at = now
        return self._xaus

    def intraday(self, symbol: str, range_: str = "5d") -> list[tuple[datetime, float]]:
        return yahoo_intraday(self._get_json(YAHOO_CHART.format(symbol=symbol),
                                             {"interval": "5m", "range": range_}))

    def fetch_latest(self, instrument: Instrument) -> Quote:
        if instrument == Instrument.COMEX_GC:
            payload = self._get_json(YAHOO_CHART.format(symbol="GC=F"), {"interval": "1d", "range": "5d"})
            parsed = parse_yahoo_last(payload)
            return Quote(
                instrument=instrument, price=parsed["price"], currency="USD", provider="public-yahoo",
                market_ts=parsed["ts"], market_id="COMEX", contract_code=str(parsed["symbol"]),
                prev_close=parsed["prev_close"], is_delayed=True, delay_minutes=15,
                quality=Quality.DELAYED,
                raw={"feed": "public", "symbol": parsed["symbol"], "name": parsed["name"],
                     "note": "Yahoo Finance GC=F delayed futures quote. Not a CME license."})
        if instrument in (Instrument.XAUUSD, Instrument.USDINR):
            parsed = parse_xaus_spot(self._xaus_payload())
            fresh = parsed["state"] == "fresh"
            if instrument == Instrument.XAUUSD:
                return Quote(
                    instrument=instrument, price=parsed["xau"], currency="USD", provider="public-xaus",
                    market_ts=parsed["ts"], market_id="SPOT", is_delayed=not fresh, delay_minutes=0 if fresh else 15,
                    quality=Quality.OK if fresh else Quality.DELAYED,
                    raw={"feed": "public", "state": parsed["state"],
                         "note": "xaus.com indicative XAU/USD spot. Not a tradable or LBMA benchmark quote."})
            return Quote(
                instrument=instrument, price=parsed["usdinr"], currency="INR", provider="public-xaus",
                market_ts=parsed["ts"], market_id="FX", is_delayed=not fresh, delay_minutes=0 if fresh else 15,
                quality=Quality.OK if fresh else Quality.DELAYED,
                raw={"feed": "public", "state": parsed["state"],
                     "note": "USD/INR taken from the same xaus.com spot payload as XAU. Not an FBIL reference rate."})
        if instrument == Instrument.MCX_GOLD:
            payload = self._get_json(MCX_FUTURES, {"exchange": "mcx"})
            parsed = parse_mcx_gold(payload)
            return Quote(
                instrument=instrument, price=parsed["price"], currency="INR", provider="public-moneycontrol",
                market_ts=parsed["ts"], market_id="MCX", contract_expiry=parsed["expiry"],
                prev_close=parsed["prev_close"], is_delayed=True, delay_minutes=15,
                quality=Quality.DELAYED,
                raw={"feed": "public", "expiry": parsed["expiry_code"],
                     "note": "Moneycontrol MCX GOLD futures last trade. MCX quotes GOLD in INR per 10g. "
                             "Trade time is IST (the feed sends no offset). Not an MCX data license."})
        raise ProviderError(f"{self.name} does not support {instrument}")
