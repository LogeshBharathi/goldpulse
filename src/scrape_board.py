"""Today's Tamil Nadu 22K board, rupees per gram, before GST.

BankBazaar's Chennai page is read first. GoodReturns is the fallback. A date in
data/manual_board.json wins only when both pages fail validation. A rate more
than 5% from the previous saved board is rejected, logged, and not saved, so it
can never be learned from. Two sources more than 0.5% apart are flagged.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.config import load_config, repo_path
from src.market import IST
from src.storage import read_csv, read_json, upsert_csv

BOARD_FIELDS = ["date", "source", "rate_22k_1g", "scraped_at_ist"]
_RUPEES = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    tables = []
    for table in soup.find_all("table"):
        parsed = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                parsed.append(cells)
        if parsed:
            tables.append(parsed)
    return tables


def _rupees(text: str) -> float | None:
    match = _RUPEES.search(text.replace("\xa0", " "))
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def parse_bankbazaar(html: str, expect_date=None) -> float | None:
    """Newest 22K 8-gram row, converted to 1 gram. None if that row is not ``expect_date``."""
    for table in _rows(html):
        header = table[0]
        if not header or "date" not in header[0].lower():
            continue
        if len(header) < 2 or "22k" not in header[1].lower() or "8 gram" not in header[1].lower():
            continue
        for cells in table[1:]:
            try:
                day = datetime.strptime(cells[0].strip(), "%d %b %Y").date()
            except (ValueError, IndexError):
                break
            amount = _rupees(cells[1]) if len(cells) > 1 else None
            if amount is None:
                break
            if expect_date is not None and day != expect_date:
                return None
            return amount / 8
    return None


def parse_goodreturns(html: str, expect_date=None) -> float | None:
    """1 gram 22K from the weight table, or the newest dated history row."""
    for table in _rows(html):
        header = [c.lower() for c in table[0]]
        if header[:1] == ["date"] and any("22k" in c for c in header):
            col = next(i for i, c in enumerate(header) if "22k" in c)
            for cells in table[1:]:
                try:
                    day = datetime.strptime(cells[0].strip(), "%b %d, %Y").date()
                except ValueError:
                    try:
                        day = datetime.strptime(cells[0].strip(), "%d %b %Y").date()
                    except ValueError:
                        break
                amount = _rupees(cells[col]) if len(cells) > col else None
                if amount is None:
                    break
                if expect_date is not None and day != expect_date:
                    return None
                return amount
        if header[:1] == ["gram"] and any(c == "22k" for c in header):
            col = header.index("22k")
            for cells in table[1:]:
                if cells and cells[0].strip() in {"1", "1 gram"}:
                    return _rupees(cells[col]) if len(cells) > col else None
    return None


def accept_rate(rate: float | None, previous: float | None, cfg: dict, source: str) -> float | None:
    """Reject an empty, absurd, or jumped rate. Rejections are logged and not returned."""
    if rate is None:
        return None
    low, high = cfg["validation"]["min_rate"], cfg["validation"]["max_rate"]
    if not low <= rate <= high:
        _log(f"rejected {source}: {rate} is outside {low}-{high}")
        return None
    jump = cfg["validation"]["max_jump_pct"]
    if previous is not None and previous > 0 and abs(rate / previous - 1) * 100 > jump:
        _log(f"rejected {source}: {rate:.0f} is {abs(rate / previous - 1) * 100:.1f}% "
             f"from the previous board {previous:.0f} (limit {jump}%)")
        return None
    return rate


def select_rate(candidates: list[tuple[str, float | None]], previous: float | None, cfg: dict):
    """First source that passes validation. Flags a disagreement above the threshold.

    Returns (source, rate, flags) or raises ValueError when nothing is usable.
    """
    valid = []
    for source, rate in candidates:
        kept = accept_rate(rate, previous, cfg, source)
        if kept is not None:
            valid.append((source, kept))
    if not valid:
        raise ValueError("no valid Tamil Nadu board rate")
    flags = []
    if len(valid) >= 2:
        first, second = valid[0][1], valid[1][1]
        gap = abs(first / second - 1) * 100
        if gap > cfg["validation"]["source_disagreement_pct"]:
            flags.append(f"sources differ by {gap:.2f}% ({valid[0][0]} {first:.0f}, {valid[1][0]} {second:.0f})")
            _log("flag: " + flags[0])
    return valid[0][0], valid[0][1], flags


def _get(url: str, cfg: dict) -> tuple[int | None, str]:
    """Return (HTTP status, body). Status is None when the connection itself failed."""
    headers = {"User-Agent": cfg["sources"]["user_agent"], "Accept": "text/html"}
    delay = cfg["sources"]["backoff_seconds"]
    last: Exception | None = None
    last_status: int | None = None
    last_body = ""
    for attempt in range(cfg["sources"]["retries"]):
        try:
            response = requests.get(url, headers=headers, timeout=cfg["sources"]["timeout_seconds"])
            body = response.content.decode("utf-8", "replace")
            if response.status_code >= 400:
                last = RuntimeError(f"HTTP {response.status_code}")
                last_status, last_body = response.status_code, body
            else:
                return response.status_code, body
        except Exception as exc:  # noqa: BLE001
            last = exc
            last_status, last_body = None, ""
        time.sleep(delay * (2 ** attempt))
    detail = f"{last}; body: {last_body[:200]!r}"
    raise RuntimeError(detail) from last


def _log_source_failure(source: str, url: str, status, body: str) -> None:
    snippet = " ".join((body or "").split())[:200]
    _log(f"FAILED {source}: HTTP {status if status is not None else 'no response'} {url}")
    _log(f"FAILED {source} body: {snippet}")


def previous_board(cfg: dict) -> tuple[str | None, float | None]:
    rows = read_csv(repo_path(cfg, "board"))
    if not rows:
        return None, None
    last = rows[-1]
    return last["date"], float(last["rate_22k_1g"])


def already_saved(cfg: dict, day: str) -> bool:
    return any(row["date"] == day for row in read_csv(repo_path(cfg, "board")))


def save_board(cfg: dict, day: str, source: str, rate: float, when: datetime) -> None:
    upsert_csv(repo_path(cfg, "board"), [{
        "date": day, "source": source, "rate_22k_1g": f"{rate:.2f}",
        "scraped_at_ist": when.isoformat(timespec="seconds"),
    }], key=["date"], fieldnames=BOARD_FIELDS)


def _read_source(source: str, url: str, parser, day, cfg: dict, fetch) -> float | None:
    try:
        fetched = fetch(url) if fetch else _get(url, cfg)
        if isinstance(fetched, tuple):
            status, html = fetched
        else:
            status, html = 200, fetched
    except Exception as exc:  # noqa: BLE001
        _log_source_failure(source, url, None, str(exc))
        return None
    if status is None or status >= 400:
        _log_source_failure(source, url, status, html)
        return None
    rate = parser(html, day)
    if rate is None:
        _log_source_failure(source, url, status, html)
    return rate


def collect(cfg: dict, day, fetch=None) -> tuple[str, float, list[str]]:
    """``fetch(url)`` returns HTML, or (status, HTML). The manual file is read locally."""
    _prev_date, previous = previous_board(cfg)
    manual = read_json(Path_manual(cfg), {})
    manual_rate = manual.get(day.isoformat())
    bank_url = cfg["sources"]["bankbazaar_url"]
    good_url = cfg["sources"]["goodreturns_url"]
    bank = _read_source("BankBazaar", bank_url, parse_bankbazaar, day, cfg, fetch)
    good = _read_source("GoodReturns", good_url, parse_goodreturns, day, cfg, fetch)
    if bank is None and good is None:
        _log("FAILED: BankBazaar and GoodReturns both failed. No website rate will be saved. "
             "See the two FAILED lines above for the HTTP status and the start of each response.")
    candidates = [("bankbazaar", bank), ("goodreturns", good),
                  ("manual", float(manual_rate) if manual_rate not in (None, "") else None)]
    return select_rate(candidates, previous, cfg)


def Path_manual(cfg: dict):
    from pathlib import Path
    return Path(cfg["_root"]) / cfg["sources"]["manual_file"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Save today's Tamil Nadu 22K board rate")
    parser.add_argument("--if-missing", action="store_true",
                        help="Exit immediately when today's board is already saved")
    args = parser.parse_args(argv)
    cfg = load_config()
    today = datetime.now(IST).date()
    if args.if_missing and already_saved(cfg, today.isoformat()):
        print(f"board for {today.isoformat()} already saved")
        return 0
    try:
        source, rate, flags = collect(cfg, today)
    except ValueError as exc:
        _log(str(exc))
        return 1
    save_board(cfg, today.isoformat(), source, rate, datetime.now(IST))
    print(f"saved {today.isoformat()} {source} {rate:.2f}" + (f" ({'; '.join(flags)})" if flags else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
