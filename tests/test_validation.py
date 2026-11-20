from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.market import effective_move
from src.scrape_board import parse_bankbazaar, parse_goodreturns, select_rate

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


def test_weekend_move_is_zero_and_friday_move_is_kept():
    cfg = load_config()
    saturday = datetime(2026, 11, 21, 5, 45, tzinfo=IST)
    friday = datetime(2026, 11, 20, 22, 0, tzinfo=IST)
    assert effective_move(0.02, saturday, cfg) == 0
    assert effective_move(-0.014, friday, cfg) == -0.014
