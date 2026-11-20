"""Generates the importable n8n workflow JSON files in ./n8n.

Design notes
- Every external call goes through the GoldPulse API (http://api:8000 inside docker-compose), so n8n needs only
  three credentials: 'GoldPulse API' (Header Auth: X-API-Key), Google Sheets, Telegram.
- HTTP nodes: timeout, retryOnFail, and an *error output* wired to a log node. Nothing fails silently.
- All schedules use the Asia/Kolkata workflow timezone.
- Holiday handling: the API's /calendar/status decides; workflows stop early on non-trading days.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

API = "http://api:8000/api/v1"
CRED_API = {"httpHeaderAuth": {"id": "REPLACE_ME", "name": "GoldPulse API"}}
CRED_SHEETS = {"googleSheetsOAuth2Api": {"id": "REPLACE_ME", "name": "Google Sheets account"}}
CRED_TG = {"telegramApi": {"id": "REPLACE_ME", "name": "Telegram account"}}
SHEET_ID = "REPLACE_WITH_GOOGLE_SHEET_ID"


class WF:
    def __init__(self, name: str):
        self.name, self.nodes, self.conns, self.x = name, [], {}, 0

    def add(self, name, type_, params, version=1, creds=None, pos=None, **extra):
        self.x += 260
        n = {"parameters": params, "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.name + name)), "name": name,
             "type": type_, "typeVersion": version, "position": pos or [self.x, 300]}
        if creds: n["credentials"] = creds
        n.update(extra)
        self.nodes.append(n)
        return name

    def link(self, a, b, out=0):
        self.conns.setdefault(a, {"main": []})["main"]
        m = self.conns[a]["main"]
        while len(m) <= out: m.append([])
        m[out].append({"node": b, "type": "main", "index": 0})

    def json(self):
        return {"name": self.name, "nodes": self.nodes, "connections": self.conns,
                "active": False, "settings": {"executionOrder": "v1", "timezone": "Asia/Kolkata",
                                              "saveManualExecutions": True, "executionTimeout": 600},
                "tags": [], "pinData": {}, "meta": {"templateCredsSetupCompleted": False}}


def http(w, name, method, path, body=None, retries=3, timeout=30000, error_output=True, wait=3000, pos=None):
    p = {"method": method, "url": f"={API}{path}" if "{{" in path else f"{API}{path}",
         "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
         "options": {"timeout": timeout}}
    if body is not None:
        p.update(sendBody=True, contentType="json", specifyBody="json", jsonBody=body)
    extra = {"retryOnFail": retries > 1, "maxTries": retries, "waitBetweenTries": wait}
    if error_output: extra["onError"] = "continueErrorOutput"
    return w.add(name, "n8n-nodes-base.httpRequest", p, 4.2, creds=CRED_API, pos=pos, **extra)


def log_node(w, name, workflow, level, event, message_expr):
    body = ("={{ JSON.stringify({workflow: '%s', level: '%s', event: '%s', message: %s, payload: $json}) }}"
            % (workflow, level, event, message_expr))
    return http(w, name, "POST", "/logs", body, retries=2, timeout=10000, error_output=False)


def schedule(w, name, cron):
    return w.add(name, "n8n-nodes-base.scheduleTrigger",
                 {"rule": {"interval": [{"field": "cronExpression", "expression": cron}]}}, 1.2)


def code(w, name, js, mode="runOnceForAllItems"):
    return w.add(name, "n8n-nodes-base.code", {"jsCode": js, "mode": mode}, 2)


INSTRUMENTS_JS = "return %s.map(i => ({json: {instrument: i}}));"
TRADING_DAY_IF = lambda w, name: w.add(name, "n8n-nodes-base.if", {"conditions": {
    "options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
    "conditions": [{"id": "td", "leftValue": "={{ $json.india_trading_day }}", "rightValue": True,
                    "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}, 2.2)

VALIDATE_JS = r"""
// Freshness + sanity validation for the quotes fetched in this run. Never repairs data.
const maxAgeH = {COMEX_GC: 3, XAUUSD: 3, USDINR: 6, MCX_GOLD: 36};
const need = %s;
const now = Date.now();
const items = $input.all().map(i => i.json);
const problems = [], quotes = [];
for (const inst of need) {
  const q = items.find(x => x.instrument === inst);
  if (!q) { problems.push(inst + ': missing (fetch failed after retries)'); continue; }
  if (!(Number(q.price) > 0)) { problems.push(inst + ': invalid price'); continue; }
  const t = Date.parse(q.as_of);
  if (isNaN(t)) { problems.push(inst + ': unparseable timestamp'); continue; }
  const ageH = (now - t) / 3.6e6;
  if (ageH < -0.05) { problems.push(inst + ': timestamp in the future'); continue; }
  if (ageH > maxAgeH[inst]) { problems.push(inst + ': stale (' + ageH.toFixed(1) + 'h old)'); continue; }
  quotes.push(q);
}
const slotDate = new Date(now + 5.5 * 3.6e6).toISOString().slice(0, 10); // IST calendar date
return [{json: {ok: problems.length === 0, problems, quotes, slot_date: slotDate}}];
"""

EXPAND_JS = r"""
// One item per quote, normalised for POST /market/snapshot. `as_of` is UTC ISO-8601 from the API.
const d = $input.first().json;
const SLOTTED = %s;
return d.quotes.map(q => ({json: {
  instrument: q.instrument, price: q.price, currency: q.currency, provider: q.provider, as_of: q.as_of,
  market_id: q.market_id, is_delayed: q.is_delayed, delay_minutes: q.delay_minutes,
  slot: SLOTTED.includes(q.instrument) ? '%s' : null, slot_date: SLOTTED.includes(q.instrument) ? d.slot_date : null}}));
"""

GATE_JS = r"""
// Collapse N per-quote items to one so downstream nodes run exactly once.
const n = $input.all().length;
return [{json: {persisted: n, expected: %d, complete: n >= %d}}];
"""


def workflow_a():
    w = WF("GoldPulse A - Overnight market collection")
    s = schedule(w, "Trigger 23:30 IST", "30 23 * * *")
    cal = http(w, "Calendar status", "GET", "/calendar/status", retries=3)
    ins = code(w, "Instruments", INSTRUMENTS_JS % json.dumps(["COMEX_GC", "XAUUSD", "USDINR", "MCX_GOLD"]))
    fetch = http(w, "Fetch quote", "GET", "/market/fetch/{{ $json.instrument }}", retries=3, wait=5000)
    val = code(w, "Validate quotes", VALIDATE_JS % json.dumps(["COMEX_GC", "XAUUSD", "USDINR"]))
    ok = w.add("Quotes valid?", "n8n-nodes-base.if", {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
        "conditions": [{"id": "ok", "leftValue": "={{ $json.ok }}", "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}, 2.2)
    exp = code(w, "Normalize timestamps & slot", EXPAND_JS % (json.dumps(["COMEX_GC", "XAUUSD", "USDINR"]), "CLOSE"))
    save = http(w, "Save snapshot (PostgreSQL via API)", "POST", "/market/snapshot",
                "={{ JSON.stringify($json) }}", retries=3)
    gate = code(w, "Summarize", GATE_JS % (3, 3))
    done = log_node(w, "Log success", "overnight_collect", "INFO", "collected", "'snapshots persisted: ' + $json.persisted")
    fail_fetch = log_node(w, "Log fetch failure", "overnight_collect", "ERROR", "fetch_failed",
                          "'fetch failed: ' + ($json.error?.message || JSON.stringify($json.error || $json))")
    fail_val = log_node(w, "Log validation failure", "overnight_collect", "WARNING", "validation_failed",
                        "'invalid/stale data: ' + $json.problems.join('; ')")
    fail_save = log_node(w, "Log save failure", "overnight_collect", "ERROR", "save_failed",
                         "'snapshot rejected/failed: ' + ($json.error?.message || JSON.stringify($json.error || $json))")
    w.link(s, cal); w.link(cal, ins); w.link(cal, fail_fetch, 1)
    w.link(ins, fetch); w.link(fetch, val); w.link(fetch, fail_fetch, 1)
    w.link(val, ok); w.link(ok, exp, 0); w.link(ok, fail_val, 1)
    w.link(exp, save); w.link(save, gate); w.link(save, fail_save, 1); w.link(gate, done)
    return w


def workflow_b():
    w = WF("GoldPulse B - Morning prediction")
    s = schedule(w, "Trigger 08:30 IST", "30 8 * * *")
    cal = http(w, "Calendar status", "GET", "/calendar/status", retries=3)
    td = TRADING_DAY_IF(w, "Indian trading day?")
    skip = log_node(w, "Log holiday skip", "morning_prediction", "INFO", "holiday_skip",
                    "'No prediction: not an Indian trading day (' + $json.date + ')'")
    ins = code(w, "Instruments", INSTRUMENTS_JS % json.dumps(["COMEX_GC", "XAUUSD", "USDINR", "MCX_GOLD"]))
    fetch = http(w, "Fetch latest price", "GET", "/market/fetch/{{ $json.instrument }}", retries=3, wait=5000)
    val = code(w, "Validate data freshness", VALIDATE_JS % json.dumps(["COMEX_GC", "XAUUSD", "USDINR", "MCX_GOLD"]))
    ok = w.add("Data fresh?", "n8n-nodes-base.if", {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
        "conditions": [{"id": "ok", "leftValue": "={{ $json.ok }}", "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}, 2.2)
    exp = code(w, "Normalize quotes", EXPAND_JS % (json.dumps(["COMEX_GC", "XAUUSD", "USDINR"]), "MORNING"))
    save = http(w, "Save morning snapshot", "POST", "/market/snapshot", "={{ JSON.stringify($json) }}", retries=3)
    gate = code(w, "Summarize", GATE_JS % (3, 3))
    gen = http(w, "Generate prediction (Python API)", "POST", "/predictions/generate", None, retries=2,
               timeout=120000, wait=10000)
    created = w.add("New prediction?", "n8n-nodes-base.if", {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
        "conditions": [{"id": "c", "leftValue": "={{ $json.created }}", "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}, 2.2)
    row = code(w, "Build sheet row", r"""
const p = $input.first().json;
const h = ['Date','Previous MCX Price','COMEX Change','USD/INR Change','Predicted Direction','Predicted Price','Actual Price','Prediction Error','Accuracy Status'];
const o = {}; h.forEach((k, i) => o[k] = p.sheet_row[i]);
return [{json: Object.assign({}, o, {telegram_text: p.telegram_text})}];""")
    sheet = w.add("Append to Daily Predictions", "n8n-nodes-base.googleSheets", {
        "operation": "append", "documentId": {"__rl": True, "mode": "id", "value": SHEET_ID},
        "sheetName": {"__rl": True, "mode": "name", "value": "Daily Predictions"},
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
        "options": {}}, 4.5, creds=CRED_SHEETS)
    tg_text = w.add("Rebuild message", "n8n-nodes-base.code", {"mode": "runOnceForAllItems", "jsCode":
        "return [{json: {text: $('Build sheet row').first().json.telegram_text}}];"}, 2)
    tg = w.add("Telegram forecast", "n8n-nodes-base.telegram", {
        "chatId": "REPLACE_WITH_TELEGRAM_CHAT_ID", "text": "={{ $json.text }}",
        "additionalFields": {"appendAttribution": False}}, 1.2, creds=CRED_TG)
    log_ok = log_node(w, "Log success", "morning_prediction", "INFO", "published", "'forecast published'")
    log_dup = log_node(w, "Log duplicate", "morning_prediction", "INFO", "duplicate_skipped",
                       "'prediction already existed; Sheets/Telegram skipped'")
    log_stale = log_node(w, "Log blocked (stale/incomplete)", "morning_prediction", "WARNING", "blocked",
                         "'No forecast: ' + $json.problems.join('; ')")
    log_fetch = log_node(w, "Log fetch failure", "morning_prediction", "ERROR", "fetch_failed",
                         "'fetch failed: ' + ($json.error?.message || JSON.stringify($json.error || $json))")
    log_gen = log_node(w, "Log generation failure", "morning_prediction", "ERROR", "generate_failed",
                       "'API refused/failed: ' + JSON.stringify($json.error || $json)")
    alert = w.add("Telegram: no forecast today", "n8n-nodes-base.telegram", {
        "chatId": "REPLACE_WITH_TELEGRAM_CHAT_ID",
        "text": "GOLDPULSE INDIA: no forecast published today (data stale/incomplete or API error). "
                "No prediction was generated from partial data. See automation_logs.",
        "additionalFields": {"appendAttribution": False}}, 1.2, creds=CRED_TG)
    log_cal = log_node(w, "Log calendar failure", "morning_prediction", "ERROR", "calendar_failed",
                       "'calendar status call failed: ' + JSON.stringify($json.error || $json)")
    w.link(s, cal); w.link(cal, td); w.link(cal, log_cal, 1); w.link(log_cal, alert)
    w.link(td, ins, 0); w.link(td, skip, 1)
    w.link(ins, fetch); w.link(fetch, val); w.link(fetch, log_fetch, 1)
    w.link(val, ok); w.link(ok, exp, 0); w.link(ok, log_stale, 1); w.link(log_stale, alert)
    w.link(exp, save); w.link(save, gate); w.link(save, log_fetch, 1)
    w.link(gate, gen); w.link(gen, created, 0); w.link(gen, log_gen, 1); w.link(log_gen, alert)
    w.link(created, row, 0); w.link(created, log_dup, 1)
    w.link(row, sheet); w.link(sheet, tg_text); w.link(tg_text, tg); w.link(tg, log_ok)
    return w


def workflow_c():
    w = WF("GoldPulse C - Accuracy evaluation")
    s = schedule(w, "Trigger 23:59 IST (after MCX close)", "59 23 * * *")
    cal = http(w, "Calendar status", "GET", "/calendar/status", retries=3)
    td = TRADING_DAY_IF(w, "Indian trading day?")
    skip = log_node(w, "Log holiday skip", "accuracy_evaluation", "INFO", "holiday_skip",
                    "'Not an Indian trading day; nothing to evaluate'")
    fetch = http(w, "Fetch actual MCX close", "GET", "/market/fetch/MCX_GOLD", retries=3, wait=10000)
    chk = code(w, "Check close is fresh", r"""
const q = $input.first().json;
const ageH = (Date.now() - Date.parse(q.as_of)) / 3.6e6;
const istDate = new Date(Date.now() + 5.5 * 3.6e6).toISOString().slice(0, 10);
if (!(q.price > 0) || isNaN(ageH) || ageH > 2) {
  return [{json: {ok: false, reason: 'MCX close missing/stale (age ' + ageH.toFixed(1) + 'h)'}}];
}
return [{json: {ok: true, trading_date: istDate, close: q.price, source: q.provider,
                contract_key: q.contract_key || 'SPOT'}}];""")
    ok = w.add("Close usable?", "n8n-nodes-base.if", {"conditions": {
        "options": {"caseSensitive": True, "typeValidation": "strict"}, "combinator": "and",
        "conditions": [{"id": "ok", "leftValue": "={{ $json.ok }}", "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}}, 2.2)
    act = http(w, "Save actual close", "POST", "/market/actual",
               "={{ JSON.stringify({trading_date: $json.trading_date, instrument: 'MCX_GOLD', "
               "contract_key: $json.contract_key, close: $json.close, source: $json.source}) }}", retries=3)
    ev = http(w, "Evaluate forecasts", "POST", "/predictions/evaluate", None, retries=2, timeout=60000)
    rows = code(w, "Build sheet rows", r"""
const r = $input.first().json;
const hdr = ['Target Date','Model Version','Predicted Price','Actual Close','Abs Error','% Error','Predicted Direction','Actual Direction','Direction Correct','Within Interval'];
const out = r.sheet_rows.map(a => { const o = {}; hdr.forEach((k, i) => o[k] = a[i]); return {json: o}; });
return out;                                // empty => branch ends, nothing appended""")
    sheet = w.add("Append to Prediction Accuracy", "n8n-nodes-base.googleSheets", {
        "operation": "append", "documentId": {"__rl": True, "mode": "id", "value": SHEET_ID},
        "sheetName": {"__rl": True, "mode": "name", "value": "Prediction Accuracy"},
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
        "options": {}}, 4.5, creds=CRED_SHEETS)
    actual_rows = code(w, "Actual row (only if newly inserted)", r"""
const saved = $input.first().json;
if (!saved.inserted) return [];            // duplicate close: do not append a second sheet row
const c = $('Check close is fresh').first().json;
return [{json: {'Trading Date': c.trading_date, 'Instrument': 'MCX_GOLD', 'Contract': c.contract_key,
                'Close': c.close, 'Source': c.source}}];""")
    actual_sheet = w.add("Append to Actual Prices", "n8n-nodes-base.googleSheets", {
        "operation": "append", "documentId": {"__rl": True, "mode": "id", "value": SHEET_ID},
        "sheetName": {"__rl": True, "mode": "name", "value": "Actual Prices"},
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": []},
        "options": {}}, 4.5, creds=CRED_SHEETS)
    perf = http(w, "Fetch model performance", "GET", "/models/performance", retries=2)
    done = log_node(w, "Log success", "accuracy_evaluation", "INFO", "evaluated", "'evaluation complete'")
    log_bad = log_node(w, "Log close unavailable", "accuracy_evaluation", "WARNING", "close_unavailable",
                       "$json.reason")
    log_fetch = log_node(w, "Log fetch failure", "accuracy_evaluation", "ERROR", "fetch_failed",
                         "'fetch failed: ' + ($json.error?.message || JSON.stringify($json.error || $json))")
    log_api = log_node(w, "Log API failure", "accuracy_evaluation", "ERROR", "api_failed",
                       "'API failed: ' + JSON.stringify($json.error || $json)")
    log_cal = log_node(w, "Log calendar failure", "accuracy_evaluation", "ERROR", "calendar_failed",
                       "'calendar status call failed: ' + JSON.stringify($json.error || $json)")
    w.link(s, cal); w.link(cal, td); w.link(cal, log_cal, 1); w.link(td, fetch, 0); w.link(td, skip, 1)
    w.link(fetch, chk); w.link(fetch, log_fetch, 1)
    w.link(chk, ok); w.link(ok, act, 0); w.link(ok, log_bad, 1)
    w.link(act, actual_rows); w.link(actual_rows, actual_sheet); w.link(act, ev); w.link(act, log_api, 1)
    w.link(ev, rows); w.link(ev, perf); w.link(ev, log_api, 1)
    w.link(rows, sheet); w.link(perf, done); w.link(perf, log_api, 1)
    return w


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "n8n"
    out.mkdir(exist_ok=True)
    for fn, f in [("workflow_a_overnight_collection.json", workflow_a), ("workflow_b_morning_prediction.json", workflow_b),
                  ("workflow_c_accuracy_evaluation.json", workflow_c)]:
        (out / fn).write_text(json.dumps(f().json(), indent=2))
        print("wrote", fn)
