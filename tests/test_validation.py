from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.market import move_since_reference
from src.scrape_board import collect, parse_bankbazaar, parse_goodreturns, save_board, select_rate
from src.storage import read_csv

IST = ZoneInfo("Asia/Kolkata")
BANK = """
<table><tr><th>Date</th><th>Standard Gold (22K) (8 grams)</th><th>Pure Gold (24K) (8 grams)</th></tr>
<tr><td>20 Nov 2026</td><td>₹ 92,080 (3,760 ▼)</td><td>₹ 1,00,456</td></tr>
<tr><td>19 Nov 2026</td><td>₹ 95,840</td><td>₹ 1,04,552</td></tr></table>
"""
GOOD = """
<table><tr><th>Gram</th><th>24K</th><th>22K</th><th>18K</th></tr>
<tr><td>1</td><td>₹12,557</td><td>₹11,510</td><td>₹9,418</td></tr></table>
"""


def test_bankbazaar_8_grams_becomes_1_gram_and_must_be_today():
    day = datetime(2026, 11, 20).date()
    assert parse_bankbazaar(BANK, day) == 11510
    assert parse_bankbazaar(BANK, datetime(2026, 11, 21).date()) is None


def test_goodreturns_1_gram_22k():
    assert parse_goodreturns(GOOD) == 11510


def test_jump_is_rejected_and_disagreement_is_flagged():
    cfg = load_config()
    with pytest.raises(ValueError):
        select_rate([("bankbazaar", 10000)], previous=11510, cfg=cfg)
    source, rate, flags = select_rate(
        [("bankbazaar", 11510), ("goodreturns", 11300)], previous=11500, cfg=cfg)
    assert (source, rate) == ("bankbazaar", 11510)
    assert flags and "differ" in flags[0]


def test_dindigul_is_used_when_chennai_pages_fail(tmp_path):
    cfg = load_config()
    cfg["_root"] = str(tmp_path)
    cfg["paths"] = {**cfg["paths"], "board": "board.csv"}
    cfg["sources"] = {**cfg["sources"], "manual_file": "manual_board.json"}
    (tmp_path / "board.csv").write_text(
        "date,source,rate_22k_1g,scraped_at_ist\n"
        "2026-11-20,legacy,11510.00,2026-11-20T10:24:41+05:30\n"
    )
    (tmp_path / "manual_board.json").write_text("{}\n")
    day = datetime(2026, 11, 21).date()

    def fetch(url):
        if "dindigul" in url:
            return 200, GOOD
        return 503, "<html>unavailable</html>"

    source, rate, flags = collect(cfg, day, fetch=fetch)
    assert (source, rate, flags) == ("goodreturns-dindigul", 11510.0, [])


def test_manual_file_is_saved_when_both_sites_fail(tmp_path):
    cfg = load_config()
    cfg["_root"] = str(tmp_path)
    cfg["paths"] = {**cfg["paths"], "board": "board.csv"}
    cfg["sources"] = {**cfg["sources"], "manual_file": "manual_board.json"}
    (tmp_path / "board.csv").write_text(
        "date,source,rate_22k_1g,scraped_at_ist\n"
        "2026-11-20,legacy,11510.00,2026-11-20T10:24:41+05:30\n"
    )
    (tmp_path / "manual_board.json").write_text('{"2026-11-21": 11520}\n')
    day = datetime(2026, 11, 21).date()

    def fetch(_url):
        return 503, "<html>unavailable</html>"

    source, rate, _flags = collect(cfg, day, fetch=fetch)
    assert (source, rate) == ("manual", 11520.0)
    save_board(cfg, day.isoformat(), source, rate, datetime(2026, 11, 21, 10, 30, tzinfo=IST))
    saved = read_csv(tmp_path / "board.csv")
    assert saved[-1]["date"] == "2026-11-21"
    assert saved[-1]["source"] == "manual"
    assert saved[-1]["rate_22k_1g"] == "11520.00"


def test_friday_reaches_saturday_sunday_is_flat_and_monday_reopens():
    points = [
        (datetime(2026, 11, 13, 9, 0, tzinfo=IST), 100.0),   # Friday reference
        (datetime(2026, 11, 13, 22, 0, tzinfo=IST), 110.0),  # Friday evening
        (datetime(2026, 11, 14, 2, 0, tzinfo=IST), 120.0),   # early Saturday close
        (datetime(2026, 11, 16, 5, 0, tzinfo=IST), 90.0),    # Monday reopen
    ]
    friday = datetime(2026, 11, 13, 9, 0, tzinfo=IST)
    friday_evening = move_since_reference(points, friday, datetime(2026, 11, 13, 22, 0, tzinfo=IST))
    saturday = move_since_reference(points, friday, datetime(2026, 11, 14, 5, 45, tzinfo=IST))
    sunday = move_since_reference(
        points, datetime(2026, 11, 14, 9, 0, tzinfo=IST), datetime(2026, 11, 15, 5, 45, tzinfo=IST))
    monday = move_since_reference(
        points, datetime(2026, 11, 15, 9, 0, tzinfo=IST), datetime(2026, 11, 16, 5, 45, tzinfo=IST))
    assert friday_evening == pytest.approx(0.10)
    assert saturday == pytest.approx(0.20)
    assert sunday == 0
    assert monday == pytest.approx(90 / 120 - 1)
