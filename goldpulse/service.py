"""Prediction + evaluation orchestration. Independent of n8n and of FastAPI."""
from __future__ import annotations

from datetime import date, datetime, time
from statistics import NormalDist

import numpy as np

from goldpulse.config import IST, NEW_YORK, Settings
from goldpulse.ml.features import FEATURE_COLUMNS, label_direction, live_feature_row
from goldpulse.ml.registry import TrainedBundle
from goldpulse.pricing import PricingAssumptions, retail_from_mcx
from goldpulse.providers.base import Instrument
from goldpulse.trading_calendar import TradingCalendar
from goldpulse.validation import check_all


class PredictionBlocked(RuntimeError):
    """Raised instead of producing a forecast from stale/incomplete inputs."""
    def __init__(self, reason: str, details: dict | None = None):
        super().__init__(reason)
        self.reason, self.details = reason, details or {}


def pricing_from_settings(s: Settings) -> PricingAssumptions:
    return PricingAssumptions(import_duty_pct=s.import_duty_pct, gst_pct=s.gst_pct,
                              domestic_premium_pct=s.domestic_premium_pct,
                              retail_margin_pct=s.retail_margin_pct, making_charges_pct=s.making_charges_pct)


def target_trading_date(now_utc: datetime, cal_in: TradingCalendar) -> date:
    """Upcoming (or currently-opening) Indian session. Before 09:00 IST on a trading day -> today."""
    now = now_utc.astimezone(IST)
    if cal_in.is_trading_day(now.date()) and now.time() < time(9, 0):
        return now.date()
    return cal_in.next_trading_day(now.date())


def generate_prediction(repo, bundle: TrainedBundle, settings: Settings, now_utc: datetime,
                        cal_in: TradingCalendar, cal_us: TradingCalendar, force: bool = False,
                        trigger: str = "api") -> dict:
    warnings: list[str] = []
    target = target_trading_date(now_utc, cal_in)
    if not cal_in.covers(target):
        warnings.append(f"India holiday calendar has no data for {target.year}; holiday handling unverified.")
    et_date = now_utc.astimezone(NEW_YORK).date()
    if cal_us.covers(et_date) and et_date in cal_us.holidays:
        raise PredictionBlocked(f"US market holiday ({et_date}); COMEX data not current.")
    if not cal_us.covers(et_date):
        warnings.append(f"US holiday calendar has no data for {et_date.year}.")

    quotes = {i: repo.latest_quote(i, before_utc=now_utc) for i in Instrument}
    dq = check_all(quotes, now_utc, settings.max_age_hours)
    if not dq["ok"]:
        raise PredictionBlocked("Stale or incomplete market data", dq)

    hist = repo.history_frame()
    prev_td = cal_in.previous_trading_day(target)
    if hist.empty or hist["date"].iloc[-1] != prev_td:
        last = None if hist.empty else str(hist["date"].iloc[-1])
        raise PredictionBlocked(
            f"Historical frame not current: last complete row {last}, need previous trading day {prev_td}.",
            {"last_history_date": last, "required": str(prev_td)})

    row = live_feature_row(hist, target, quotes[Instrument.COMEX_GC].price, quotes[Instrument.XAUUSD].price,
                           quotes[Instrument.USDINR].price)
    if row[FEATURE_COLUMNS].isna().any(axis=None):
        raise PredictionBlocked("Insufficient history to compute features",
                                {"nan_features": [c for c in FEATURE_COLUMNS if row[c].isna().any()]})

    prev_close = float(hist["mcx_close"].iloc[-1])
    mcx_q = quotes[Instrument.MCX_GOLD]
    if mcx_q.prev_close and abs(mcx_q.prev_close / prev_close - 1) > 0.02:
        warnings.append("MCX quote prev_close differs >2% from stored close; check contract/rollover.")
    for inst, r in dq["per_instrument"].items():
        warnings += [f"{inst}: {n}" for n in r["notes"]]
    for inst, q in quotes.items():
        if q is not None and isinstance(q.raw, dict) and q.raw.get("feed") == "public":
            warnings.append(
                f"{inst.value}: {q.provider} is an indicative public feed, not a licensed exchange feed."
            )

    meta = bundle.meta
    warnings += meta.get("limitations", [])
    pred_ret = float(bundle.return_model.predict(row)[0])
    price = prev_close * (1 + pred_ret)

    iv = meta.get("interval")
    if iv:
        lo_r, hi_r, iv_basis = pred_ret + iv["low"], pred_ret + iv["high"], "empirical out-of-sample residual quantiles"
    else:
        z = NormalDist().inv_cdf(0.5 + settings.interval_level / 2)
        sigma = float(row["vol_10"].iloc[0])
        lo_r, hi_r = pred_ret - z * sigma, pred_ret + z * sigma
        iv_basis = "UNCALIBRATED volatility-based (10d std) – no out-of-sample residuals available"
        warnings.append("Prediction interval is volatility-based and not empirically validated.")
    thr = meta.get("neutral_threshold", settings.neutral_threshold_pct / 100)

    raw_scores, confidence = None, None
    if bundle.direction_model is not None:
        proba = bundle.direction_model.predict_proba(row).iloc[0]
        raw_scores = {k: float(v) for k, v in proba.items()}
        direction = str(proba.idxmax())
        if meta.get("calibrated"):
            confidence = float(proba.max())
    else:
        direction = str(label_direction([pred_ret], thr)[0])
    retail = retail_from_mcx(price, pricing_from_settings(settings))

    f = row.iloc[0]
    last = hist.iloc[-1]
    pred = {
        "target_date": target, "model_version": bundle.version, "trigger": trigger,
        "previous_mcx_close": round(prev_close, 2), "expected_return_pct": round(pred_ret * 100, 4),
        "predicted_mcx_price": round(price, 2),
        "interval_low": round(prev_close * (1 + lo_r), 2), "interval_high": round(prev_close * (1 + hi_r), 2),
        "interval_level": iv["level"] if iv else settings.interval_level, "interval_basis": iv_basis,
        "direction": direction, "direction_raw_scores": raw_scores, "confidence": confidence,
        "retail_24k_per_g": retail.per_gram_24k, "retail_22k_per_g": retail.per_gram_22k,
        "pricing_assumptions": retail.assumptions, "retail_notes": retail.notes,
        "indicators": {
            "comex_prev_close": float(last["comex_close"]), "comex_latest": quotes[Instrument.COMEX_GC].price,
            "comex_overnight_pct": round(float(f["comex_overnight"]) * 100, 4),
            "usdinr": quotes[Instrument.USDINR].price, "usdinr_prev_close": float(last["usdinr_close"]),
            "usdinr_move_pct": round(float(f["fx_overnight"]) * 100, 4),
            "xauusd": quotes[Instrument.XAUUSD].price, "xau_overnight_pct": round(float(f["xau_overnight"]) * 100, 4),
            "previous_mcx_close": prev_close, "vol_10_pct": round(float(f["vol_10"]) * 100, 4),
            "momentum_5d_pct": round(float(f["mom_5"]) * 100, 4),
        },
        "data_quality": {"checks": dq["per_instrument"], "warnings": list(dict.fromkeys(warnings))},
        "model": {"chosen": meta.get("chosen_model"), "backend": meta.get("backend"),
                  "calibrated": bool(meta.get("calibrated")), "synthetic": bool(meta.get("synthetic")),
                  "trained_through": meta.get("trained_through")},
    }
    saved, created = repo.save_prediction(pred, force=force)
    saved["created"] = created
    repo.log("prediction", "INFO", "generated" if created else "duplicate_skipped",
             f"{saved['target_date']} {saved['model_version']}", {"direction": saved["direction"]})
    return saved


def evaluate_predictions(repo, up_to: date, neutral_threshold: float) -> list[dict]:
    """Compare every pending prediction with the actual MCX close, if known. Idempotent."""
    out = []
    for p in repo.pending_predictions(up_to):
        actual = repo.actual_close(p["target_date"])
        if actual is None:
            continue
        err = p["predicted_mcx_price"] - actual
        actual_ret = actual / p["previous_mcx_close"] - 1
        actual_dir = str(label_direction([actual_ret], neutral_threshold)[0])
        ev = {"prediction_id": p["id"], "target_date": p["target_date"], "model_version": p["model_version"],
              "predicted_price": p["predicted_mcx_price"], "actual_close": actual,
              "error": round(err, 2), "abs_error": round(abs(err), 2),
              "pct_error": round(abs(err) / actual * 100, 4),
              "predicted_direction": p["direction"], "actual_direction": actual_dir,
              "direction_correct": p["direction"] == actual_dir,
              "within_interval": p["interval_low"] <= actual <= p["interval_high"],
              "accuracy_status": "HIT" if p["direction"] == actual_dir else "MISS"}
        if repo.save_evaluation(ev):
            out.append(ev)
    return out


def accuracy_summary(evals: list[dict]) -> dict:
    if not evals:
        return {"n": 0, "note": "No evaluated predictions yet."}
    a = np.array([e["abs_error"] for e in evals]); pct = np.array([e["pct_error"] for e in evals])
    return {"n": len(evals), "mae": float(a.mean()), "rmse": float(np.sqrt((a ** 2).mean())),
            "mape_pct": float(pct.mean()),
            "directional_accuracy": float(np.mean([e["direction_correct"] for e in evals])),
            "interval_coverage": float(np.mean([e["within_interval"] for e in evals])),
            "note": "Live results; small samples are not statistically meaningful."}
