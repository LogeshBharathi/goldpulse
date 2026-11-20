"""Load public daily history and the latest indicative quotes into PostgreSQL.

  python scripts/bootstrap_live.py

History comes from Yahoo Finance daily bars (COMEX GC=F, USD/INR) and xaus.com
daily XAU closes. MCX publishes no public daily file that this environment can
read, so MCX closes are loaded only from data/mcx_gold.csv when that file exists
(columns: date, close). The latest MCX futures trade is still stored as a quote.

Daily bars have a session date, not an 08:30/23:30 print. Slot timestamps use
those schedule times and the raw payload says so. Prices are the daily open
(morning) and close.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from goldpulse.config import IST, get_settings  # noqa: E402
from goldpulse.ml.registry import load_bundle  # noqa: E402
from goldpulse.providers.base import Instrument, Quote  # noqa: E402
from goldpulse.providers.public_feeds import (PublicFeedProvider, XAUS_HISTORY, YAHOO_CHART,  # noqa: E402
                                              xaus_daily_bars, yahoo_daily_bars)
from goldpulse.db.sql_repository import SqlRepository  # noqa: E402
from goldpulse.trading_calendar import load_india_calendar  # noqa: E402


def _slot_ts(day, hh, mm) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=IST)


def _history_frames(provider: PublicFeedProvider) -> pd.DataFrame:
    gc = yahoo_daily_bars(provider._get_json(YAHOO_CHART.format(symbol="GC=F"),
                                              {"interval": "1d", "range": "2y"}))
    fx = yahoo_daily_bars(provider._get_json(YAHOO_CHART.format(symbol="INR=X"),
                                              {"interval": "1d", "range": "2y"}))
    xau = xaus_daily_bars(provider._get_json(XAUS_HISTORY, {"range": "5y"}))
    g = pd.DataFrame(gc).rename(columns={"open": "comex_open", "close": "comex_close"})
    f = pd.DataFrame(fx).rename(columns={"open": "fx_open", "close": "usdinr_close"})
    x = pd.DataFrame(xau).rename(columns={"close": "xau_close"})
    out = g.merge(f, on="date", how="inner").merge(x, on="date", how="inner")
    out = out.dropna(subset=["comex_close", "usdinr_close", "xau_close"]).sort_values("date")
    out = out.drop_duplicates("date", keep="last")
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out


def _load_mcx_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["date", "close"])
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["close"] = df["close"].astype(float)
    return df[["date", "close"]].dropna()


def main():
    s = get_settings()
    repo = SqlRepository(s.database_url)
    model_path = os.environ.get("ACTIVE_MODEL_PATH", "")
    if not model_path:
        raise SystemExit("ACTIVE_MODEL_PATH is not set")
    bundle = load_bundle(model_path)
    repo.save_model_version(bundle.meta)
    if bundle.meta.get("metrics_walk_forward"):
        repo.save_model_metrics(bundle.version, "walk_forward", bundle.meta["metrics_walk_forward"])
    provider = PublicFeedProvider()
    hist = _history_frames(provider)
    mcx = _load_mcx_csv(Path("data/mcx_gold.csv"))
    n_slots = 0
    horizon = datetime.now(timezone.utc)
    for _, r in hist.iterrows():
        day = r["date"]
        morning_open = r["comex_open"] if pd.notna(r["comex_open"]) else r["comex_close"]
        fx_open = r["fx_open"] if pd.notna(r["fx_open"]) else r["usdinr_close"]
        rows = [
            (Instrument.COMEX_GC, "public-yahoo", "USD", "COMEX", morning_open, r["comex_close"], "GC=F"),
            (Instrument.XAUUSD, "public-xaus", "USD", "SPOT", r["xau_close"], r["xau_close"], None),
            (Instrument.USDINR, "public-yahoo", "INR", "FX", fx_open, r["usdinr_close"], None),
        ]
        for inst, prov, cur, market, morning, close, code in rows:
            note = ("Daily bar has no 08:30/23:30 print. Slot clock is the schedule convention; "
                    "price is the daily open (morning) or close. XAU morning repeats the daily close "
                    "when the source publishes no open.")
            for slot, hh, mm, price in (("MORNING", 8, 30, morning), ("CLOSE", 23, 30, close)):
                ts = _slot_ts(day, hh, mm)
                if ts.astimezone(timezone.utc) > horizon:
                    continue  # do not store a close timestamped after now
                q = Quote(inst, float(price), cur, prov, ts, market,
                          contract_code=code,
                          raw={"feed": "public", "field": slot.lower(), "note": note})
                repo.upsert_quote(q, slot, day)
                n_slots += 1
    n_actual = 0
    for _, r in mcx.iterrows():
        if repo.save_actual(r["date"], "MCX_GOLD", "SPOT", float(r["close"]), "mcx-csv"):
            n_actual += 1
    latest = {}
    for inst in Instrument:
        q = provider.fetch_latest(inst)
        repo.upsert_quote(q)
        latest[inst.value] = {"price": q.price, "as_of_ist": q.ts_ist.isoformat(), "provider": q.provider}

    cal = load_india_calendar(s.holiday_dir)
    now = datetime.now(IST)
    target = now.date() if cal.is_trading_day(now.date()) and now.time() < time(9, 0) else cal.next_trading_day(now.date())
    need = cal.previous_trading_day(target)
    joined = hist.merge(mcx, on="date", how="inner") if len(mcx) else hist.iloc[0:0]
    last = None if joined.empty else joined["date"].iloc[-1]
    print(f"model {bundle.version}")
    print(f"international daily rows aligned: {len(hist)}  ({hist['date'].iloc[0]} .. {hist['date'].iloc[-1]})")
    print(f"snapshot rows written: {n_slots}")
    print(f"MCX actuals inserted: {n_actual} from data/mcx_gold.csv")
    print(f"history last complete day: {last}; forecast for {target} needs {need}")
    for k, v in latest.items():
        print(f"live {k}: {v['price']} @ {v['as_of_ist']} via {v['provider']}")


if __name__ == "__main__":
    main()
