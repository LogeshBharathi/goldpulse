"""Load config.yaml. Paths in the file are relative to the repository root."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.yaml"
    with path.open() as fh:
        cfg = yaml.safe_load(fh)
    cfg["_root"] = str(path.parent if path.name == "config.yaml" else ROOT)
    return cfg


def repo_path(cfg: dict, key: str) -> Path:
    """Resolve a path from cfg['paths'] or a dotted source key against the repo root."""
    root = Path(cfg.get("_root", ROOT))
    if key in cfg.get("paths", {}):
        return root / cfg["paths"][key]
    return root / key
