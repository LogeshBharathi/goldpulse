"""SQLAlchemy 2.0 models. Predictions are append-only (no UPDATE paths exist in the code)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Identity, Index, Integer,
                        Numeric, String, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")
Price = Numeric(18, 6)


class Base(DeclarativeBase):
    pass


class MarketDataSource(Base):
    __tablename__ = "market_data_sources"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(30))                      # rest_api | csv | manual
    licensed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    license_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarketData(Base):
    __tablename__ = "market_data"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_data_sources.id"))
    instrument: Mapped[str] = mapped_column(String(20))
    market_id: Mapped[str] = mapped_column(String(20), default="")
    contract_key: Mapped[str] = mapped_column(String(30), default="SPOT")   # e.g. GOLD04DEC2026
    contract_expiry: Mapped[date | None] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Price)
    open: Mapped[float | None] = mapped_column(Price)
    high: Mapped[float | None] = mapped_column(Price)
    low: Mapped[float | None] = mapped_column(Price)
    prev_close: Mapped[float | None] = mapped_column(Price)
    currency: Mapped[str] = mapped_column(String(3))
    observed_at_market: Mapped[datetime] = mapped_column(DateTime(timezone=True))   # as reported (tz-aware)
    observed_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at_ist: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    slot: Mapped[str | None] = mapped_column(String(10))               # CLOSE | MORNING
    slot_date: Mapped[date | None] = mapped_column(Date)               # IST trading date of the slot
    is_delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0)
    quality_status: Mapped[str] = mapped_column(String(10))
    quality_notes: Mapped[list | None] = mapped_column(JSONType)
    raw: Mapped[dict | None] = mapped_column(JSONType)
    __table_args__ = (
        UniqueConstraint("source_id", "instrument", "contract_key", "observed_at_utc", name="uq_market_obs"),
        UniqueConstraint("instrument", "slot", "slot_date", name="uq_market_slot"),   # NULL slots never collide
        Index("ix_market_instr_time", "instrument", "observed_at_utc"),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(120), unique=True)
    model_name: Mapped[str] = mapped_column(String(40))
    backend: Mapped[str | None] = mapped_column(String(120))
    trained_through: Mapped[date | None] = mapped_column(Date)
    n_train_rows: Mapped[int] = mapped_column(Integer)
    feature_list: Mapped[list] = mapped_column(JSONType)
    params: Mapped[dict | None] = mapped_column(JSONType)
    data_fingerprint: Mapped[str | None] = mapped_column(String(64))
    artifact_path: Mapped[str | None] = mapped_column(Text)
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    limitations: Mapped[list | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelMetric(Base):
    __tablename__ = "model_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    scope: Mapped[str] = mapped_column(String(20))            # walk_forward | backtest | live
    regime: Mapped[str] = mapped_column(String(10), default="all")   # all | high_vol
    name: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Float)
    n_obs: Mapped[int | None] = mapped_column(Integer)
    window_end: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("model_version_id", "scope", "regime", "name", "window_end",
                                       name="uq_model_metric"),)


class PredictionRun(Base):
    __tablename__ = "prediction_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_trading_date: Mapped[date] = mapped_column(Date)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    run_seq: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[str] = mapped_column(String(20), default="api")
    status: Mapped[str] = mapped_column(String(12), default="OK")
    input_snapshot: Mapped[dict | None] = mapped_column(JSONType)
    data_quality: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    prediction: Mapped["GoldPricePrediction"] = relationship(back_populates="run", uselist=False)
    __table_args__ = (UniqueConstraint("target_trading_date", "model_version_id", "run_seq", name="uq_run"),)


class GoldPricePrediction(Base):
    __tablename__ = "gold_price_predictions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("prediction_runs.id"), unique=True)
    target_trading_date: Mapped[date] = mapped_column(Date, index=True)
    previous_mcx_close: Mapped[float] = mapped_column(Price)
    expected_return_pct: Mapped[float] = mapped_column(Float)
    predicted_mcx_price: Mapped[float] = mapped_column(Price)
    interval_low: Mapped[float] = mapped_column(Price)
    interval_high: Mapped[float] = mapped_column(Price)
    interval_level: Mapped[float] = mapped_column(Float)
    interval_basis: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(8))
    direction_raw_scores: Mapped[dict | None] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float)            # NULL unless calibrated
    retail_24k_per_g: Mapped[float] = mapped_column(Price)
    retail_22k_per_g: Mapped[float] = mapped_column(Price)
    assumptions: Mapped[dict] = mapped_column(JSONType)
    payload: Mapped[dict] = mapped_column(JSONType)                    # full prediction dict for audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run: Mapped[PredictionRun] = relationship(back_populates="prediction")


class ActualMarketPrice(Base):
    __tablename__ = "actual_market_prices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date)
    instrument: Mapped[str] = mapped_column(String(20))
    contract_key: Mapped[str] = mapped_column(String(30), default="SPOT")
    close_price: Mapped[float] = mapped_column(Price)
    source_id: Mapped[int] = mapped_column(ForeignKey("market_data_sources.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("trading_date", "instrument", "contract_key", "source_id",
                                       name="uq_actual"),)


class PredictionEvaluation(Base):
    __tablename__ = "prediction_evaluations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("gold_price_predictions.id"), unique=True)
    actual_price_id: Mapped[int | None] = mapped_column(ForeignKey("actual_market_prices.id"))
    actual_close: Mapped[float] = mapped_column(Price)
    abs_error: Mapped[float] = mapped_column(Float)
    pct_error: Mapped[float] = mapped_column(Float)
    predicted_direction: Mapped[str] = mapped_column(String(8))
    actual_direction: Mapped[str] = mapped_column(String(8))
    direction_correct: Mapped[bool] = mapped_column(Boolean)
    within_interval: Mapped[bool] = mapped_column(Boolean)
    payload: Mapped[dict] = mapped_column(JSONType)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AutomationLog(Base):
    __tablename__ = "automation_logs"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(10))
    event: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONType)
    execution_id: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
