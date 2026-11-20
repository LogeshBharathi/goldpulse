"""FastAPI binding. Business logic lives in handlers.py / service.py.

NOTE: FastAPI was not installable in the authoring environment, so this file is
syntax-checked only. The identical logic is covered by tests/test_service_e2e.py via App handlers.
"""
from __future__ import annotations

import html
import os
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from goldpulse import board_rate
from goldpulse.api.handlers import ApiError, App
from goldpulse.config import get_settings
from goldpulse.db.sql_repository import SqlRepository
from goldpulse.ml.registry import load_bundle
from goldpulse.providers.base import Instrument
from goldpulse.providers.public_feeds import PublicFeedProvider, value_at
from goldpulse.providers.tn_board import SOURCE as TN_BOARD_SOURCE
from goldpulse.providers.tn_board import fetch_board_history
from goldpulse.providers.rest import PRESET_TWELVEDATA, QuoteMapping, RestQuoteProvider
from goldpulse.trading_calendar import load_india_calendar, load_us_calendar

settings = get_settings()
_app: App | None = None


def build_app() -> App:
    repo = SqlRepository(settings.database_url)
    model_path = os.environ.get("ACTIVE_MODEL_PATH", "")
    if not model_path:
        raise RuntimeError("ACTIVE_MODEL_PATH is not set. Train a model or point it at models/<version>.")
    bundle = load_bundle(model_path)
    repo.save_model_version(bundle.meta)
    providers = []
    if os.environ.get("TWELVEDATA_API_KEY"):
        providers.append(RestQuoteProvider("twelvedata", PRESET_TWELVEDATA,
                                           api_key=os.environ["TWELVEDATA_API_KEY"]))
    providers += _configured_futures_providers()
    if os.environ.get("PUBLIC_MARKET_DATA", "").lower() in ("1", "true", "yes"):
        providers.append(PublicFeedProvider())
    return App(repo, bundle, settings, load_india_calendar(settings.holiday_dir),
               load_us_calendar(settings.holiday_dir), providers)


def _configured_futures_providers() -> list:
    """COMEX/MCX providers from env: COMEX_API_URL / MCX_API_URL + *_PRICE_PATH, *_TS_PATH, *_PARAMS (JSON)."""
    import json
    out = []
    for inst, prefix, cur in [(Instrument.COMEX_GC, "COMEX", "USD"), (Instrument.MCX_GOLD, "MCX", "INR")]:
        url = os.environ.get(f"{prefix}_API_URL")
        if not url:
            continue
        m = QuoteMapping(url=url, params=json.loads(os.environ.get(f"{prefix}_API_PARAMS", "{}")),
                         price=os.environ.get(f"{prefix}_PRICE_PATH", "price"),
                         timestamp=os.environ.get(f"{prefix}_TS_EPOCH_PATH") or None,
                         datetime_str=os.environ.get(f"{prefix}_TS_PATH") or None,
                         source_tz=os.environ.get(f"{prefix}_TS_TZ", "UTC"), currency=cur, market_id=prefix,
                         is_delayed=os.environ.get(f"{prefix}_DELAYED", "true").lower() == "true",
                         delay_minutes=int(os.environ.get(f"{prefix}_DELAY_MINUTES", "10")))
        out.append(RestQuoteProvider(f"{prefix.lower()}-vendor", {inst: m},
                                     api_key=os.environ.get(f"{prefix}_API_KEY", "")))
    return out


def get_app() -> App:
    global _app
    if _app is None:
        _app = build_app()
    return _app


def auth(x_api_key: str = Header(default="")):
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "invalid API key")


api = FastAPI(title="GoldPulse India", version="1.0.0")
v1 = APIRouter(prefix="/api/v1", dependencies=[Depends(auth)])

def _inr(amount: float) -> str:
    """₹1,28,838 — Indian digit grouping."""
    n = int(round(amount))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return f"{sign}₹{s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while head:
        groups.append(head[-2:])
        head = head[:-2]
    return sign + "₹" + ",".join(reversed(groups)) + "," + tail


def _refresh_quotes(app: App) -> None:
    provider = next((p for p in app.providers if getattr(p, "name", "") == "public-indicative"), None)
    if provider is None:
        return
    for inst in Instrument:
        try:
            app.repo.upsert_quote(provider.fetch_latest(inst))
        except Exception:
            continue


def _price_view(app: App) -> dict:
    """Expected MCX price for a first-time reader.

    Uses the same parity idea as the selected model: yesterday's MCX close,
    moved by the change in international gold and the rupee since that close.
    """
    from sqlalchemy import select

    from goldpulse.db import models as m
    from goldpulse.pricing import retail_from_mcx
    from goldpulse.service import pricing_from_settings

    _refresh_quotes(app)

    def live(instrument: str):
        with app.repo.Session() as s:
            return s.execute(
                select(m.MarketData.price, m.MarketData.prev_close, m.MarketData.observed_at_ist)
                .where(m.MarketData.instrument == instrument, m.MarketData.slot.is_(None))
                .order_by(m.MarketData.observed_at_utc.desc()).limit(1)
            ).first()

    def last_close(instrument: str):
        with app.repo.Session() as s:
            return s.execute(
                select(m.MarketData.price, m.MarketData.slot_date)
                .where(m.MarketData.instrument == instrument, m.MarketData.slot == "CLOSE")
                .order_by(m.MarketData.slot_date.desc()).limit(1)
            ).first()

    mcx, comex, fx = live("MCX_GOLD"), live("COMEX_GC"), live("USDINR")
    if mcx is None or comex is None or fx is None:
        raise RuntimeError("Live gold prices are not loaded yet.")
    comex_prev = float(comex.prev_close) if comex.prev_close else None
    if comex_prev is None and (row := last_close("COMEX_GC")):
        comex_prev = float(row.price)
    fx_prev_row = last_close("USDINR")
    fx_prev = float(fx_prev_row.price) if fx_prev_row else None
    yesterday = float(mcx.prev_close) if mcx.prev_close else None
    if comex_prev is None or fx_prev is None or yesterday is None:
        raise RuntimeError("Yesterday's close is missing, so an expected price cannot be calculated.")

    comex_move = float(comex.price) / comex_prev - 1
    fx_move = float(fx.price) / fx_prev - 1
    expected_ret = (1 + comex_move) * (1 + fx_move) - 1
    expected = yesterday * (1 + expected_ret)
    now_px = float(mcx.price)
    change = expected - yesterday
    pct = expected_ret * 100
    if abs(pct) < 0.10:
        word, tone = "About the same as yesterday", "flat"
    elif change < 0:
        word, tone = "Down from yesterday", "down"
    else:
        word, tone = "Up from yesterday", "up"
    assumptions = pricing_from_settings(app.settings)
    shop = retail_from_mcx(expected, assumptions)
    yesterday_shop = retail_from_mcx(yesterday, assumptions)
    gst = 1 + assumptions.gst_pct / 100
    # Indian rate boards quote 22K per gram before GST. GST is added on the bill.
    g22_ex = shop.per_gram_22k / gst
    g24_ex = shop.per_gram_24k / gst
    y22_ex = yesterday_shop.per_gram_22k / gst
    as_of = mcx.observed_at_ist
    return {
        "expected": expected, "yesterday": yesterday, "now": now_px,
        "change": g22_ex - y22_ex, "pct": pct, "word": word, "tone": tone,
        "g24": shop.per_gram_24k, "g22": shop.per_gram_22k,
        "g24_ex": g24_ex, "g22_ex": g22_ex, "y22_ex": y22_ex,
        "gst_pct": assumptions.gst_pct,
        "as_of": as_of.strftime("%I:%M %p").lstrip("0") if as_of else "",
        "comex_pct": comex_move * 100, "fx_pct": fx_move * 100,
    }


def _today_ist() -> datetime:
    return datetime.now(ZoneInfo("Asia/Kolkata"))


_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, seconds: float, load):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < seconds:
        return hit[1]
    value = load()
    _cache[key] = (time.monotonic(), value)
    return value


def _public_provider(app: App) -> PublicFeedProvider | None:
    return next((p for p in app.providers if isinstance(p, PublicFeedProvider)), None)


def _tn_view(app: App) -> dict:
    """Tamil Nadu board: today, yesterday, high/low, and the expected next board."""
    path = board_rate.default_path()
    now = _today_ist()
    fetch_note = None
    try:
        fetched = _cached("tn_board", 30 * 60, fetch_board_history)
        board_rate.merge_fetched(path, fetched, TN_BOARD_SOURCE, now)
    except Exception:
        fetch_note = "Could not reach BankBazaar just now. Showing the rates already saved."
    rows = board_rate.load_all(path)
    today = board_rate.latest(rows, now.date())
    if today is None:
        return {"rows": rows, "fetch_note": fetch_note}
    board_day = date.fromisoformat(today["board_date"])
    out = {
        "rows": rows, "fetch_note": fetch_note, "today": today, "board_day": board_day,
        "yesterday": board_rate.previous(rows, board_day),
        "extremes": board_rate.extremes(rows),
        "first_day": date.fromisoformat(rows[0]["board_date"]),
        "move": None,
    }
    provider = _public_provider(app)
    if provider is None:
        return out
    try:
        gc = _cached("gc_5m", 120, lambda: provider.intraday("GC=F", "1mo"))
        fx = _cached("inr_5m", 120, lambda: provider.intraday("INR=X", "1mo"))
    except Exception:
        return out
    tz = ZoneInfo("Asia/Kolkata")
    gap = timedelta(days=3)  # weekend boards follow Friday's last trade

    def ref_time(d: date) -> datetime:
        return datetime.combine(d, board_rate.BOARD_SET_AT, tz)

    def intl_at(t: datetime) -> float | None:
        g, f = value_at(gc, t, gap), value_at(fx, t, gap)
        return g * f if g and f else None

    set_at = ref_time(board_day)
    gc_then, fx_then = value_at(gc, set_at, gap), value_at(fx, set_at, gap)
    if gc_then is None or fx_then is None or now < set_at:
        return out

    days = []  # (board day, board rate, international level at the reference time) for consecutive board days
    prev = None
    for r in rows:
        d = date.fromisoformat(r["board_date"])
        if prev and d.weekday() != 6:  # Sunday repeats Saturday's board
            days.append((prev[0], prev[1], d, r["g22"]))
        prev = (d, r["g22"])
    pairs = []
    for a, ga, b, gb in days:
        la, lb = intl_at(ref_time(a)), intl_at(ref_time(b))
        if la and lb:
            pairs.append((gb / ga - 1, lb / la - 1))
    fit = board_rate.fit_pass_through(pairs)
    pass_through = fit[0] if fit else 1.0

    target = max(board_day + timedelta(days=1), now.date())
    hours_left = max((ref_time(target) - now).total_seconds() / 3600, 0.0)
    # Replay the same estimate on past days, made the same number of hours before each board.
    past_errors = []
    for a, ga, b, gb in days:
        made = max(ref_time(b) - timedelta(hours=hours_left), ref_time(a))
        la, lm = intl_at(ref_time(a)), intl_at(made)
        if la and lm and made <= now:
            past_errors.append(gb / board_rate.expected_g22(ga, lm / la - 1, pass_through) - 1)
    move = board_rate.international_move(gc_then, fx_then, gc[-1][1], fx[-1][1])
    expected = board_rate.expected_g22(today["g22"], move, pass_through)
    rng = board_rate.error_band(expected, past_errors)
    low, high = rng if rng else (None, None)

    est_path = board_rate.estimates_path()
    # The earliest Chennai board seen in Nov 2026 was reported at 6:29 AM; estimates after 6 AM may know it.
    board_rate.record_estimate(est_path, target=target, g22=expected, low=low, high=high, made_at=now,
                               cutoff=datetime.combine(target, datetime.min.time(), tz) + timedelta(hours=6))
    saved = board_rate.load_estimates(est_path)
    check = None
    if board_day.isoformat() in saved:
        check = {"est": saved[board_day.isoformat()], "actual": today["g22"]}

    out.update({
        "move": move, "pass_through": pass_through, "fitted": fit is not None, "fit_days": len(pairs),
        "hours_left": hours_left, "error_days": len(past_errors),
        "gc_move": gc[-1][1] / gc_then - 1, "fx_move": fx[-1][1] / fx_then - 1,
        "expected": expected, "low": low, "high": high, "target": target, "check": check,
    })
    return out


def _unit_switch(unit: str) -> str:
    links = []
    for key, (_, label) in board_rate.UNITS.items():
        cls = "on" if key == unit else ""
        links.append(f'<a class="{cls}" href="/?unit={key}">{html.escape(label)}</a>')
    return f'<nav class="units">{"".join(links)}</nav>'


def _board_form(error: str | None, value: str) -> str:
    err = f"<p class='bad'>{html.escape(error)}</p>" if error else ""
    return f"""
<form class="board" method="post" action="/">
  <label for="g22">Today’s Tamil Nadu board rate · 22 karat, 1 gram</label>
  <div class="row">
    <span class="rs">₹</span>
    <input id="g22" name="g22" inputmode="numeric" autocomplete="off" placeholder="11980" value="{html.escape(value)}">
    <button type="submit">Save</button>
  </div>
  <p class="hint">Use the rate before GST, the way the jeweller board or newspaper prints it.</p>
  {err}
</form>"""


def _dashboard(form_error: str | None = None, form_value: str = "", unit: str = "gram") -> str:
    """Browser page for /. Tamil Nadu board rates in everyday language."""
    app = get_app()
    try:
        view = _price_view(app)
        err = None
    except Exception as e:
        view, err = None, str(e)

    def esc(v) -> str:
        return html.escape("" if v is None else str(v))

    if err or view is None:
        body = f"<p class='bad'>{esc(err or 'Price unavailable')}</p>"
        return _page(body)

    unit = unit if unit in board_rate.UNITS else "gram"
    grams, unit_label = board_rate.UNITS[unit]
    gst = 1 + view["gst_pct"] / 100
    when = f" · {esc(view['as_of'])}" if view["as_of"] else ""
    tn = _tn_view(app)
    today = tn.get("today")
    fetch_note = f"<p class='bad'>{esc(tn['fetch_note'])}</p>" if tn.get("fetch_note") else ""

    def amount(g22: float) -> str:
        return _inr(board_rate.per_unit(g22, grams))

    def day(d: date) -> str:
        return f"{d:%a} {d.day} {d:%b}"

    if today:
        board_day = tn["board_day"]
        stale = board_day != _today_ist().date()
        base = f"the {day(board_day)} board" if stale else "today"
        rows_html = []
        if tn["move"] is not None:
            exp, target = tn["expected"], tn["target"]
            diff = board_rate.per_unit(exp, grams) - board_rate.per_unit(today["g22"], grams)
            pct = (exp / today["g22"] - 1) * 100
            tone = "flat" if abs(pct) < 0.10 else ("down" if diff < 0 else "up")
            word = {"flat": "About the same as", "down": "Down from", "up": "Up from"}[tone]
            pt = tn["pass_through"]
            head = f"""
<p class="kicker">Tamil Nadu · 22 karat · {esc(unit_label)}</p>
<p class="label">Expected on {day(target)}</p>
<p class="price">{amount(exp)}</p>
<p class="move {tone}">{word} {base} · {_inr(abs(diff))} · {pct:+.2f}%</p>
{f"<p class='range'>Likely between {amount(tn['low'])} and {amount(tn['high'])}</p>" if tn['low'] else ""}
<p class="versus">With {view['gst_pct']:g}% GST on the bill: {_inr(board_rate.per_unit(exp, grams) * gst)}</p>"""
            if tn["fitted"]:
                pt_text = (f"Over the last {tn['fit_days']} board days, the Tamil Nadu board moved about "
                           f"{pt * 100:.0f}% as much as international gold in rupees, so only that share is applied. ")
            else:
                pt_text = "There is not enough saved history yet to measure how closely the board follows, so the full move is applied. "
            left = (f"Prices can still move in the {tn['hours_left']:.0f} hours before the next board, and that is "
                    "where most misses come from. " if tn["hours_left"] >= 1 else "")
            rng_text = (f"The range is how far this same estimate missed on the last {tn['error_days']} board days "
                        "when made this many hours ahead, sized to hold about 8 in 10 days. "
                        if tn["low"] else "")
            how = (f"<p>The board lines up best with international prices at about 9 AM. Since 9 AM on "
                   f"{day(board_day)}, international gold has moved {tn['gc_move'] * 100:+.2f}% and the rupee "
                   f"{tn['fx_move'] * 100:+.2f}%, together {tn['move'] * 100:+.2f}%. {pt_text}{left}{rng_text}"
                   "Weekend boards follow Friday night’s prices, and Sunday usually repeats Saturday. "
                   "This is an estimate, not a promise.</p>")
        else:
            head = f"""
<p class="kicker">Tamil Nadu · 22 karat · {esc(unit_label)}</p>
<p class="label">Board on {day(board_day)}</p>
<p class="price">{amount(today['g22'])}</p>
<p class="versus">The expected rate for tomorrow shows once international prices since 9 AM are available.</p>"""
            how = "<p>The expected rate needs international gold and rupee prices since the board was set.</p>"

        def row(label: str, value: str, sub: str = "") -> str:
            sub_html = f"<span class='sub'>{esc(sub)}</span>" if sub else ""
            return f"<div class='r'><span>{esc(label)}{sub_html}</span><strong>{value}</strong></div>"

        rows_html.append(row("Latest board" if stale else "Today", amount(today["g22"]), day(board_day)))
        if tn["yesterday"]:
            y = tn["yesterday"]
            rows_html.append(row("Board before that" if stale else "Yesterday", amount(y["g22"]),
                                 day(date.fromisoformat(y["board_date"]))))
        if tn.get("check"):
            est, actual = tn["check"]["est"], tn["check"]["actual"]
            made = datetime.fromisoformat(est["made_at"])
            off = board_rate.per_unit(actual, grams) - board_rate.per_unit(est["g22"], grams)
            verdict = ""
            if est.get("low") is not None:
                inside = est["low"] <= actual <= est["high"]
                verdict = ", inside the range" if inside else ", outside the range"
            rows_html.append(row(f"GoldPulse expected for {day(board_day)}", amount(est["g22"]),
                                 f"made {day(made.date())} {made:%I:%M %p}".replace(" 0", " ")
                                 + f" · the board was {_inr(abs(off))} {'higher' if off > 0 else 'lower'}{verdict}"))
        hi, lo = tn["extremes"]
        span = f"in {len(tn['rows'])} days"
        rows_html.append(row(f"Highest {span}", amount(hi["g22"]), day(date.fromisoformat(hi["board_date"]))))
        rows_html.append(row(f"Lowest {span}", amount(lo["g22"]), day(date.fromisoformat(lo["board_date"]))))
        stale_note = (f"<p class='bad'>GoldPulse does not have today’s board yet, so these start from the "
                      f"{day(board_day)} board. If it is out, type it under “Today’s board looks wrong?”.</p>"
                      if stale else "")
        body = f"""
{_unit_switch(unit)}
{head}
{stale_note}{fetch_note}
<div class="rows">{''.join(rows_html)}</div>
<p class="note">All rates are before GST. Making charges are extra. Highest and lowest cover the days
GoldPulse has saved so far, from {day(tn['first_day'])}. The range grows each day.</p>
<details>
  <summary>How tomorrow’s rate is worked out</summary>
  {how}
</details>
<details{' open' if form_error else ''}>
  <summary>Today’s board looks wrong?</summary>
  {_board_form(form_error, form_value)}
</details>
<p class="now">Board rates: BankBazaar, Chennai. Exchange right now: <strong>{_inr(view['now'])}</strong> per 10 grams{when}.</p>
"""
    else:
        body = f"""
<p class="kicker">22 karat gold, per gram</p>
<p class="price">{_inr(view['g22_ex'])}</p>
<p class="unit">from the exchange, before GST</p>
<p class="move {view['tone']}">{esc(view['word'])} · {_inr(abs(view['change']))} · {view['pct']:+.2f}%</p>
<p class="note">This comes from the exchange price, so it sits a little away from the Tamil Nadu board. Save today’s board rate below to see the expected Tamil Nadu rate.</p>
{_board_form(form_error, form_value)}
<p class="now">Exchange right now: <strong>{_inr(view['now'])}</strong> per 10 grams{when}.</p>
"""
    return _page(body)


def _page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>GoldPulse — expected gold price</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ margin: 0; font: 17px/1.45 system-ui, sans-serif; background: #12140f; color: #f4f0e6; }}
  main {{ max-width: 520px; margin: 0 auto; padding: 36px 20px 72px; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 28px; letter-spacing: 0.02em; }}
  .kicker {{ margin: 0; color: #b7b1a4; }}
  .price {{ font-size: 3.4rem; font-weight: 720; letter-spacing: -0.04em; margin: 4px 0 0; }}
  .unit {{ margin: 0; color: #b7b1a4; }}
  .move {{ font-size: 1.15rem; margin: 18px 0 4px; }}
  .versus {{ margin: 0 0 28px; color: #b7b1a4; }}
  .down {{ color: #ffb4a8; }}
  .up {{ color: #b7e3b4; }}
  .flat {{ color: #e4c36a; }}
  .cards {{ display: flex; gap: 12px; }}
  .card {{ flex: 1; background: #1c1e18; border-radius: 14px; padding: 14px 16px; }}
  .k {{ margin: 0; color: #b7b1a4; font-size: 0.95rem; }}
  .v {{ margin: 2px 0 0; font-size: 1.55rem; font-weight: 680; }}
  .s {{ margin: 0; color: #b7b1a4; }}
  .note, .now {{ color: #d9d3c7; }}
  details {{ margin-top: 22px; color: #b7b1a4; }}
  summary {{ cursor: pointer; color: #e4c36a; }}
  a {{ color: #e4c36a; }}
  .bad {{ color: #ffb4a8; }}
  .cards .v {{ font-size: 1.3rem; }}
  .board {{ margin: 26px 0 8px; padding: 16px; background: #1c1e18; border-radius: 14px; }}
  .board label {{ display: block; color: #d9d3c7; margin-bottom: 8px; }}
  .board .row {{ display: flex; align-items: center; gap: 8px; }}
  .board .rs {{ color: #b7b1a4; font-size: 1.2rem; }}
  .board input {{ flex: 1; min-width: 0; font: inherit; font-size: 1.2rem; padding: 8px 10px; border-radius: 10px;
                  border: 1px solid #3a3d33; background: #12140f; color: #f4f0e6; }}
  .board button {{ font: inherit; padding: 8px 16px; border-radius: 10px; border: 0; background: #e4c36a;
                   color: #12140f; font-weight: 650; cursor: pointer; }}
  .board .hint {{ margin: 8px 0 0; color: #b7b1a4; font-size: 0.92rem; }}
  .board .bad {{ margin: 8px 0 0; }}
  .units {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 24px; }}
  .units a {{ text-decoration: none; color: #d9d3c7; background: #1c1e18; border-radius: 999px;
              padding: 6px 12px; font-size: 0.95rem; }}
  .units a.on {{ background: #e4c36a; color: #12140f; font-weight: 650; }}
  .label {{ margin: 6px 0 0; color: #f4f0e6; font-weight: 600; }}
  .range {{ margin: 0 0 4px; color: #d9d3c7; }}
  .rows {{ background: #1c1e18; border-radius: 14px; padding: 4px 16px; margin: 4px 0 16px; }}
  .r {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
        padding: 12px 0; border-bottom: 1px solid #2a2c25; }}
  .r:last-child {{ border-bottom: 0; }}
  .r strong {{ font-size: 1.2rem; white-space: nowrap; }}
  .sub {{ display: block; color: #b7b1a4; font-size: 0.88rem; }}
  details .board {{ margin-top: 12px; }}
</style></head><body><main>
<h1>GoldPulse</h1>
{body}
</main></body></html>"""


@api.get("/", response_class=HTMLResponse)
def home(unit: str = "gram"):
    return HTMLResponse(_dashboard(unit=unit))


@api.post("/", response_class=HTMLResponse)
async def save_board_rate(request: Request):
    raw = parse_qs((await request.body()).decode("utf-8", "replace")).get("g22", [""])[0]
    try:
        g22 = board_rate.parse_rupees(raw)
        view = _price_view(get_app())
        board_rate.check_rate(g22, view["now"])
    except ValueError as e:
        return HTMLResponse(_dashboard(str(e), raw), status_code=400)
    except Exception as e:
        return HTMLResponse(_dashboard(f"Could not read the exchange price, so the rate was not saved. {e}", raw),
                            status_code=503)
    now = _today_ist()
    board_rate.save_manual(board_rate.default_path(), board_date=now.date(), g22=g22, saved_at=now)
    return RedirectResponse("/", status_code=303)


@api.exception_handler(ApiError)
async def _api_error(_, e: ApiError):
    return JSONResponse(status_code=e.status, content={"detail": e.detail})


@v1.get("/health")
def health(): return get_app().health()

@v1.get("/calendar/status")
def calendar_status(d: date | None = Query(None, alias="date")): return get_app().calendar_status(d)

@v1.post("/logs")
def logs(body: dict): return get_app().log_event(body)

@v1.get("/market/latest")
def market_latest(): return get_app().market_latest()

@v1.get("/market/history")
def market_history(instrument: str | None = None, start: datetime | None = None, end: datetime | None = None,
                   limit: int = Query(500, le=5000)):
    return get_app().market_history(instrument, start, end, limit)

@v1.get("/market/fetch/{instrument}")
def market_fetch(instrument: str): return get_app().market_fetch(instrument)

@v1.post("/market/snapshot")
def market_snapshot(body: dict): return get_app().market_snapshot(body)

@v1.post("/market/actual")
def market_actual(body: dict): return get_app().market_actual(body)

@v1.post("/predictions/generate")
def predictions_generate(force: bool = False): return get_app().predictions_generate(force=force, trigger="n8n")

@v1.post("/predictions/evaluate")
def predictions_evaluate(up_to: date | None = None): return get_app().predictions_evaluate(up_to)

@v1.get("/predictions/latest")
def predictions_latest(): return get_app().predictions_latest()

@v1.get("/predictions/history")
def predictions_history(limit: int = Query(100, le=1000)): return get_app().predictions_history(limit)

@v1.get("/predictions/accuracy")
def predictions_accuracy(): return get_app().predictions_accuracy()

@v1.get("/models/performance")
def models_performance(): return get_app().models_performance()


api.include_router(v1)
