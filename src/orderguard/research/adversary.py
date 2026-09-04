"""Tries to break a strategy hypothesis by re-running backtest_engine.py across a small,
fixed grid of perturbed entry/exit parameters. This is the "try to break it, not just
believe it" stage -- a hypothesis that only looks good at its exact proposed parameters
is fragile, and fragile strategies are precisely what portfolio_manager.py should not be
handed capital for.

Pure and deterministic: same grid, same bars, same score every time.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from orderguard.research.backtest_engine import BacktestBar, run_backtest
from orderguard.research.schemas import (
    BacktestReport,
    EntryRule,
    ExitRule,
    RobustnessReport,
    StrategyHypothesis,
)

PARAMETER_PERTURBATIONS: tuple[Decimal, ...] = (Decimal("-0.25"), Decimal(0), Decimal("0.25"))
"""Each of entry's and exit's primary parameter is scaled by (1 + factor) for each of
these factors, independently -- a 3x3 = 9-point grid including the unperturbed baseline
at (0, 0)."""

DEGRADATION_TO_SCORE_SCALE = Decimal(10)
"""Every 10 percentage points a perturbed point's OOS return falls short of the
baseline's OOS return costs 100 points of parameter_sensitivity_score -- i.e. an average
10pp degradation across the grid fully zeroes the score."""

SHARPE_GAP_TO_SCORE_SCALE = Decimal(20)
"""Every 1.0 of |train_sharpe - oos_sharpe| costs 20 points of oos_stability_score."""

SENSITIVITY_WEIGHT = Decimal("0.6")
STABILITY_WEIGHT = Decimal("0.4")

PASS_THRESHOLD = Decimal(50)
"""overall_score at or above this passes -- matches the KILLED boundary lifecycle.py
uses, so a strategy the adversary fails is never something the lifecycle would call ALIVE."""


def _perturb_entry(entry: EntryRule, factor: Decimal) -> EntryRule:
    if entry.kind == "ma_crossover":
        fast = max(1, round(entry.fast_ma_days * (1 + factor)))
        slow = max(fast + 1, round(entry.slow_ma_days * (1 + factor)))
        return entry.model_copy(update={"fast_ma_days": fast, "slow_ma_days": slow})
    lookback = max(1, round(entry.lookback_days * (1 + factor)))
    return entry.model_copy(update={"lookback_days": lookback})


def _perturb_exit(exit_rule: ExitRule, factor: Decimal) -> ExitRule:
    if exit_rule.kind == "atr_stop":
        multiple = max(Decimal("0.1"), exit_rule.atr_multiple * (1 + factor))
        return exit_rule.model_copy(update={"atr_multiple": multiple})
    if exit_rule.kind == "trailing_stop_pct":
        pct = max(Decimal("0.1"), exit_rule.trailing_pct * (1 + factor))
        return exit_rule.model_copy(update={"trailing_pct": pct})
    days = max(1, round(exit_rule.hold_days * (1 + factor)))
    return exit_rule.model_copy(update={"hold_days": days})


def _sensitivity_score(baseline_oos_return: Decimal, grid_oos_returns: list[Decimal]) -> Decimal:
    degradations = [max(Decimal(0), baseline_oos_return - r) for r in grid_oos_returns]
    avg_degradation = sum(degradations) / len(degradations) if degradations else Decimal(0)
    score = Decimal(100) - avg_degradation * DEGRADATION_TO_SCORE_SCALE
    return max(Decimal(0), min(Decimal(100), score))


def _stability_score(baseline_report: BacktestReport) -> Decimal:
    gap = abs(baseline_report.train_metrics.sharpe - baseline_report.oos_metrics.sharpe)
    score = Decimal(100) - gap * SHARPE_GAP_TO_SCORE_SCALE
    return max(Decimal(0), min(Decimal(100), score))


def stress_test(hypothesis: StrategyHypothesis, symbol: str, bars: Sequence[BacktestBar]) -> RobustnessReport:
    """Re-backtests `hypothesis` across the perturbation grid and scores how much it
    degrades. Raises whatever `run_backtest` raises (e.g. `InsufficientHistoryError`) --
    an adversary can't stress-test a hypothesis it can't even backtest once."""
    grid_oos_returns: list[Decimal] = []
    baseline_report: BacktestReport | None = None

    for entry_factor in PARAMETER_PERTURBATIONS:
        for exit_factor in PARAMETER_PERTURBATIONS:
            perturbed = hypothesis.model_copy(
                update={
                    "entry": _perturb_entry(hypothesis.entry, entry_factor),
                    "exit": _perturb_exit(hypothesis.exit, exit_factor),
                }
            )
            report = run_backtest(perturbed, symbol, bars)
            grid_oos_returns.append(report.oos_metrics.total_return_pct)
            if entry_factor == 0 and exit_factor == 0:
                baseline_report = report

    assert baseline_report is not None  # (0, 0) is always in the grid

    sensitivity_score = _sensitivity_score(baseline_report.oos_metrics.total_return_pct, grid_oos_returns)
    stability_score = _stability_score(baseline_report)
    overall_score = sensitivity_score * SENSITIVITY_WEIGHT + stability_score * STABILITY_WEIGHT

    return RobustnessReport(
        strategy_name=hypothesis.name,
        parameter_sensitivity_score=sensitivity_score,
        oos_stability_score=stability_score,
        overall_score=overall_score,
        verdict="PASS" if overall_score >= PASS_THRESHOLD else "FAIL",
        grid_points_tested=len(grid_oos_returns),
    )
