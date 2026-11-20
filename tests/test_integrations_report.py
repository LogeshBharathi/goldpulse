from datetime import datetime, timezone

import pytest

from goldpulse.integrations.sheets import TABS, accuracy_row, daily_prediction_row, write_template_xlsx
from goldpulse.integrations.telegram import send_message
from goldpulse.ml.registry import train
from goldpulse.report import DISCLAIMER, render_report, render_telegram
from goldpulse.service import generate_prediction
from goldpulse.config import Settings
from tests.test_service_e2e import CAL_IN, CAL_US, NOW, add_live_quotes, seeded_repo


@pytest.fixture(scope="module")
def pred(synth):
    b = train(synth, 0.001, 250, is_synthetic=True)
    repo, h = seeded_repo(synth); add_live_quotes(repo, h)
    return generate_prediction(repo, b, Settings(), NOW, CAL_IN, CAL_US)


def test_report_matches_required_format(pred):
    t = render_report(pred)
    order = ["GOLDPULSE INDIA", "Date:", "MARKET INDICATORS", "- COMEX previous close:", "- COMEX latest price:",
             "- COMEX overnight movement:", "- USD/INR:", "- USD/INR movement:", "- XAU/USD:", "- Previous MCX close:",
             "PREDICTION", "- Expected direction:", "- Estimated MCX gold price:", "- Expected percentage change:",
             "- Expected 24K price per gram:", "- Expected 22K price per gram:", "- Prediction interval",
             "- Model confidence:", "ANALYSIS", "- Main bullish indicators:", "- Main bearish indicators:",
             "- Important market events:", "- Data quality warnings:", "- Reasons supporting the forecast:",
             "DISCLAIMER", DISCLAIMER]
    pos = [t.index(k) for k in order]
    assert pos == sorted(pos)
    assert "Not shown" in t or "calibrated" in t                      # confidence rule explicit
    assert "SYNTHETIC" in t                                           # limitation surfaced in the report


def test_report_never_shows_uncalibrated_confidence(pred):
    p = dict(pred, confidence=None)
    assert "Not shown" in render_report(p)
    p = dict(pred, confidence=0.61)
    assert "61% (statistically calibrated)" in render_report(p)


def test_telegram_message_contents(pred):
    t = render_telegram(pred)
    for k in ["Direction:", "MCX gold:", "24K", "22K", "COMEX overnight", "USD/INR", "Range", "Data:", DISCLAIMER]:
        assert k in t
    assert len(t) < 4096


def test_sheet_rows(pred):
    r = daily_prediction_row(pred)
    assert len(r) == len(TABS["Daily Predictions"]) == 9 and r[-1] == "PENDING" and r[6] == ""
    ev = {"target_date": "2026-11-19", "model_version": "v", "predicted_price": 1, "actual_close": 2, "abs_error": 1,
          "pct_error": 50, "predicted_direction": "UP", "actual_direction": "UP", "direction_correct": True,
          "within_interval": False}
    assert len(accuracy_row(ev)) == len(TABS["Prediction Accuracy"])


def test_template_xlsx_has_required_tabs(tmp_path):
    from openpyxl import load_workbook
    wb = load_workbook(write_template_xlsx(str(tmp_path / "t.xlsx")))
    assert wb.sheetnames == ["Daily Predictions", "Market Data", "Actual Prices", "Prediction Accuracy",
                             "Model Performance"]
    assert [c.value for c in wb["Daily Predictions"][1]] == ["Date", "Previous MCX Price", "COMEX Change",
        "USD/INR Change", "Predicted Direction", "Predicted Price", "Actual Price", "Prediction Error", "Accuracy Status"]


class _R:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


class _S:
    def __init__(self, r): self.r, self.calls = r, []
    def post(self, url, json=None, timeout=None): self.calls.append((url, json, timeout)); return self.r


def test_telegram_send_ok_failure_and_unconfigured():
    s = _S(_R(200, {"ok": True}))
    assert send_message("TOKEN", "123", "x" * 5000, session=s)["ok"]
    assert len(s.calls[0][1]["text"]) == 4096 and s.calls[0][2] == 10.0 and "botTOKEN" in s.calls[0][0]
    with pytest.raises(RuntimeError, match="failed"):
        send_message("T", "1", "x", session=_S(_R(400, {"ok": False, "description": "bad"})))
    with pytest.raises(RuntimeError, match="not configured"):
        send_message("", "", "x", session=s)
