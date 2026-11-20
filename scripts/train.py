"""Train, evaluate (walk-forward) and save a model bundle.

  python scripts/train.py --csv data/my_licensed_history.csv
  python scripts/train.py --csv data/synthetic_history.csv --allow-synthetic     # pipeline test only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goldpulse.config import get_settings  # noqa: E402
from goldpulse.ml.registry import save_bundle, train  # noqa: E402
from goldpulse.providers.csv_history import load_history_csv  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--allow-synthetic", action="store_true")
    ap.add_argument("--register-db", action="store_true", help="also register the version in PostgreSQL")
    a = ap.parse_args()
    s = get_settings()
    df = load_history_csv(a.csv, allow_synthetic=a.allow_synthetic)
    b = train(df, s.neutral_threshold_pct / 100, s.min_train_rows, s.interval_level, is_synthetic=a.allow_synthetic)
    path = save_bundle(b, s.model_dir)
    if a.register_db:
        from goldpulse.db.sql_repository import SqlRepository
        repo = SqlRepository(s.database_url)
        repo.save_model_version(b.meta)
        if "metrics_walk_forward" in b.meta:
            repo.save_model_metrics(b.version, "walk_forward", b.meta["metrics_walk_forward"])
    print(f"saved {path}\nchosen model: {b.meta['chosen_model']}  backend: {b.meta['backend']}")
    for l in b.meta["limitations"]:
        print("LIMITATION:", l)
    if "metrics_walk_forward" in b.meta:
        print(json.dumps(b.meta["metrics_walk_forward"], indent=2))
    print(f"\nexport ACTIVE_MODEL_PATH={path}")


if __name__ == "__main__":
    main()
