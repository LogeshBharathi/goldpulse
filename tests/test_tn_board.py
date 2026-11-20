"""BankBazaar Chennai board parser and the intraday lookup used for tomorrow's estimate. No network."""
from datetime import date, datetime, timedelta, timezone

import pytest

from goldpulse.providers.base import ProviderError
from goldpulse.providers.public_feeds import value_at, yahoo_intraday
from goldpulse.providers.tn_board import parse_bankbazaar_history

PAGE = """
<table><tr><th>Gram</th><th>Today</th><th>Yesterday</th></tr>
<tr><td>1 gram</td><td>&#8377; 11,980</td><td>&#8377; 12,215</td></tr></table>
<table>
<tr><th>Date</th><th>Standard Gold (22K) (8 grams)</th><th>Pure Gold (24K) (8 grams)</th></tr>
<tr><td>19 Nov 2026</td><td>₹ 95,840<span>(1,880 ▼)</span></td><td>₹ 1,00,632</td></tr>
<tr><td>18 Nov 2026</td><td>₹ 97,720(2,040 ▼)</td><td>₹ 1,02,608</td></tr>
<tr><td>15 Nov 2026</td><td>₹ 1,00,920(0)</td><td>₹ 1,05,968(0)</td></tr>
</table>
<table><tr><th>Month</th><th>22K Price (Rs./g)</th></tr><tr><td>Oct-26</td><td>Rs.13,217</td></tr></table>
"""


def test_parse_bankbazaar_history_reads_8_gram_22k_rows_as_per_gram():
    assert parse_bankbazaar_history(PAGE) == [
        (date(2026, 11, 19), 11_980.0), (date(2026, 11, 18), 12_215.0), (date(2026, 11, 15), 12_615.0)]


def test_parse_bankbazaar_history_errors_without_table():
    with pytest.raises(ProviderError):
        parse_bankbazaar_history("<table><tr><td>nothing</td></tr></table>")


def _bars(start: datetime, closes):
    return {"chart": {"result": [{
        "timestamp": [int((start + timedelta(minutes=5 * i)).timestamp()) for i in range(len(closes))],
        "indicators": {"quote": [{"close": closes}]},
    }]}}


def test_yahoo_intraday_skips_missing_closes():
    start = datetime(2026, 11, 19, 3, 50, tzinfo=timezone.utc)
    pts = yahoo_intraday(_bars(start, [3716.9, None, 3727.0]))
    assert [v for _, v in pts] == [3716.9, 3727.0]


def test_value_at_takes_last_bar_before_time_within_gap():
    start = datetime(2026, 11, 19, 3, 50, tzinfo=timezone.utc)  # 09:20 IST
    pts = yahoo_intraday(_bars(start, [3716.9, 3721.3, 3727.0, 3726.2]))
    nine_thirty_ist = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
    assert value_at(pts, nine_thirty_ist) == 3727.0
    assert value_at(pts, start - timedelta(minutes=1)) is None
    assert value_at(pts, nine_thirty_ist + timedelta(hours=2)) is None
    assert value_at(pts, nine_thirty_ist + timedelta(hours=2), timedelta(days=3)) == 3726.2
