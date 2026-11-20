"""Human-readable report and Telegram message. Pure functions of a prediction dict."""
from __future__ import annotations

DISCLAIMER = "This is a statistical forecast, not a guaranteed future market price."


def _inr(x) -> str:
    return f"₹{x:,.2f}"


def _analysis(p: dict) -> tuple[list[str], list[str], list[str]]:
    i = p["indicators"]
    bull, bear = [], []
    for label, v, unit in [("COMEX overnight", i["comex_overnight_pct"], "%"),
                           ("XAU/USD overnight", i["xau_overnight_pct"], "%"),
                           ("USD/INR (rupee weaker)", i["usdinr_move_pct"], "%"),
                           ("5-day MCX momentum", i["momentum_5d_pct"], "%")]:
        (bull if v > 0 else bear if v < 0 else []).append(f"{label}: {v:+.2f}{unit}")
    reasons = [f"Expected move {p['expected_return_pct']:+.2f}% from the model "
               f"'{p['model']['chosen']}' (backend: {p['model']['backend']}).",
               f"Interval basis: {p['interval_basis']}."]
    return bull, bear, reasons


def render_report(p: dict) -> str:
    i, dq = p["indicators"], p["data_quality"]
    bull, bear, reasons = _analysis(p)
    conf = (f"{p['confidence']*100:.0f}% (statistically calibrated)" if p.get("confidence") is not None
            else "Not shown – model not statistically calibrated")
    lines = [
        "GOLDPULSE INDIA", f"Date: {p['target_date']}", "",
        "MARKET INDICATORS",
        f"- COMEX previous close: ${i['comex_prev_close']:,.2f}",
        f"- COMEX latest price: ${i['comex_latest']:,.2f}",
        f"- COMEX overnight movement: {i['comex_overnight_pct']:+.2f}%",
        f"- USD/INR: {i['usdinr']:.4f}",
        f"- USD/INR movement: {i['usdinr_move_pct']:+.2f}%",
        f"- XAU/USD: ${i['xauusd']:,.2f}",
        f"- Previous MCX close: {_inr(i['previous_mcx_close'])} /10g", "",
        "PREDICTION",
        f"- Expected direction: {p['direction']}",
        f"- Estimated MCX gold price: {_inr(p['predicted_mcx_price'])} /10g",
        f"- Expected percentage change: {p['expected_return_pct']:+.2f}%",
        f"- Expected 24K price per gram: {_inr(p['retail_24k_per_g'])}",
        f"- Expected 22K price per gram: {_inr(p['retail_22k_per_g'])}",
        f"- Prediction interval ({p['interval_level']*100:.0f}%): {_inr(p['interval_low'])} – {_inr(p['interval_high'])} /10g",
        f"- Model confidence: {conf}", "",
        "ANALYSIS",
        "- Main bullish indicators: " + ("; ".join(bull) or "none"),
        "- Main bearish indicators: " + ("; ".join(bear) or "none"),
        "- Important market events: none (no reliable event feed configured)",
        "- Data quality warnings: " + ("; ".join(dq["warnings"]) or "none"),
        "- Reasons supporting the forecast: " + " ".join(reasons),
        "- Retail assumptions: " + p["retail_notes"] +
        f" GST {p['pricing_assumptions']['gst_pct']}%, margin {p['pricing_assumptions']['retail_margin_pct']}%, "
        f"making charges {p['pricing_assumptions']['making_charges_pct']}%.", "",
        "DISCLAIMER", DISCLAIMER]
    return "\n".join(lines)


def render_telegram(p: dict) -> str:
    i, dq = p["indicators"], p["data_quality"]
    fresh = ", ".join(f"{k} {v['status']}" for k, v in dq["checks"].items())
    arrow = {"UP": "🔼", "DOWN": "🔽", "NEUTRAL": "➖"}[p["direction"]]
    warn = f"\n⚠️ {len(dq['warnings'])} data/model warning(s): {dq['warnings'][0]}" if dq["warnings"] else ""
    return (f"GOLDPULSE INDIA – {p['target_date']}\n"
            f"{arrow} Direction: {p['direction']}\n"
            f"MCX gold: {_inr(p['predicted_mcx_price'])}/10g ({p['expected_return_pct']:+.2f}%)\n"
            f"Range ({p['interval_level']*100:.0f}%): {_inr(p['interval_low'])} – {_inr(p['interval_high'])}\n"
            f"24K ≈ {_inr(p['retail_24k_per_g'])}/g | 22K ≈ {_inr(p['retail_22k_per_g'])}/g (estimate, excl. making charges)\n"
            f"COMEX overnight: {i['comex_overnight_pct']:+.2f}% | USD/INR: {i['usdinr']:.2f} ({i['usdinr_move_pct']:+.2f}%)\n"
            f"Data: {fresh}{warn}\n"
            f"{DISCLAIMER}")
