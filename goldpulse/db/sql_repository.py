"""SQLAlchemy implementation of the Repository interface (PostgreSQL).

NOT executed in the authoring environment (SQLAlchemy/psycopg2 were unavailable and a
database server wasn't reachable). Syntax-checked only; run `pytest tests/test_sql_repository.py`
against a real Postgres (see README) before relying on it.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from goldpulse.db import models as m
from goldpulse.history import build_history_frame
from goldpulse.providers.base import Instrument, Quality, Quote


def _jsonable(o):
    return json.loads(json.dumps(o, default=str))


class SqlRepository:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, pool_pre_ping=True)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    # -- helpers --
    def _source(self, s: Session, name: str, delayed=False, delay=0) -> int:
        row = s.scalar(select(m.MarketDataSource).where(m.MarketDataSource.name == name))
        if row is None:
            row = m.MarketDataSource(name=name, kind="rest_api", licensed=True, is_delayed=delayed, delay_minutes=delay)
            s.add(row); s.flush()
        return row.id

    # -- market data --
    def upsert_quote(self, q: Quote, slot=None, slot_date=None) -> bool:
        with self.Session.begin() as s:
            sid = self._source(s, q.provider, q.is_delayed, q.delay_minutes)
            stmt = pg_insert(m.MarketData).values(
                source_id=sid, instrument=q.instrument.value, market_id=q.market_id, contract_key=q.contract_key,
                contract_expiry=q.contract_expiry, price=q.price, open=q.open, high=q.high, low=q.low,
                prev_close=q.prev_close, currency=q.currency, observed_at_market=q.market_ts,
                observed_at_utc=q.ts_utc, observed_at_ist=q.ts_ist, slot=slot, slot_date=slot_date,
                is_delayed=q.is_delayed, delay_minutes=q.delay_minutes, quality_status=q.quality.value,
                quality_notes=q.quality_notes, raw=_jsonable(q.raw) if q.raw else None,
            ).on_conflict_do_nothing()          # any unique constraint -> idempotent no-op
            return s.execute(stmt).rowcount == 1

    def _to_quote(self, r: m.MarketData, provider: str) -> Quote:
        return Quote(Instrument(r.instrument), float(r.price), r.currency, provider, r.observed_at_market,
                     r.market_id, r.contract_expiry, is_delayed=r.is_delayed, delay_minutes=r.delay_minutes,
                     quality=Quality(r.quality_status), contract_code=r.contract_key)

    def latest_quote(self, inst, before_utc=None):
        with self.Session() as s:
            st = (select(m.MarketData, m.MarketDataSource.name).join(m.MarketDataSource)
                  .where(m.MarketData.instrument == inst.value))
            if before_utc: st = st.where(m.MarketData.observed_at_utc <= before_utc)
            row = s.execute(st.order_by(m.MarketData.observed_at_utc.desc()).limit(1)).first()
            return self._to_quote(row[0], row[1]) if row else None

    def quotes(self, inst, start, end, limit):
        with self.Session() as s:
            st = select(m.MarketData, m.MarketDataSource.name).join(m.MarketDataSource)
            if inst: st = st.where(m.MarketData.instrument == inst.value)
            if start: st = st.where(m.MarketData.observed_at_utc >= start)
            if end: st = st.where(m.MarketData.observed_at_utc <= end)
            rows = s.execute(st.order_by(m.MarketData.observed_at_utc.desc()).limit(limit)).all()
            return [{"instrument": r.instrument, "price": float(r.price), "currency": r.currency, "provider": n,
                     "as_of_utc": r.observed_at_utc.isoformat(), "as_of_ist": r.observed_at_ist.isoformat(),
                     "contract": r.contract_key, "is_delayed": r.is_delayed, "quality": r.quality_status}
                    for r, n in rows]

    def history_frame(self) -> pd.DataFrame:
        with self.Session() as s:
            snaps = [{"instrument": r.instrument, "slot": r.slot, "trading_date": r.slot_date, "price": float(r.price)}
                     for r in s.scalars(select(m.MarketData).where(m.MarketData.slot.is_not(None)))]
            acts = [{"trading_date": r.trading_date, "close": float(r.close_price)}
                    for r in s.scalars(select(m.ActualMarketPrice).where(m.ActualMarketPrice.instrument == "MCX_GOLD"))]
        return build_history_frame(snaps, acts)

    def save_actual(self, trading_date, instrument, contract_key, close, source) -> bool:
        with self.Session.begin() as s:
            sid = self._source(s, source)
            stmt = pg_insert(m.ActualMarketPrice).values(
                trading_date=trading_date, instrument=instrument, contract_key=contract_key,
                close_price=close, source_id=sid).on_conflict_do_nothing()
            return s.execute(stmt).rowcount == 1

    def actual_close(self, trading_date, instrument="MCX_GOLD"):
        with self.Session() as s:
            r = s.scalar(select(m.ActualMarketPrice.close_price).where(
                m.ActualMarketPrice.trading_date == trading_date, m.ActualMarketPrice.instrument == instrument)
                .order_by(m.ActualMarketPrice.id.desc()).limit(1))
            return float(r) if r is not None else None

    # -- models --
    def save_model_version(self, meta: dict) -> None:
        with self.Session.begin() as s:
            if s.scalar(select(m.ModelVersion.id).where(m.ModelVersion.version == meta["version"])): return
            s.add(m.ModelVersion(
                version=meta["version"], model_name=meta["chosen_model"], backend=meta.get("backend"),
                trained_through=date.fromisoformat(meta["trained_through"]) if meta.get("trained_through") else None,
                n_train_rows=meta["n_rows"], feature_list=meta["feature_columns"], params=_jsonable(meta.get("params")),
                data_fingerprint=meta.get("data_fingerprint"), calibrated=bool(meta.get("calibrated")),
                is_synthetic=bool(meta.get("synthetic")), limitations=meta.get("limitations"), is_active=True))

    def save_model_metrics(self, version, scope, metrics) -> None:
        with self.Session.begin() as s:
            mv = s.scalar(select(m.ModelVersion).where(m.ModelVersion.version == version))
            for k, v in metrics.items():
                if isinstance(v, (int, float)) and k != "n":
                    s.execute(pg_insert(m.ModelMetric).values(
                        model_version_id=mv.id, scope=scope, regime="high_vol" if k.startswith("high_vol") else "all",
                        name=k, value=float(v), n_obs=metrics.get("n"), window_end=date.today()).on_conflict_do_nothing())

    def model_metrics(self):
        with self.Session() as s:
            rows = s.execute(select(m.ModelMetric, m.ModelVersion.version).join(m.ModelVersion)).all()
            return [{"version": v, "scope": r.scope, "regime": r.regime, "name": r.name, "value": r.value,
                     "n_obs": r.n_obs} for r, v in rows]

    # -- predictions (append-only) --
    def save_prediction(self, pred: dict, force=False):
        with self.Session.begin() as s:
            mv = s.scalar(select(m.ModelVersion).where(m.ModelVersion.version == pred["model_version"]))
            if mv is None: raise RuntimeError(f"model version {pred['model_version']} not registered")
            runs = s.scalars(select(m.PredictionRun).where(
                m.PredictionRun.target_trading_date == pred["target_date"],
                m.PredictionRun.model_version_id == mv.id).order_by(m.PredictionRun.run_seq)).all()
            if runs and not force:
                return self._row_to_pred(runs[-1].prediction, mv.version, runs[-1].run_seq), False
            run = m.PredictionRun(target_trading_date=pred["target_date"], model_version_id=mv.id,
                                  run_seq=len(runs) + 1, trigger=pred.get("trigger", "api"),
                                  input_snapshot=_jsonable(pred["indicators"]), data_quality=_jsonable(pred["data_quality"]))
            s.add(run); s.flush()
            row = m.GoldPricePrediction(
                run_id=run.id, target_trading_date=pred["target_date"], previous_mcx_close=pred["previous_mcx_close"],
                expected_return_pct=pred["expected_return_pct"], predicted_mcx_price=pred["predicted_mcx_price"],
                interval_low=pred["interval_low"], interval_high=pred["interval_high"],
                interval_level=pred["interval_level"], interval_basis=pred["interval_basis"],
                direction=pred["direction"], direction_raw_scores=pred.get("direction_raw_scores"),
                confidence=pred.get("confidence"), retail_24k_per_g=pred["retail_24k_per_g"],
                retail_22k_per_g=pred["retail_22k_per_g"], assumptions=pred["pricing_assumptions"],
                payload=_jsonable(pred))
            s.add(row); s.flush()
            return self._row_to_pred(row, mv.version, run.run_seq), True

    @staticmethod
    def _row_to_pred(row: m.GoldPricePrediction, version: str, run_seq: int) -> dict:
        d = dict(row.payload)
        d.update(id=row.id, run_seq=run_seq, model_version=version, target_date=row.target_trading_date,
                 created_at_utc=row.created_at.astimezone(timezone.utc).isoformat() if row.created_at else None)
        return d

    def _preds(self, s, st):
        out = []
        for r in s.scalars(st):
            out.append(self._row_to_pred(r, r.payload["model_version"], r.run.run_seq))
        return out

    def latest_prediction(self):
        with self.Session() as s:
            r = self._preds(s, select(m.GoldPricePrediction).order_by(m.GoldPricePrediction.id.desc()).limit(1))
            return r[0] if r else None

    def predictions(self, limit):
        with self.Session() as s:
            return self._preds(s, select(m.GoldPricePrediction).order_by(m.GoldPricePrediction.id.desc()).limit(limit))

    def pending_predictions(self, up_to):
        with self.Session() as s:
            done = select(m.PredictionEvaluation.prediction_id)
            return self._preds(s, select(m.GoldPricePrediction).where(
                m.GoldPricePrediction.target_trading_date <= up_to, m.GoldPricePrediction.id.not_in(done)))

    def save_evaluation(self, ev: dict) -> bool:
        with self.Session.begin() as s:
            stmt = pg_insert(m.PredictionEvaluation).values(
                prediction_id=ev["prediction_id"], actual_close=ev["actual_close"], abs_error=ev["abs_error"],
                pct_error=ev["pct_error"], predicted_direction=ev["predicted_direction"],
                actual_direction=ev["actual_direction"], direction_correct=ev["direction_correct"],
                within_interval=ev["within_interval"], payload=_jsonable(ev)).on_conflict_do_nothing()
            return s.execute(stmt).rowcount == 1

    def evaluations(self):
        with self.Session() as s:
            return [dict(r.payload) for r in s.scalars(select(m.PredictionEvaluation))]

    def log(self, workflow, level, event, message, payload=None):
        with self.Session.begin() as s:
            s.add(m.AutomationLog(workflow=workflow, level=level, event=event, message=message,
                                  payload=_jsonable(payload or {})))
