"""Unit tests for adversary.py against synthetic price series -- no network, no LLM."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from orderguard.research.adversary import stress_test
from orderguard.research.backtest_engine import MIN_BARS, InsufficientHistoryError
from orderguard.research.schemas import EntryRule, ExitRule, StrategyHypothesis

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bars(closes: list[float]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(open=c, high=c, low=c, close=c, volume=1_000_000, timestamp=START + timedelta(days=i))
        for i, c in enumerate(closes)
    ]


def _hypothesis(entry: EntryRule, exit_rule: ExitRule) -> StrategyHypothesis:
    return StrategyHypothesis(
        name="Test Strategy",
        rationale="synthetic test hypothesis",
        universe=["TEST"],
        entry=entry,
        exit=exit_rule,
        generated_at=datetime.now(timezone.utc),
    )


def test_stress_test_reports_full_grid() -> None:
    closes = [100.0 + i for i in range(MIN_BARS)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )

    report = stress_test(hyp, "TEST", _bars(closes))

    assert report.grid_points_tested == 9  # 3x3 perturbation grid, including baseline
    assert 0 <= report.parameter_sensitivity_score <= 100
    assert 0 <= report.oos_stability_score <= 100
    assert 0 <= report.overall_score <= 100
    assert report.verdict in ("PASS", "FAIL")
    assert report.strategy_name == "Test Strategy"


def test_a_robust_uptrend_strategy_passes() -> None:
    """A strategy that profits consistently across a smoothly rising series, regardless
    of small parameter perturbations, should score well and PASS."""
    closes = [100.0 + i * 0.5 for i in range(MIN_BARS)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )

    report = stress_test(hyp, "TEST", _bars(closes))

    assert report.verdict == "PASS"


def test_a_strategy_with_no_trades_anywhere_is_not_penalized_into_fabricated_degradation() -> None:
    """A flat series triggers no trades at any grid point -- every OOS return is 0, so
    there's no degradation to measure and sensitivity should be neutral/high, not
    penalized for something that never happened."""
    closes = [100.0] * MIN_BARS
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )

    report = stress_test(hyp, "TEST", _bars(closes))

    assert report.parameter_sensitivity_score == 100


def test_stress_test_raises_on_insufficient_history() -> None:
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    with pytest.raises(InsufficientHistoryError):
        stress_test(hyp, "TEST", _bars([100.0] * (MIN_BARS - 1)))


def test_ma_crossover_perturbation_keeps_fast_below_slow() -> None:
    """Regression check: perturbing an ma_crossover's fast/slow params must never
    produce fast_ma_days >= slow_ma_days (which EntryRule itself would reject)."""
    closes = [100.0] * 10 + [100.0 + i for i in range(MIN_BARS - 10)]
    hyp = _hypothesis(
        EntryRule(kind="ma_crossover", lookback_days=5, fast_ma_days=3, slow_ma_days=4),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )

    report = stress_test(hyp, "TEST", _bars(closes))

    assert report.grid_points_tested == 9
