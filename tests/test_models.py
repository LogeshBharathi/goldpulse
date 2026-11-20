from datetime import date

from src.config import load_config
from src.models import (beta_estimate, choose_champion, fit_beta, fit_premium, premium_estimate,
                        ridge_beta, walk_forward)


def test_ridge_beta_with_prior():
    assert ridge_beta([], [], lam=1.0, prior=1.0) == 1.0
    assert ridge_beta([1, 2], [1, 2], lam=0, prior=1) == 1
    # (1*2 + 1*1) / (1 + 1) = 1.5
    assert ridge_beta([1], [2], lam=1, prior=1) == 1.5


def test_fit_beta_uses_only_the_window():
    pairs = [(0.5, 1.0)] * 5 + [(0.0, 1.0)]
    assert fit_beta(pairs, lam=0, prior=1, window=1) == 0.0


def test_estimates():
    assert beta_estimate(11510, -0.05, 0.8) == 11510 * 0.96
    assert premium_estimate(10700, 0.07) == 10700 * 1.07


def test_premium_weights_recent_days_more():
    assert fit_premium([0.0, 0.0, 0.2], half_life=1) > fit_premium([0.2, 0.0, 0.0], half_life=1)


def test_champion_is_beta_until_the_window_is_full():
    cfg = load_config()
    window = cfg["champion"]["window_days"]
    short = {"beta": [1] * (window - 1), "premium": [0] * (window - 1)}
    assert choose_champion(short, window) == "beta"
    full = {"beta": [2] * window, "premium": [1] * window}
    assert choose_champion(full, window) == "premium"


def test_walk_forward_does_not_see_a_future_board():
    history = [(date(2026, 11, day), 12000 - day) for day in range(10, 16)]
    moves = {date(2026, 11, day): -0.001 for day in range(11, 16)}
    cfg = load_config()
    original = walk_forward(history, moves, cfg)
    changed = history[:-1] + [(history[-1][0], 1)]
    assert walk_forward(changed, moves, cfg)[:-1] == original[:-1]
