"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

J = pg.JSONB()
P = sa.Numeric(18, 6)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "market_data_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("licensed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_delayed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("delay_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("license_notes", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "market_data",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("market_data_sources.id"), nullable=False),
        sa.Column("instrument", sa.String(20), nullable=False),
        sa.Column("market_id", sa.String(20), nullable=False, server_default=""),
        sa.Column("contract_key", sa.String(30), nullable=False, server_default="SPOT"),
        sa.Column("contract_expiry", sa.Date),
        sa.Column("price", P, nullable=False),
        sa.Column("open", P), sa.Column("high", P), sa.Column("low", P), sa.Column("prev_close", P),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("observed_at_market", TS, nullable=False),
        sa.Column("observed_at_utc", TS, nullable=False),
        sa.Column("observed_at_ist", TS, nullable=False),
        sa.Column("ingested_at_utc", TS, server_default=sa.func.now()),
        sa.Column("slot", sa.String(10)),
        sa.Column("slot_date", sa.Date),
        sa.Column("is_delayed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("delay_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_status", sa.String(10), nullable=False),
        sa.Column("quality_notes", J),
        sa.Column("raw", J),
        sa.UniqueConstraint("source_id", "instrument", "contract_key", "observed_at_utc", name="uq_market_obs"),
        sa.UniqueConstraint("instrument", "slot", "slot_date", name="uq_market_slot"),
        sa.CheckConstraint("price > 0", name="ck_market_price_pos"),
    )
    op.create_index("ix_market_instr_time", "market_data", ["instrument", "observed_at_utc"])

    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("version", sa.String(120), nullable=False, unique=True),
        sa.Column("model_name", sa.String(40), nullable=False),
        sa.Column("backend", sa.String(120)),
        sa.Column("trained_through", sa.Date),
        sa.Column("n_train_rows", sa.Integer, nullable=False),
        sa.Column("feature_list", J, nullable=False),
        sa.Column("params", J),
        sa.Column("data_fingerprint", sa.String(64)),
        sa.Column("artifact_path", sa.Text),
        sa.Column("calibrated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_synthetic", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("limitations", J),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "model_metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model_version_id", sa.Integer, sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("regime", sa.String(10), nullable=False, server_default="all"),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("value", sa.Float),
        sa.Column("n_obs", sa.Integer),
        sa.Column("window_end", sa.Date),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.UniqueConstraint("model_version_id", "scope", "regime", "name", "window_end", name="uq_model_metric"),
    )
    op.create_table(
        "prediction_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("target_trading_date", sa.Date, nullable=False),
        sa.Column("model_version_id", sa.Integer, sa.ForeignKey("model_versions.id"), nullable=False),
        sa.Column("run_seq", sa.Integer, nullable=False, server_default="1"),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="api"),
        sa.Column("status", sa.String(12), nullable=False, server_default="OK"),
        sa.Column("input_snapshot", J),
        sa.Column("data_quality", J),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.UniqueConstraint("target_trading_date", "model_version_id", "run_seq", name="uq_run"),
    )
    op.create_table(
        "gold_price_predictions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("prediction_runs.id"), nullable=False, unique=True),
        sa.Column("target_trading_date", sa.Date, nullable=False),
        sa.Column("previous_mcx_close", P, nullable=False),
        sa.Column("expected_return_pct", sa.Float, nullable=False),
        sa.Column("predicted_mcx_price", P, nullable=False),
        sa.Column("interval_low", P, nullable=False),
        sa.Column("interval_high", P, nullable=False),
        sa.Column("interval_level", sa.Float, nullable=False),
        sa.Column("interval_basis", sa.Text, nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("direction_raw_scores", J),
        sa.Column("confidence", sa.Float),
        sa.Column("retail_24k_per_g", P, nullable=False),
        sa.Column("retail_22k_per_g", P, nullable=False),
        sa.Column("assumptions", J, nullable=False),
        sa.Column("payload", J, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.CheckConstraint("direction IN ('UP','DOWN','NEUTRAL')", name="ck_direction"),
    )
    op.create_index("ix_pred_target", "gold_price_predictions", ["target_trading_date"])
    # Append-only guard: predictions can never be updated or deleted.
    op.execute("""
        CREATE OR REPLACE FUNCTION goldpulse_block_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'gold_price_predictions is append-only'; END; $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_pred_append_only BEFORE UPDATE OR DELETE ON gold_price_predictions
        FOR EACH STATEMENT EXECUTE FUNCTION goldpulse_block_mutation();
    """)
    op.create_table(
        "actual_market_prices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("instrument", sa.String(20), nullable=False),
        sa.Column("contract_key", sa.String(30), nullable=False, server_default="SPOT"),
        sa.Column("close_price", P, nullable=False),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("market_data_sources.id"), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.UniqueConstraint("trading_date", "instrument", "contract_key", "source_id", name="uq_actual"),
    )
    op.create_table(
        "prediction_evaluations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("prediction_id", sa.Integer, sa.ForeignKey("gold_price_predictions.id"), nullable=False, unique=True),
        sa.Column("actual_price_id", sa.Integer, sa.ForeignKey("actual_market_prices.id")),
        sa.Column("actual_close", P, nullable=False),
        sa.Column("abs_error", sa.Float, nullable=False),
        sa.Column("pct_error", sa.Float, nullable=False),
        sa.Column("predicted_direction", sa.String(8), nullable=False),
        sa.Column("actual_direction", sa.String(8), nullable=False),
        sa.Column("direction_correct", sa.Boolean, nullable=False),
        sa.Column("within_interval", sa.Boolean, nullable=False),
        sa.Column("payload", J, nullable=False),
        sa.Column("evaluated_at", TS, server_default=sa.func.now()),
    )
    op.create_table(
        "automation_logs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("workflow", sa.String(40), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("payload", J),
        sa.Column("execution_id", sa.String(60)),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_automation_logs_workflow", "automation_logs", ["workflow"])
    op.create_index("ix_automation_logs_created_at", "automation_logs", ["created_at"])


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_pred_append_only ON gold_price_predictions;"
               "DROP FUNCTION IF EXISTS goldpulse_block_mutation();")
    for t in ["automation_logs", "prediction_evaluations", "actual_market_prices", "gold_price_predictions",
              "prediction_runs", "model_metrics", "model_versions", "market_data", "market_data_sources"]:
        op.drop_table(t)
