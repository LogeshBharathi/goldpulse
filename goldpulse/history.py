"""Turn stored snapshots + actual MCX closes into the daily training frame."""
from __future__ import annotations

import pandas as pd

from goldpulse.providers.csv_history import REQUIRED


def build_history_frame(snapshots: list[dict], mcx_actuals: list[dict]) -> pd.DataFrame:
    """snapshots: {instrument, slot ('CLOSE'|'MORNING'), trading_date, price} for COMEX_GC/XAUUSD/USDINR.
    mcx_actuals: {trading_date, close}.  Rows lacking any column are dropped (never imputed)."""
    if not snapshots or not mcx_actuals:
        return pd.DataFrame(columns=REQUIRED)
    s = pd.DataFrame(snapshots)
    s = s[s["instrument"].isin(["COMEX_GC", "XAUUSD", "USDINR"])]
    s["col"] = s["instrument"].map({"COMEX_GC": "comex", "XAUUSD": "xau", "USDINR": "usdinr"}) + \
        "_" + s["slot"].str.lower().map({"close": "close", "morning": "morning"})
    wide = s.pivot_table(index="trading_date", columns="col", values="price", aggfunc="last")
    m = pd.DataFrame(mcx_actuals).drop_duplicates("trading_date", keep="last") \
        .rename(columns={"close": "mcx_close"}).set_index("trading_date")
    out = wide.join(m[["mcx_close"]], how="inner").dropna()
    out = out.reset_index().rename(columns={"trading_date": "date"}).sort_values("date")
    for c in REQUIRED:
        if c not in out:
            return pd.DataFrame(columns=REQUIRED)
    return out[REQUIRED].reset_index(drop=True)
