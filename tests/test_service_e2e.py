"""End-to-end tests of generate -> store -> report -> evaluate using the in-memory repository."""
from datetime import date, datetime, timedelta, timezone

import pytest

from goldpulse.api.handlers import ApiError, App
from goldpulse.config import Settings
from goldpulse.ml.registry import train
from goldpulse.providers.base import Instrument, Quote
from goldpulse.repository import InMemoryRepository
from goldpulse.service import PredictionBlocked, generate_prediction, target_trading_date
from goldpulse.trading_calendar import load_india_calendar, load_us_calendar

CAL_IN, CAL_US = load_india_calendar("data/holidays"), load_us_calendar("data/holidays")
SET = Settings()
THR = 0.001
# 2026-11-19 (Thu) 08:30 IST == 03:00 UTC.  Target session = same day.
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bundle(synth):
    return train(synth, THR, min_train_rows=250, is_synthetic=True)


def seeded_repo(synth, now=NOW, shift_to=date(2026, 11, 18)):
    """Re-date the synthetic history so its last row is the previous trading day (2026-11-18)."""
    import pandas as pd
    h = synth.copy()
    n = len(h)
    dates, d = [], shift_to
    while len(dates) < n:
        if CAL_IN.is_trading_day(d): dates.append(d)
        d -= timedelta(days=1)
    h["date"] = list(reversed(dates))
    repo = InMemoryRepository()
    t0 = now - timedelta(days=1)
    for _, r in h.iterrows():
        for inst, c in [(Instrument.COMEX_GC, "comex"), (Instrument.XAUUSD, "xau"), (Instrument.USDINR, "usdinr")]:
            for slot, col in [("CLOSE", f"{c}_close"), ("MORNING", f"{c}_morning")]:
                ts = datetime.combine(r["date"], datetime.min.time(), tzinfo=timezone.utc) + \
                     timedelta(hours=18 if slot == "CLOSE" else 3, minutes=0 if slot == "MORNING" else 0)
                repo.upsert_quote(Quote(inst, float(r[col]), "USD", "hist", ts), slot, r["date"])
        repo.save_actual(r["date"], "MCX_GOLD", "SPOT", float(r["mcx_close"]), "hist")
    return repo, h


def add_live_quotes(repo, h, age_h=0.5, skip=None, comex_mult=1.004, fx_mult=1.001):
    last = h.iloc[-1]
    base_ts = NOW - timedelta(hours=age_h)
    vals = {Instrument.COMEX_GC: last.comex_close * comex_mult, Instrument.XAUUSD: last.xau_close * comex_mult,
            Instrument.USDINR: last.usdinr_close * fx_mult, Instrument.MCX_GOLD: last.mcx_close}
    for inst, v in vals.items():
        if inst == skip: continue
        repo.upsert_quote(Quote(inst, float(v), "USD", "live", base_ts, is_delayed=(inst == Instrument.XAUUSD),
                                delay_minutes=10 if inst == Instrument.XAUUSD else 0))


def test_target_date_logic():
    assert target_trading_date(NOW, CAL_IN) == date(2026, 11, 19)                       # 08:30 Thu -> today
    assert target_trading_date(datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc), CAL_IN) == date(2026, 11, 20)
    assert target_trading_date(datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc), CAL_IN) == date(2026, 11, 23)  # Fri eve -> Mon


def test_happy_path_generates_stores_and_is_idempotent(synth, bundle):
    repo, h = seeded_repo(synth); add_live_quotes(repo, h)
    p = generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US)
    assert p["created"] and p["target_date"] == date(2026, 11, 19)
    assert p["previous_mcx_close"] == pytest.approx(h.mcx_close.iloc[-1], abs=0.01)
    assert p["indicators"]["comex_overnight_pct"] == pytest.approx(0.4, abs=1e-6)
    assert p["interval_low"] < p["predicted_mcx_price"] < p["interval_high"]
    assert p["direction"] in {"UP", "DOWN", "NEUTRAL"} and p["retail_24k_per_g"] > p["retail_22k_per_g"]
    assert any("SYNTHETIC" in w for w in p["data_quality"]["warnings"])
    assert any("delayed" in w for w in p["data_quality"]["warnings"])
    assert p["confidence"] is None or bundle.meta["calibrated"]       # never shown if uncalibrated
    again = generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US)
    assert not again["created"] and again["id"] == p["id"] and len(repo.predictions(10)) == 1
    forced = generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US, force=True)
    assert forced["created"] and forced["run_seq"] == 2 and len(repo.predictions(10)) == 2   # append-only


def test_stale_missing_and_history_gap_block_prediction(synth, bundle):
    repo, h = seeded_repo(synth); add_live_quotes(repo, h, age_h=6)
    with pytest.raises(PredictionBlocked) as e:
        generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US)
    assert "STALE" in str(e.value.details["problems"]) and repo.predictions(5) == []

    repo, h = seeded_repo(synth); add_live_quotes(repo, h, skip=Instrument.MCX_GOLD)
    with pytest.raises(PredictionBlocked) as e:
        generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US)
    assert "MISSING" in str(e.value.details["problems"])

    repo, h = seeded_repo(synth, shift_to=date(2026, 11, 17)); add_live_quotes(repo, h)     # history one day behind
    with pytest.raises(PredictionBlocked) as e:
        generate_prediction(repo, bundle, SET, NOW, CAL_IN, CAL_US)
    assert "not current" in e.value.reason


def test_us_holiday_blocks():
    now = datetime(2026, 11, 27, 3, 0, tzinfo=timezone.utc)   # 22:00 ET on Thanksgiving (2026-11-26)
    with pytest.raises(PredictionBlocked) as e:
        generate_prediction(InMemoryRepository(), None, SET, now, CAL_IN, CAL_US)
    assert "US market holiday" in e.value.reason


def test_api_handlers_flow_and_evaluation(synth, bundle):
    repo, h = seeded_repo(synth); add_live_quotes(repo, h)
    app = App(repo, bundle, SET, CAL_IN, CAL_US, providers=[], clock=lambda: NOW)
    with pytest.raises(ApiError) as e: app.predictions_latest()
    assert e.value.status == 404
    out = app.predictions_generate()
    assert "GOLDPULSE INDIA" in out["report_text"] and "DISCLAIMER" in out["report_text"]
    assert "statistical forecast" in out["telegram_text"] and out["sheet_row"][-1] == "PENDING"
    assert len(out["sheet_row"]) == 9
    assert app.market_latest()["COMEX_GC"]["age_minutes"] == 30.0
    assert app.health()["status"] == "ok" and app.models_performance()["active_version"] == bundle.version
    assert app.predictions_accuracy()["n"] == 0
    # actual close arrives after the session
    actual = out["predicted_mcx_price"] * 1.004
    assert app.market_actual({"trading_date": "2026-11-19", "close": actual, "source": "mcx-eod"})["inserted"]
    ev = app.predictions_evaluate(date(2026, 11, 19))
    assert ev["evaluated"] == 1 and ev["evaluations"][0]["abs_error"] == pytest.approx(actual - out["predicted_mcx_price"], abs=0.01)
    assert app.predictions_evaluate(date(2026, 11, 19))["evaluated"] == 0          # idempotent
    assert app.predictions_accuracy()["n"] == 1
    assert len(app.predictions_history()) == 1


def test_snapshot_endpoint_rejects_stale_and_dedupes():
    app = App(InMemoryRepository(), None, SET, CAL_IN, CAL_US, [], clock=lambda: NOW)
    body = {"instrument": "COMEX_GC", "price": 4000, "currency": "USD", "provider": "p",
            "as_of": (NOW - timedelta(minutes=5)).isoformat(), "slot": "MORNING", "slot_date": "2026-11-19"}
    assert app.market_snapshot(body)["inserted"] is True
    assert app.market_snapshot(body)["inserted"] is False                         # duplicate prevented
    with pytest.raises(ApiError) as e:
        app.market_snapshot({**body, "as_of": (NOW - timedelta(hours=30)).isoformat(), "slot_date": "2026-11-18"})
    assert e.value.status == 422
    with pytest.raises(ApiError): app.market_snapshot({**body, "as_of": "2026-11-19T03:00:00"})   # naive tz? -> rejected
    assert any(l["event"] == "rejected" for l in app.repo.logs)


def test_market_fetch_falls_through_providers():
    from goldpulse.providers.base import ProviderError
    class Bad:
        name = "bad"; licensed = True
        def supports(self, i): return True
        def fetch_latest(self, i): raise ProviderError("down")
    class Good(Bad):
        name = "good"
        def fetch_latest(self, i): return Quote(i, 1.0, "USD", "good", NOW)
    app = App(InMemoryRepository(), None, SET, CAL_IN, CAL_US, [Bad(), Good()], clock=lambda: NOW)
    assert app.market_fetch("XAUUSD")["provider"] == "good"
    app = App(InMemoryRepository(), None, SET, CAL_IN, CAL_US, [Bad()], clock=lambda: NOW)
    with pytest.raises(ApiError) as e: app.market_fetch("XAUUSD")
    assert e.value.status == 502
    with pytest.raises(ApiError) as e: app.market_fetch("BTC")
    assert e.value.status == 400
