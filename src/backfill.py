"""One-off history load. Not part of the daily schedule.

    python -m src.backfill path/to/board.csv

The CSV needs columns date, rate_22k_1g. About two years of hourly international
prices are downloaded once and cached at data/intl_hourly.csv.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from src.config import load_config, repo_path
from src.market import IST, fetch_hourly
from src.scrape_board import BOARD_FIELDS
from src.storage import read_csv, upsert_csv


def import_board(cfg: dict, csv_path: Path) -> int:
    incoming = read_csv(csv_path)
    if not incoming:
        raise RuntimeError(f"{csv_path} has no rows")
    now = datetime.now(IST).isoformat(timespec="seconds")
    rows = []
    for row in incoming:
        if "date" not in row or "rate_22k_1g" not in row:
            raise RuntimeError("board CSV needs columns date, rate_22k_1g")
        rows.append({"date": row["date"], "source": row.get("source") or "backfill",
                     "rate_22k_1g": f"{float(row['rate_22k_1g']):.2f}",
                     "scraped_at_ist": row.get("scraped_at_ist") or now})
    upsert_csv(repo_path(cfg, "board"), rows, key=["date"], fieldnames=BOARD_FIELDS)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill board rates and international prices")
    parser.add_argument("board_csv", type=Path, help="CSV with columns date, rate_22k_1g")
    args = parser.parse_args(argv)
    cfg = load_config()
    try:
        count = import_board(cfg, args.board_csv)
        points = fetch_hourly(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {count} board days and {len(points)} hourly bars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
