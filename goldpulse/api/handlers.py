"""Framework-free endpoint logic (unit-testable without FastAPI). main.py binds these."""
from __future__ import annotations

from datetime import date, datetime, timezone

from goldpulse.config import Settings
from goldpulse.integrations.sheets import accuracy_row, daily_prediction_row
from goldpulse.providers.base import Instrument, ProviderError, Quote
from goldpulse.report import render_report, render_telegram
from goldpulse.service import (PredictionBlocked, accuracy_summary, evaluate_predictions, generate_prediction)


class ApiError(Exception):
    def __init__(self, status: int, detail):
        self.status, self.detail = status, detail


class App:
    """Holds dependencies; one instance per process."""
    def __init__(self, repo, bundle, settings: Settings, cal_in, cal_us, providers: list, clock=None):
        self.repo, self.bundle, self.settings = repo, bundle, settings
        self.cal_in, self.cal_us, self.providers = cal_in, cal_us, providers
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # GET /api/v1/market/latest
    def market_latest(self) -> dict:
        now = self.clock(); out = {}
        for i in Instrument:
            q = self.repo.latest_quote(i)
            out[i.value] = None if q is None else {
                "price": q.price, "currency": q.currency, "provider": q.provider,
                "as_of_utc": q.ts_utc.isoformat(), "as_of_ist": q.ts_ist.isoformat(),
                "age_minutes": round((now - q.ts_utc).total_seconds() / 60, 1),
                "contract": q.contract_key, "is_delayed": q.is_delayed, "quality": q.quality.value}
        return out

    # GET /api/v1/market/history
    def market_history(self, instrument: str | None, start: datetime | None, end: datetime | None,
                       limit: int = 500) -> list[dict]:
        inst = None
        if instrument:
            try: inst = Instrument(instrument)
            except ValueError: raise ApiError(400, f"unknown instrument {instrument}")
        return self.repo.quotes(inst, start, end, min(max(limit, 1), 5000))

    # POST /api/v1/predictions/generate
    def predictions_generate(self, force: bool = False, trigger: str = "api") -> dict:
        try:
            p = generate_prediction(self.repo, self.bundle, self.settings, self.clock(),
                                    self.cal_in, self.cal_us, force=force, trigger=trigger)
        except PredictionBlocked as e:
            self.repo.log("prediction", "WARNING", "blocked", e.reason, e.details)
            raise ApiError(422, {"error": "prediction_blocked", "reason": e.reason, "details": e.details})
        return self._with_text(p)

    def _with_text(self, p: dict) -> dict:
        p = dict(p)
        p["report_text"] = render_report(p)
        p["telegram_text"] = render_telegram(p)
        p["sheet_row"] = daily_prediction_row(p)
        return p

    def predictions_latest(self) -> dict:
        p = self.repo.latest_prediction()
        if not p: raise ApiError(404, "no predictions yet")
        return self._with_text(p)

    def predictions_history(self, limit: int = 100) -> list[dict]:
        return self.repo.predictions(min(max(limit, 1), 1000))

    def predictions_accuracy(self) -> dict:
        return accuracy_summary(self.repo.evaluations())

    # POST /api/v1/predictions/evaluate
    def predictions_evaluate(self, up_to: date | None = None) -> dict:
        thr = self.bundle.meta.get("neutral_threshold", self.settings.neutral_threshold_pct / 100)
        evs = evaluate_predictions(self.repo, up_to or self.clock().date(), thr)
        return {"evaluated": len(evs), "evaluations": evs, "sheet_rows": [accuracy_row(e) for e in evs],
                "summary": accuracy_summary(self.repo.evaluations())}

    # GET /api/v1/models/performance
    def models_performance(self) -> dict:
        m = self.bundle.meta
        return {"active_version": self.bundle.version, "chosen_model": m.get("chosen_model"),
                "backend": m.get("backend"), "trained_through": m.get("trained_through"),
                "calibrated": m.get("calibrated"), "limitations": m.get("limitations", []),
                "walk_forward_metrics": m.get("metrics_walk_forward"),
                "direction_classifier_metrics": m.get("metrics_direction_classifier"),
                "candidates": m.get("metrics_all_candidates"),
                "live_metrics": self.repo.model_metrics(), "live_accuracy": self.predictions_accuracy()}

    # GET /api/v1/market/fetch/{instrument}   (used by n8n; does not persist)
    def market_fetch(self, instrument: str) -> dict:
        try: inst = Instrument(instrument)
        except ValueError: raise ApiError(400, f"unknown instrument {instrument}")
        errors = []
        for prov in self.providers:
            if not prov.supports(inst): continue
            try:
                q = prov.fetch_latest(inst)
                return quote_to_dict(q)
            except ProviderError as e:
                errors.append(str(e))
        raise ApiError(502, {"error": "no_provider_succeeded", "instrument": instrument, "errors": errors})

    # POST /api/v1/market/snapshot   (persist a validated quote under a schedule slot)
    def market_snapshot(self, body: dict) -> dict:
        from goldpulse.providers.base import Quote
        try:
            q = Quote(instrument=Instrument(body["instrument"]), price=float(body["price"]),
                      currency=body["currency"], provider=body["provider"],
                      market_ts=datetime.fromisoformat(body["as_of"]), market_id=body.get("market_id", ""),
                      is_delayed=bool(body.get("is_delayed", False)), delay_minutes=int(body.get("delay_minutes", 0)))
            slot, slot_date = body.get("slot"), date.fromisoformat(body["slot_date"]) if body.get("slot_date") else None
        except (KeyError, ValueError) as e:
            raise ApiError(400, f"bad snapshot payload: {e}")
        if slot not in (None, "CLOSE", "MORNING"): raise ApiError(400, "slot must be CLOSE or MORNING")
        if slot and not slot_date: raise ApiError(400, "slot_date required with slot")
        from goldpulse.validation import validate_quote, usable
        status, notes = validate_quote(q, self.clock(), self.settings.max_age_hours[q.instrument.value])
        q.quality, q.quality_notes = status, notes
        if not usable(status):
            self.repo.log("collect", "WARNING", "rejected", f"{q.instrument.value} {status.value}", {"notes": notes})
            raise ApiError(422, {"error": "quote_rejected", "status": status.value, "notes": notes})
        return {"inserted": self.repo.upsert_quote(q, slot, slot_date), "status": status.value}

    # POST /api/v1/market/actual
    def market_actual(self, body: dict) -> dict:
        try:
            ok = self.repo.save_actual(date.fromisoformat(body["trading_date"]), body.get("instrument", "MCX_GOLD"),
                                       body.get("contract_key", "SPOT"), float(body["close"]), body["source"])
        except (KeyError, ValueError) as e:
            raise ApiError(400, f"bad actual payload: {e}")
        return {"inserted": ok}

    # GET /api/v1/calendar/status   (n8n uses this to skip holidays)
    def calendar_status(self, d: date | None = None) -> dict:
        from goldpulse.config import IST, NEW_YORK
        now = self.clock()
        d = d or now.astimezone(IST).date()
        et = now.astimezone(NEW_YORK).date()
        return {"date": d.isoformat(), "india_trading_day": self.cal_in.is_trading_day(d),
                "india_calendar_covers_date": self.cal_in.covers(d),
                "us_et_date": et.isoformat(), "us_holiday": et in self.cal_us.holidays,
                "us_calendar_covers_date": self.cal_us.covers(et)}

    # POST /api/v1/logs   (n8n failure/success logging -> automation_logs)
    def log_event(self, body: dict) -> dict:
        try:
            self.repo.log(str(body["workflow"])[:40], str(body.get("level", "INFO"))[:10],
                          str(body["event"])[:40], str(body.get("message", "")), body.get("payload"))
        except KeyError as e:
            raise ApiError(400, f"missing field {e}")
        return {"logged": True}

    # GET /api/v1/health
    def health(self) -> dict:
        m = self.bundle.meta if self.bundle else {}
        return {"status": "ok", "model_version": getattr(self.bundle, "version", None),
                "model_limitations": m.get("limitations", []),
                "india_calendar_years": sorted(self.cal_in.covered_years),
                "us_calendar_years": sorted(self.cal_us.covered_years),
                "providers": [{"name": getattr(p, "name", "?"),
                               "licensed": bool(getattr(p, "licensed", False))} for p in self.providers],
                "now_utc": self.clock().isoformat()}


def quote_to_dict(q: Quote) -> dict:
    out = {"instrument": q.instrument.value, "price": q.price, "currency": q.currency, "provider": q.provider,
           "as_of": q.ts_utc.isoformat(), "as_of_ist": q.ts_ist.isoformat(), "market_id": q.market_id,
           "contract_key": q.contract_key,
           "contract_expiry": q.contract_expiry.isoformat() if q.contract_expiry else None,
           "is_delayed": q.is_delayed, "delay_minutes": q.delay_minutes}
    if isinstance(q.raw, dict) and q.raw.get("feed"):
        out["feed"] = q.raw["feed"]
        if q.raw.get("note"):
            out["note"] = q.raw["note"]
    return out
