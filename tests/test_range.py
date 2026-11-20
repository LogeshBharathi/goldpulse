from src.config import load_config
from src.models import conformal_quantile, range_around, volatility_ratio


def test_conformal_rank_uses_the_finite_sample_correction():
    errors = list(range(1, 11))  # n = 10, coverage 0.8 -> ceil(11*0.8) = 9 -> value 9
    assert conformal_quantile(errors, 0.80) == 9
    assert conformal_quantile([], 0.80) is None


def test_minimum_width_and_volatility_scaling():
    cfg = load_config()["range"]
    calm_lo, calm_hi = range_around(10000, [100] * 30, cfg, recent_moves=[0.001] * 10, baseline_moves=[0.001] * 30)
    wild_lo, wild_hi = range_around(10000, [100] * 30, cfg, recent_moves=[0.01] * 10, baseline_moves=[0.001] * 30)
    assert calm_hi - calm_lo >= cfg["min_width_inr"]
    assert wild_hi - wild_lo > calm_hi - calm_lo


def test_volatility_ratio_is_clipped():
    assert volatility_ratio([1, 1], [0.01], low=0.5, high=3) == 3
    assert volatility_ratio([], [], low=0.5, high=3) == 1


def test_range_uses_only_the_configured_window():
    cfg = load_config()["range"]
    window = cfg["window_days"]
    old = [1000] * 5
    recent = [10] * window
    lo, hi = range_around(10000, old + recent, cfg, recent_moves=[0.001], baseline_moves=[0.001])
    # The huge old errors are outside the window, so the half-width stays near the minimum.
    assert hi - lo < 500
