"""Structural validation + execution of the embedded Code-node JavaScript under real Node.js.

LIMITATION: n8n itself is not installed here, so full workflow runs (credentials, HTTP, Sheets, Telegram)
are NOT executed. Everything that can be verified without n8n is: JSON shape, graph integrity, schedules,
timezone, error handling flags, and the JS logic of every Code node (run in Node with mocked n8n globals).
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "n8n"
FILES = sorted(DIR.glob("*.json"))
HAVE_NODE = shutil.which("node") is not None


def load(p): return json.loads(p.read_text())


def test_three_workflows_exist():
    assert [f.name for f in FILES] == ["workflow_a_overnight_collection.json",
                                       "workflow_b_morning_prediction.json",
                                       "workflow_c_accuracy_evaluation.json"]


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.stem)
def test_graph_integrity_and_settings(f):
    d = load(f)
    names = [n["name"] for n in d["nodes"]]
    assert len(names) == len(set(names)), "duplicate node names"
    assert len({n["id"] for n in d["nodes"]}) == len(names)
    assert d["settings"]["timezone"] == "Asia/Kolkata"
    for src, c in d["connections"].items():
        assert src in names
        for out in c["main"]:
            for t in out:
                assert t["node"] in names
    triggers = [n for n in d["nodes"] if n["type"].endswith("scheduleTrigger")]
    assert len(triggers) == 1
    # every node (except the trigger) is reachable from the trigger
    seen, stack = set(), [triggers[0]["name"]]
    while stack:
        n = stack.pop()
        if n in seen: continue
        seen.add(n)
        stack += [t["node"] for out in d["connections"].get(n, {"main": []})["main"] for t in out]
    assert seen == set(names), f"unreachable nodes: {set(names) - seen}"


@pytest.mark.parametrize("f", FILES, ids=lambda f: f.stem)
def test_http_nodes_have_timeout_retry_and_error_path(f):
    d = load(f)
    conns = d["connections"]
    for n in d["nodes"]:
        if not n["type"].endswith("httpRequest"):
            continue
        assert n["parameters"]["options"]["timeout"] > 0, n["name"]
        assert n["maxTries"] >= 2, f"{n['name']} has no retries"
        if n.get("onError") == "continueErrorOutput":
            outs = conns.get(n["name"], {"main": []})["main"]
            assert len(outs) == 2 and outs[1], f"{n['name']}: error output not wired to a handler"
        else:
            assert n["name"].startswith("Log"), f"{n['name']} can fail silently"


def test_schedules():
    cron = {f.stem: load(f)["nodes"][0]["parameters"]["rule"]["interval"][0]["expression"] for f in FILES}
    assert cron["workflow_a_overnight_collection"] == "30 23 * * *"      # ~11:30 PM IST
    assert cron["workflow_b_morning_prediction"] == "30 8 * * *"         # 8:30 AM IST
    assert cron["workflow_c_accuracy_evaluation"] == "59 23 * * *"       # after MCX session


def test_holiday_gating_and_placeholders_only():
    for f in FILES:
        raw = f.read_text()
        if "morning" in f.name or "accuracy" in f.name:
            assert "india_trading_day" in raw
        assert "REPLACE_" in raw                                          # placeholders, no real secrets
        for bad in ("bot1", "AIza", "sk-"):
            assert bad not in raw


def code_nodes(f):
    return [n for n in load(f)["nodes"] if n["type"].endswith(".code")]


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
@pytest.mark.parametrize("f", FILES, ids=lambda f: f.stem)
def test_code_nodes_are_valid_javascript(f, tmp_path):
    for n in code_nodes(f):
        p = tmp_path / "x.js"
        p.write_text("(async function(){\n" + n["parameters"]["jsCode"] + "\n})")
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, f"{n['name']}: {r.stderr}"


def run_js(code: str, input_items: list, extra_globals: str = "") -> list:
    harness = textwrap.dedent("""
        const items = %s;
        const wrap = j => ({json: j});
        const $input = {all: () => items.map(wrap), first: () => wrap(items[0])};
        %s
        const fn = async function() { %s };
        fn().then(r => console.log(JSON.stringify(r))).catch(e => { console.error(e.message); process.exit(1); });
    """) % (json.dumps(input_items), extra_globals, code)
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def node_code(workflow_file: str, name: str) -> str:
    return next(n["parameters"]["jsCode"] for n in code_nodes(DIR / workflow_file) if n["name"] == name)


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
def test_validate_node_behaviour():
    from datetime import datetime, timedelta, timezone
    js = node_code("workflow_b_morning_prediction.json", "Validate data freshness")
    now = datetime.now(timezone.utc)
    q = lambda inst, age_h, price=100.0: {"instrument": inst, "price": price,
                                          "as_of": (now - timedelta(hours=age_h)).isoformat()}
    good = [q("COMEX_GC", 1), q("XAUUSD", 1), q("USDINR", 1), q("MCX_GOLD", 1)]
    out = run_js(js, good)[0]["json"]
    assert out["ok"] and len(out["quotes"]) == 4 and len(out["slot_date"]) == 10
    stale = [q("COMEX_GC", 1), q("XAUUSD", 9), q("USDINR", 1), q("MCX_GOLD", 1)]
    out = run_js(js, stale)[0]["json"]
    assert not out["ok"] and "XAUUSD: stale" in out["problems"][0]
    missing = good[:3]                                                   # a fetch failed after retries
    assert "MCX_GOLD: missing" in run_js(js, missing)[0]["json"]["problems"][0]
    assert "invalid price" in run_js(js, [q("COMEX_GC", 1, -5)] + good[1:])[0]["json"]["problems"][0]
    future = [q("COMEX_GC", -3)] + good[1:]
    assert "future" in run_js(js, future)[0]["json"]["problems"][0]


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
def test_normalize_node_marks_slots():
    js = node_code("workflow_b_morning_prediction.json", "Normalize quotes")
    d = {"slot_date": "2026-11-19", "quotes": [
        {"instrument": "COMEX_GC", "price": 1, "currency": "USD", "provider": "p", "as_of": "x"},
        {"instrument": "MCX_GOLD", "price": 2, "currency": "INR", "provider": "p", "as_of": "x"}]}
    out = [o["json"] for o in run_js(js, [d])]
    assert out[0]["slot"] == "MORNING" and out[0]["slot_date"] == "2026-11-19"
    assert out[1]["slot"] is None                                         # MCX is not a training snapshot


@pytest.mark.skipif(not HAVE_NODE, reason="node not installed")
def test_sheet_row_and_duplicate_actual_nodes():
    js = node_code("workflow_b_morning_prediction.json", "Build sheet row")
    p = {"sheet_row": ["2026-11-19", 1, 2, 3, "UP", 5, "", "", "PENDING"], "telegram_text": "hi"}
    o = run_js(js, [p])[0]["json"]
    assert o["Date"] == "2026-11-19" and o["Accuracy Status"] == "PENDING" and o["telegram_text"] == "hi"
    js = node_code("workflow_c_accuracy_evaluation.json", "Actual row (only if newly inserted)")
    g = "const $ = () => ({first: () => ({json: {trading_date: '2026-11-19', contract_key: 'SPOT', close: 1, source: 's'}})});"
    assert run_js(js.replace("$('Check close is fresh')", "$()"), [{"inserted": False}], g) == []
    row = run_js(js.replace("$('Check close is fresh')", "$()"), [{"inserted": True}], g)
    assert row[0]["json"]["Close"] == 1
