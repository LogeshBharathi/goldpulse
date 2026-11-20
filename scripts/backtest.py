"""Full-system backtest: replays the real service on historical days.

For each replayed morning D (after a warm-up), it builds a repository containing ONLY data that existed
before 08:30 IST on D, trains on rows < D, runs generate_prediction(), then evaluates against the actual
close. This exercises validation, features, pricing, interval, direction and evaluation together.

  python scripts/backtest.py --csv data/synthetic_history.csv --allow-synthetic --days 40
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from goldpulse.config import IST, Settings  # noqa: E402
from goldpulse.ml.registry import train  # noqa: E402
from goldpulse.providers.base import Instrument, Quote  # noqa: E402
from goldpulse.providers.csv_history import load_history_csv  # noqa: E402
from goldpulse.repository import InMemoryRepository  # noqa: E402
from goldpulse.service import accuracy_summary, evaluate_predictions, generate_prediction  # noqa: E402
from goldpulse.trading_calendar import TradingCalendar  # noqa: E402

MAP = [(Instrument.COMEX_GC, "comex"), (Instrument.XAUUSD, "xau"), (Instrument.USDINR, "usdinr")]


def replay(df: pd.DataFrame, n_days: int, thr: float, settings: Settings, synthetic: bool, min_train=250,
           retrain_every=10) -> tuple[pd.DataFrame, dict]:
    dates = list(df["date"])
    cal = TradingCalendar("replay", set(), {d.year for d in dates}, weekend=())   # dataset dates ARE the trading days
    calus = TradingCalendar("replay-us", set(), {d.year for d in dates}, weekend=())
    # make every dataset date a trading day and every other day closed
    all_days = {dates[0] + timedelta(days=i) for i in range((dates[-1] - dates[0]).days + 2)}
    cal.holidays = all_days - set(dates)
    out, bundle = [], None
    for k in range(len(df) - n_days, len(df)):
        hist, row = df.iloc[:k], df.iloc[k]
        if bundle is None or (k - (len(df) - n_days)) % retrain_every == 0:
            bundle = train(hist, thr, min_train, settings.interval_level, is_synthetic=synthetic)
        repo = InMemoryRepository()
        for _, r in hist.iterrows():
            for inst, c in MAP:
                for slot in ("CLOSE", "MORNING"):
                    ts = datetime.combine(r["date"], time(18 if slot == "CLOSE" else 3), tzinfo=timezone.utc)
                    repo.upsert_quote(Quote(inst, float(r[f"{c}_{slot.lower()}"]), "X", "hist", ts), slot, r["date"])
            repo.save_actual(r["date"], "MCX_GOLD", "SPOT", float(r["mcx_close"]), "hist")
        now = datetime.combine(row["date"], time(3, 0), tzinfo=timezone.utc)      # 08:30 IST
        for inst, c in MAP:
            repo.upsert_quote(Quote(inst, float(row[f"{c}_morning"]), "X", "live", now - timedelta(minutes=10)))
        repo.upsert_quote(Quote(Instrument.MCX_GOLD, float(hist["mcx_close"].iloc[-1]), "INR", "live",
                                now - timedelta(minutes=10)))
        p = generate_prediction(repo, bundle, settings, now, cal, calus)
        repo.save_actual(row["date"], "MCX_GOLD", "SPOT", float(row["mcx_close"]), "actual")
        evaluate_predictions(repo, row["date"], thr)
        ev = repo.evaluations()[0]
        out.append({**ev, "date": row["date"]})
    res = pd.DataFrame(out)
    return res, accuracy_summary(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--allow-synthetic", action="store_true")
    a = ap.parse_args()
    s = Settings()
    df = load_history_csv(a.csv, a.allow_synthetic)
    res, summ = replay(df, a.days, s.neutral_threshold_pct / 100, s, a.allow_synthetic)
    print(res[["date", "predicted_price", "actual_close", "abs_error", "predicted_direction",
               "actual_direction", "within_interval"]].tail(10).to_string(index=False))
    print(summ)
    if a.allow_synthetic:
        print("\nNOTE: dataset is SYNTHETIC; this validates the pipeline, not market forecasting skill.")
