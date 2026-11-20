"""Integration tests for the PostgreSQL repository + migration.

Skipped unless sqlalchemy/psycopg2 are installed AND TEST_DATABASE_URL points at an EMPTY scratch database:
  TEST_DATABASE_URL=postgresql+psycopg2://goldpulse:pw@localhost:5432/goldpulse_test alembic upgrade head
  TEST_DATABASE_URL=... pytest tests/test_sql_repository.py
These were NOT executed in the authoring environment (no SQLAlchemy / Postgres available).
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg2")
URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

from sqlalchemy import text  # noqa: E402

from goldpulse.db.sql_repository import SqlRepository  # noqa: E402
from goldpulse.providers.base import Instrument, Quote  # noqa: E402


def test_quote_dedup_and_slot_uniqueness():
    repo = SqlRepository(URL)
    ts = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
    q = Quote(Instrument.COMEX_GC, 4000.0, "USD", "pytest", ts)
    assert repo.upsert_quote(q, "MORNING", date(2099, 1, 1)) is True
    assert repo.upsert_quote(q, "MORNING", date(2099, 1, 1)) is False
    q2 = Quote(Instrument.COMEX_GC, 4001.0, "USD", "pytest", ts + timedelta(minutes=1))
    assert repo.upsert_quote(q2, "MORNING", date(2099, 1, 1)) is False   # same slot/day: no second row


def test_predictions_are_append_only():
    repo = SqlRepository(URL)
    with repo.engine.begin() as c:
        with pytest.raises(Exception, match="append-only"):
            c.execute(text("UPDATE gold_price_predictions SET direction='UP'"))
