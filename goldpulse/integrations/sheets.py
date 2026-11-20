"""Google Sheets layout: row builders (used by n8n via the API) and an .xlsx template.

Import the generated template into Google Sheets (File > Import) or let the n8n
Google Sheets node append rows to a sheet that already has these tabs/headers.
History is preserved: rows are only ever appended; evaluation rows are appended to
'Prediction Accuracy' and 'Actual Prices' rather than overwriting 'Daily Predictions'.
"""
from __future__ import annotations

TABS = {
    "Daily Predictions": ["Date", "Previous MCX Price", "COMEX Change", "USD/INR Change",
                          "Predicted Direction", "Predicted Price", "Actual Price", "Prediction Error",
                          "Accuracy Status"],
    "Market Data": ["Timestamp UTC", "Timestamp IST", "Instrument", "Contract", "Price", "Currency",
                    "Provider", "Delayed", "Quality"],
    "Actual Prices": ["Trading Date", "Instrument", "Contract", "Close", "Source"],
    "Prediction Accuracy": ["Target Date", "Model Version", "Predicted Price", "Actual Close", "Abs Error",
                            "% Error", "Predicted Direction", "Actual Direction", "Direction Correct",
                            "Within Interval"],
    "Model Performance": ["Evaluated At", "Model Version", "Scope", "N", "MAE", "RMSE", "MAPE %",
                          "Directional Accuracy", "Precision UP", "Recall UP", "Precision DOWN", "Recall DOWN"],
}


def daily_prediction_row(p: dict) -> list:
    i = p["indicators"]
    # Actual price / error / status are blank at prediction time (a separate evaluation row is appended).
    return [str(p["target_date"]), p["previous_mcx_close"], i["comex_overnight_pct"], i["usdinr_move_pct"],
            p["direction"], p["predicted_mcx_price"], "", "", "PENDING"]


def accuracy_row(ev: dict) -> list:
    return [str(ev["target_date"]), ev["model_version"], ev["predicted_price"], ev["actual_close"],
            ev["abs_error"], ev["pct_error"], ev["predicted_direction"], ev["actual_direction"],
            ev["direction_correct"], ev["within_interval"]]


def write_template_xlsx(path: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); wb.remove(wb.active)
    for name, headers in TABS.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
        ws.freeze_panes = "A2"
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = max(14, len(str(col[0].value)) + 2)
    wb.save(path)
    return path
