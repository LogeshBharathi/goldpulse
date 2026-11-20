"""Historical daily dataset loader (your licensed export, or the synthetic generator).

Required columns (one row per Indian trading date D, ascending):
  date              IST trading date D
  mcx_close         MCX gold (continuous, rollover-adjusted) close on D, INR/10g
  comex_close       COMEX GC snapshot at ~23:30 IST on D     (USD/oz)
  xau_close         XAU/USD snapshot at ~23:30 IST on D
  usdinr_close      USD/INR snapshot at ~23:30 IST on D
  comex_morning     COMEX GC snapshot at ~08:30 IST on D     (what the morning job sees)
  xau_morning       XAU/USD snapshot at ~08:30 IST on D
  usdinr_morning    USD/INR snapshot at ~08:30 IST on D
Optional: data_source (str).  'SYNTHETIC' marks generated data.
"""
from __future__ import annotations

import pandas as pd

REQUIRED = ["date", "mcx_close", "comex_close", "xau_close", "usdinr_close",
            "comex_morning", "xau_morning", "usdinr_morning"]


class DatasetError(ValueError):
    pass


def load_history_csv(path: str, allow_synthetic: bool = False) -> pd.DataFrame:
    df = pd.read_csv(path)
    return validate_history(df, allow_synthetic=allow_synthetic)


def validate_history(df: pd.DataFrame, allow_synthetic: bool = False) -> pd.DataFrame:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DatasetError(f"Missing columns: {missing}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    if not df["date"].is_monotonic_increasing or df["date"].duplicated().any():
        raise DatasetError("dates must be strictly ascending and unique")
    if not allow_synthetic and "data_source" in df.columns and (df["data_source"] == "SYNTHETIC").any():
        raise DatasetError("Dataset is marked SYNTHETIC; pass allow_synthetic=True (testing only).")
    num = [c for c in REQUIRED if c != "date"]
    if (df[num] <= 0).any().any():
        raise DatasetError("non-positive prices found")
    if df[num].isna().any().any():
        raise DatasetError("NaNs found in required columns; handle missing data before training")
    return df.reset_index(drop=True)
