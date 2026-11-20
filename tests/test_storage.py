from src.forecast import FORECAST_FIELDS
from src.storage import read_csv, upsert_csv, write_json, read_json


def test_upsert_replaces_the_same_key_and_keeps_a_scored_actual(tmp_path):
    path = tmp_path / "forecasts.csv"
    key = ["target_date", "kind", "model"]
    first = {"target_date": "2026-11-21", "kind": "evening", "made_at_ist": "a", "model": "beta",
             "estimate": "11488", "lo": "11300", "hi": "11600", "actual": "11510", "error": "22"}
    upsert_csv(path, [first], key, FORECAST_FIELDS)
    upsert_csv(path, [{**first, "estimate": "11500", "actual": "", "error": ""}], key, FORECAST_FIELDS)
    rows = read_csv(path)
    assert len(rows) == 1
    assert rows[0]["estimate"] == "11500"
    assert rows[0]["actual"] == "11510"
    assert rows[0]["error"] == "22"


def test_json_round_trip(tmp_path):
    path = tmp_path / "params.json"
    write_json(path, {"beta": 0.8})
    assert read_json(path, {})["beta"] == 0.8
    assert read_json(tmp_path / "missing.json", {}) == {}
