"""Gold price conversions for the Indian market. All assumptions are explicit.

MCX GOLD futures are quoted in INR per 10 grams (999 purity, ex-GST).
Jewellery retail is NOT the MCX price: it adds a retailer margin, GST, and
(optionally) making charges, and 22K is 91.6% pure vs 99.9% for 24K.
Every default below is a placeholder for the operator to verify/calibrate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

TROY_OUNCE_GRAMS = 31.1034768


@dataclass(frozen=True)
class PricingAssumptions:
    import_duty_pct: float = 6.0        # PLACEHOLDER – verify current rate
    gst_pct: float = 3.0                # GST on gold value
    domestic_premium_pct: float = 0.0   # premium(+)/discount(-) of domestic vs. parity
    retail_margin_pct: float = 0.0      # jeweller markup on metal value, excl. making charges
    making_charges_pct: float = 0.0     # excluded by default; set if you want it in the quote
    purity_24k: float = 0.999
    purity_22k: float = 0.916

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetailQuote:
    per_gram_24k: float
    per_gram_22k: float
    mcx_per_gram: float
    assumptions: dict
    notes: str = ("Retail estimate = MCX value x (1+retail margin) [+ making] x (1+GST). "
                  "22K scaled by purity ratio. Actual shop prices differ by city and jeweller.")


def usd_per_oz_to_inr_per_gram(usd_per_oz: float, usdinr: float) -> float:
    if usd_per_oz <= 0 or usdinr <= 0:
        raise ValueError("Prices and FX must be positive")
    return usd_per_oz / TROY_OUNCE_GRAMS * usdinr


def parity_inr_per_10g(usd_per_oz: float, usdinr: float, a: PricingAssumptions) -> float:
    """Theoretical landed domestic price per 10g (999 purity), ex-GST."""
    base = usd_per_oz_to_inr_per_gram(usd_per_oz, usdinr)
    return base * (1 + a.import_duty_pct / 100) * (1 + a.domestic_premium_pct / 100) * 10


def implied_domestic_premium_pct(mcx_per_10g: float, usd_per_oz: float, usdinr: float,
                                 import_duty_pct: float) -> float:
    """Diagnostic: how far MCX sits from duty-inclusive parity (basis/premium proxy)."""
    parity = usd_per_oz_to_inr_per_gram(usd_per_oz, usdinr) * (1 + import_duty_pct / 100) * 10
    return (mcx_per_10g / parity - 1) * 100


def retail_from_mcx(mcx_per_10g: float, a: PricingAssumptions) -> RetailQuote:
    if mcx_per_10g <= 0:
        raise ValueError("MCX price must be positive")
    g = mcx_per_10g / 10.0  # 999 purity
    mult = (1 + a.retail_margin_pct / 100) * (1 + a.making_charges_pct / 100) * (1 + a.gst_pct / 100)
    p24 = g * mult
    p22 = g * (a.purity_22k / a.purity_24k) * mult
    return RetailQuote(round(p24, 2), round(p22, 2), round(g, 2), a.as_dict())


def pct_change(new: float, old: float) -> float:
    if old == 0:
        raise ZeroDivisionError("old price is zero")
    return (new / old - 1) * 100
