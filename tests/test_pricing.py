import pytest

from goldpulse.pricing import (PricingAssumptions, TROY_OUNCE_GRAMS, implied_domestic_premium_pct,
                               parity_inr_per_10g, pct_change, retail_from_mcx,
                               usd_per_oz_to_inr_per_gram)


def test_usd_oz_to_inr_gram():
    # $3110.34768/oz at 80 INR = exactly 100 USD/g -> 8000 INR/g
    assert usd_per_oz_to_inr_per_gram(3110.34768, 80) == pytest.approx(8000.0)


def test_parity_with_duty():
    a = PricingAssumptions(import_duty_pct=10.0, domestic_premium_pct=0.0)
    base10g = 2000 / TROY_OUNCE_GRAMS * 80 * 10
    assert parity_inr_per_10g(2000, 80, a) == pytest.approx(base10g * 1.10)


def test_implied_premium_roundtrip():
    a = PricingAssumptions(import_duty_pct=6.0, domestic_premium_pct=2.0)
    p = parity_inr_per_10g(2000, 80, a)
    assert implied_domestic_premium_pct(p, 2000, 80, 6.0) == pytest.approx(2.0, abs=1e-9)


def test_retail_conversion_explicit_assumptions():
    a = PricingAssumptions(gst_pct=3.0, retail_margin_pct=0.0, making_charges_pct=0.0)
    q = retail_from_mcx(100000, a)  # 10,000 INR/g at 999 purity
    assert q.mcx_per_gram == 10000.0
    assert q.per_gram_24k == pytest.approx(10000 * 1.03)
    assert q.per_gram_22k == pytest.approx(10000 * 0.916 / 0.999 * 1.03, abs=0.01)
    assert q.assumptions["gst_pct"] == 3.0 and "purity_22k" in q.assumptions


def test_retail_is_not_mcx_price():
    q = retail_from_mcx(100000, PricingAssumptions(retail_margin_pct=1.0))
    assert q.per_gram_24k != q.mcx_per_gram


@pytest.mark.parametrize("bad", [(0, 80), (2000, 0), (-1, 80)])
def test_invalid_inputs(bad):
    with pytest.raises(ValueError):
        usd_per_oz_to_inr_per_gram(*bad)


def test_pct_change():
    assert pct_change(101, 100) == pytest.approx(1.0)
    with pytest.raises(ZeroDivisionError):
        pct_change(1, 0)
