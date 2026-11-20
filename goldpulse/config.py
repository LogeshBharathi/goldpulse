"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://goldpulse:goldpulse@localhost:5432/goldpulse"
    )
    model_dir: str = os.environ.get("MODEL_DIR", "models")
    holiday_dir: str = os.environ.get("HOLIDAY_DIR", "data/holidays")
    api_key: str = os.environ.get("GOLDPULSE_API_KEY", "")  # shared secret for n8n -> API
    # Neutral band: |return| below this (in %) is labelled NEUTRAL.
    neutral_threshold_pct: float = _f("NEUTRAL_THRESHOLD_PCT", 0.10)
    interval_level: float = _f("INTERVAL_LEVEL", 0.80)
    min_train_rows: int = int(os.environ.get("MIN_TRAIN_ROWS", 250))
    # Freshness limits (hours) applied at prediction time.
    max_age_hours: dict = field(
        default_factory=lambda: {
            "COMEX_GC": _f("MAX_AGE_COMEX_H", 3),
            "XAUUSD": _f("MAX_AGE_XAUUSD_H", 3),
            "USDINR": _f("MAX_AGE_USDINR_H", 6),
            "MCX_GOLD": _f("MAX_AGE_MCX_H", 36),
        }
    )
    # Pricing assumptions (all PLACEHOLDERS to be verified/calibrated by the operator).
    import_duty_pct: float = _f("IMPORT_DUTY_PCT", 6.0)
    gst_pct: float = _f("GST_PCT", 3.0)
    domestic_premium_pct: float = _f("DOMESTIC_PREMIUM_PCT", 0.0)
    retail_margin_pct: float = _f("RETAIL_MARGIN_PCT", 0.0)
    making_charges_pct: float = _f("MAKING_CHARGES_PCT", 0.0)
    telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.environ.get("TELEGRAM_CHAT_ID", "")


def get_settings() -> Settings:
    return Settings()
