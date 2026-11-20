"""Repository interface + in-memory implementation (used by tests and local dry-runs).

The SQL implementation lives in goldpulse/db/sql_repository.py. Predictions are
append-only: nothing here overwrites a stored prediction.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from typing import Protocol

import pandas as pd

from goldpulse.history import build_history_frame
from goldpulse.providers.base import Instrument, Quote


class Repository(Protocol):
    def upsert_quote(self, q: Quote, slot: str | None = None, slot_date: date | None = None) -> bool: ...
    def latest_quote(self, inst: Instrument, before_utc: datetime | None = None) -> Quote | None: ...
    def quotes(self, inst: Instrument | None, start: datetime | None, end: datetime | None, limit: int) -> list[dict]: ...
    def history_frame(self) -> pd.DataFrame: ...
    def save_actual(self, trading_date: date, instrument: str, contract_key: str, close: float,
                    source: str) -> bool: ...
    def save_prediction(self, pred: dict, force: bool = False) -> tuple[dict, bool]: ...
    def latest_prediction(self) -> dict | None: ...
    def predictions(self, limit: int) -> list[dict]: ...
    def pending_predictions(self, up_to: date) -> list[dict]: ...
    def actual_close(self, trading_date: date, instrument: str = "MCX_GOLD") -> float | None: ...
    def save_evaluation(self, ev: dict) -> bool: ...
    def evaluations(self) -> list[dict]: ...
    def save_model_version(self, meta: dict) -> None: ...
    def save_model_metrics(self, version: str, scope: str, metrics: dict) -> None: ...
    def model_metrics(self) -> list[dict]: ...
    def log(self, workflow: str, level: str, event: str, message: str, payload: dict | None = None) -> None: ...


class InMemoryRepository:
    def __init__(self):
        self._quotes: list[dict] = []
        self._actuals: dict[tuple, dict] = {}
        self._preds: list[dict] = []
        self._evals: dict[int, dict] = {}
        self._models: dict[str, dict] = {}
        self._metrics: list[dict] = []
        self.logs: list[dict] = []

    # --- market data ---
    def upsert_quote(self, q: Quote, slot=None, slot_date=None) -> bool:
        key = (q.provider, q.instrument.value, q.contract_key, q.ts_utc)
        for r in self._quotes:
            if r["key"] == key or (slot and (r["instrument"], r["slot"], r["slot_date"]) ==
                                   (q.instrument.value, slot, slot_date)):
                return False                       # duplicate: idempotent no-op
        self._quotes.append({"key": key, "quote": q, "instrument": q.instrument.value, "slot": slot,
                             "slot_date": slot_date, "price": q.price})
        return True

    def latest_quote(self, inst, before_utc=None):
        rows = [r["quote"] for r in self._quotes if r["instrument"] == inst.value
                and (before_utc is None or r["quote"].ts_utc <= before_utc)]
        return max(rows, key=lambda q: q.ts_utc) if rows else None

    def quotes(self, inst, start, end, limit):
        out = []
        for r in sorted(self._quotes, key=lambda r: r["quote"].ts_utc, reverse=True):
            q = r["quote"]
            if inst and q.instrument != inst: continue
            if start and q.ts_utc < start: continue
            if end and q.ts_utc > end: continue
            out.append({"instrument": q.instrument.value, "price": q.price, "currency": q.currency,
                        "provider": q.provider, "as_of_utc": q.ts_utc.isoformat(),
                        "as_of_ist": q.ts_ist.isoformat(), "contract": q.contract_key,
                        "is_delayed": q.is_delayed, "quality": q.quality.value})
            if len(out) >= limit: break
        return out

    def history_frame(self) -> pd.DataFrame:
        snaps = [{"instrument": r["instrument"], "slot": r["slot"], "trading_date": r["slot_date"],
                  "price": r["price"]} for r in self._quotes if r["slot"]]
        acts = [{"trading_date": v["trading_date"], "close": v["close"]}
                for v in self._actuals.values() if v["instrument"] == "MCX_GOLD"]
        return build_history_frame(snaps, acts)

    def save_actual(self, trading_date, instrument, contract_key, close, source) -> bool:
        k = (trading_date, instrument, contract_key, source)
        if k in self._actuals: return False
        self._actuals[k] = {"trading_date": trading_date, "instrument": instrument, "close": close,
                            "contract_key": contract_key, "source": source}
        return True

    def actual_close(self, trading_date, instrument="MCX_GOLD"):
        v = [a["close"] for a in self._actuals.values()
             if a["trading_date"] == trading_date and a["instrument"] == instrument]
        return v[-1] if v else None

    # --- predictions (append-only) ---
    def save_prediction(self, pred: dict, force: bool = False):
        same = [p for p in self._preds if p["target_date"] == pred["target_date"]
                and p["model_version"] == pred["model_version"]]
        if same and not force:
            return copy.deepcopy(same[-1]), False
        rec = copy.deepcopy(pred)
        rec["id"] = len(self._preds) + 1
        rec["run_seq"] = len(same) + 1
        rec["created_at_utc"] = datetime.now(timezone.utc).isoformat()
        self._preds.append(rec)
        return copy.deepcopy(rec), True

    def latest_prediction(self):
        return copy.deepcopy(self._preds[-1]) if self._preds else None

    def predictions(self, limit):
        return copy.deepcopy(list(reversed(self._preds))[:limit])

    def pending_predictions(self, up_to):
        return [copy.deepcopy(p) for p in self._preds
                if p["target_date"] <= up_to and p["id"] not in self._evals]

    def save_evaluation(self, ev) -> bool:
        if ev["prediction_id"] in self._evals: return False
        self._evals[ev["prediction_id"]] = copy.deepcopy(ev); return True

    def evaluations(self):
        return copy.deepcopy(list(self._evals.values()))

    # --- models / logs ---
    def save_model_version(self, meta): self._models[meta["version"]] = copy.deepcopy(meta)

    def save_model_metrics(self, version, scope, metrics):
        self._metrics.append({"version": version, "scope": scope, "metrics": copy.deepcopy(metrics)})

    def model_metrics(self): return copy.deepcopy(self._metrics)

    def log(self, workflow, level, event, message, payload=None):
        self.logs.append({"workflow": workflow, "level": level, "event": event, "message": message,
                          "payload": payload or {}, "ts": datetime.now(timezone.utc).isoformat()})
