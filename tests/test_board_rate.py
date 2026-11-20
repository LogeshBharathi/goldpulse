from datetime import date, datetime

import pytest

from goldpulse import board_rate as br

MCX = 128_432.0  # INR per 10 g, 999
NOW = datetime(2026, 11, 19, 21, 30)


def test_exchange_g22_converts_10g_999_to_1g_22k():
    assert br.exchange_g22(MCX) == pytest.approx(MCX / 10 * 0.916 / 0.999)


@pytest.mark.parametrize("text,value", [("11980", 11980), ("₹11,980", 11980), (" Rs 11980.50 ", 11980.5)])
def test_parse_rupees(text, value):
    assert br.parse_rupees(text) == value


@pytest.mark.parametrize("text", ["", "abc", "-5", "0"])
def test_parse_rupees_rejects(text):
    with pytest.raises(ValueError):
        br.parse_rupees(text)


def test_check_rate_accepts_board_near_exchange():
    br.check_rate(11_980, MCX)


def test_check_rate_spots_pavun_and_10_grams():
    with pytest.raises(ValueError, match="pavun"):
        br.check_rate(95_840, MCX)
    with pytest.raises(ValueError, match="10 grams"):
        br.check_rate(119_800, MCX)


def test_check_rate_rejects_far_off_number():
    with pytest.raises(ValueError, match="away from the exchange"):
        br.check_rate(9_000, MCX)


def test_merge_fetched_adds_days_and_keeps_typed_rate(tmp_path):
    path = tmp_path / "board.json"
    br.save_manual(path, board_date=date(2026, 11, 19), g22=11_990, saved_at=NOW)
    fetched = [(date(2026, 11, 19), 11_980.0), (date(2026, 11, 18), 12_215.0)]
    assert br.merge_fetched(path, fetched, "BankBazaar", NOW) == 1
    assert br.merge_fetched(path, fetched, "BankBazaar", NOW) == 0
    rows = br.load_all(path)
    assert [(r["board_date"], r["g22"], r["source"]) for r in rows] == [
        ("2026-11-18", 12_215.0, "BankBazaar"), ("2026-11-19", 11_990.0, br.USER)]


def test_latest_previous_and_extremes():
    rows = [{"board_date": d, "g22": g} for d, g in
            [("2026-11-10", 13_210.0), ("2026-11-18", 12_215.0), ("2026-11-19", 11_980.0)]]
    assert br.latest(rows, date(2026, 11, 19))["g22"] == 11_980
    assert br.latest(rows, date(2026, 11, 18))["g22"] == 12_215
    assert br.previous(rows, date(2026, 11, 19))["g22"] == 12_215
    assert br.previous(rows, date(2026, 11, 10)) is None
    hi, lo = br.extremes(rows)
    assert (hi["board_date"], lo["board_date"]) == ("2026-11-10", "2026-11-19")
    assert br.extremes([]) is None


def test_international_move_and_expected():
    move = br.international_move(3727.0, 93.467, 3677.0, 93.28)
    assert move == pytest.approx(3677.0 * 93.28 / (3727.0 * 93.467) - 1)
    assert br.expected_g22(11_980, move) == pytest.approx(11_980 * (1 + move))


def test_expected_applies_pass_through():
    assert br.expected_g22(10_000, -0.05, 0.8) == pytest.approx(9_600)


def test_fit_pass_through_recovers_slope_and_needs_history():
    intl = [-0.02, -0.015, -0.01, 0.005, -0.03, 0.01, -0.005]
    pairs = [(0.8 * i, i) for i in intl]
    slope, sd = br.fit_pass_through(pairs)
    assert slope == pytest.approx(0.8) and sd == pytest.approx(0, abs=1e-12)
    assert br.fit_pass_through(pairs[:br.MIN_FIT_DAYS - 1]) is None
    assert br.fit_pass_through([(0.0, 0.0)] * 10) is None


def test_fit_pass_through_is_clamped():
    pairs = [(3 * i, i) for i in [-0.02, -0.01, 0.01, 0.02, -0.03, 0.015]]
    assert br.fit_pass_through(pairs)[0] == br.PASS_THROUGH_LIMITS[1]


def test_error_band_uses_root_mean_square_of_past_misses():
    errors = [0.01, -0.01] * 4
    lo, hi = br.error_band(10_000, errors)
    assert hi - 10_000 == pytest.approx(10_000 * br.BAND_Z * 0.01)
    assert 10_000 - lo == pytest.approx(hi - 10_000)
    assert br.error_band(10_000, errors[:br.MIN_FIT_DAYS - 1]) is None


def test_record_estimate_allows_missing_range(tmp_path):
    path = tmp_path / "est.json"
    br.record_estimate(path, target=date(2026, 11, 21), g22=11_400, low=None, high=None,
                       made_at=datetime(2026, 11, 20, 20, 0), cutoff=datetime(2026, 11, 21, 6, 0))
    assert br.load_estimates(path)["2026-11-21"]["low"] is None


def test_record_estimate_keeps_latest_before_cutoff(tmp_path):
    path = tmp_path / "est.json"
    cutoff = datetime(2026, 11, 21, 6, 0)
    kw = dict(target=date(2026, 11, 21), low=1, high=2, cutoff=cutoff)
    br.record_estimate(path, g22=11_400, made_at=datetime(2026, 11, 20, 20, 0), **kw)
    br.record_estimate(path, g22=11_450, made_at=datetime(2026, 11, 20, 23, 0), **kw)
    br.record_estimate(path, g22=11_000, made_at=datetime(2026, 11, 21, 7, 0), **kw)
    assert br.load_estimates(path)["2026-11-21"]["g22"] == 11_450


def test_per_unit_matches_board_tables():
    assert br.per_unit(11_980, 8) == 95_840
    assert br.per_unit(11_980, 10) == 1_19_800
    assert br.per_unit(11_979.6, 100) == 11_98_000


def test_g24_from_g22_matches_published_ratio():
    assert br.g24_from_g22(11_980) == pytest.approx(13_065, abs=1)
