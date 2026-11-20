"""Provider abstraction. The prediction engine only sees `Quote` objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Protocol

from goldpulse.config import IST, UTC


class Instrument(str, Enum):
    COMEX_GC = "COMEX_GC"     # COMEX gold futures (USD/oz)
    XAUUSD = "XAUUSD"         # spot gold (USD/oz)
    USDINR = "USDINR"         # FX
    MCX_GOLD = "MCX_GOLD"     # MCX gold futures (INR/10g)


class Quality(str, Enum):
    OK = "OK"
    DELAYED = "DELAYED"       # usable but not real-time
    STALE = "STALE"
    SUSPECT = "SUSPECT"       # failed a sanity check
    MISSING = "MISSING"


class ProviderError(RuntimeError):
    pass


class ProviderNotConfigured(ProviderError):
    pass


@dataclass
class Quote:
    instrument: Instrument
    price: float
    currency: str
    provider: str
    market_ts: datetime                  # timestamp as reported by the market (tz-aware)
    market_id: str = ""                  # e.g. "COMEX", "MCX", "FX"
    contract_expiry: date | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    is_delayed: bool = False
    delay_minutes: int = 0
    fetched_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    quality: Quality = Quality.OK
    quality_notes: list[str] = field(default_factory=list)
    raw: dict | None = None
    contract_code: str | None = None

    def __post_init__(self):
        if self.market_ts.tzinfo is None:
            raise ValueError("market_ts must be timezone-aware; refusing to guess a time zone")
        if not isinstance(self.instrument, Instrument):
            self.instrument = Instrument(self.instrument)

    @property
    def ts_utc(self) -> datetime:
        return self.market_ts.astimezone(UTC)

    @property
    def ts_ist(self) -> datetime:
        return self.market_ts.astimezone(IST)

    @property
    def contract_key(self) -> str:
        if self.contract_code:
            return self.contract_code
        from goldpulse.contracts import contract_key
        return contract_key(self.contract_expiry)


class MarketDataProvider(Protocol):
    name: str
    licensed: bool

    def supports(self, instrument: Instrument) -> bool: ...
    def fetch_latest(self, instrument: Instrument) -> Quote: ...
