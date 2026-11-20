"""Idempotent CSV and JSON helpers. Writes go to a temp file, then replace."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def read_csv(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path | str, rows: list[dict], fieldnames: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})
    tmp.replace(path)


def upsert_csv(path: Path | str, rows: list[dict], key: list[str], fieldnames: list[str]) -> list[dict]:
    """Insert or replace rows by ``key``. Existing columns not in the new row are kept.

    A blank value does not wipe a value that was already stored. That lets a re-run
    refresh an estimate without deleting an actual that has already been scored.
    """
    existing = read_csv(path)
    index: dict[tuple, dict] = {}
    order: list[tuple] = []
    for row in existing:
        ident = tuple(str(row.get(field, "")) for field in key)
        if ident not in index:
            order.append(ident)
        index[ident] = row
    for row in rows:
        ident = tuple(str(row.get(field, "")) for field in key)
        if ident not in index:
            order.append(ident)
            index[ident] = {}
        merged = dict(index[ident])
        for field in fieldnames:
            incoming = row.get(field, "")
            if incoming not in ("", None) or field not in merged or merged.get(field) in ("", None):
                merged[field] = "" if incoming is None else incoming
        index[ident] = merged
    out = [index[ident] for ident in order]
    write_csv(path, out, fieldnames)
    return out


def read_json(path: Path | str, default):
    path = Path(path)
    if not path.exists() or not path.read_text().strip():
        return default
    return json.loads(path.read_text())


def write_json(path: Path | str, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
