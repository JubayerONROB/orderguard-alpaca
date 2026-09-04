"""Unit tests for portfolio_manager.py using hand-built reports -- no backtest/adversary
needed, this module only consumes their output shapes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from orderguard.research.portfolio_manager import (
    PortfolioCandidate,
    build_allocation_plan,
)
from orderguard.research.schemas import (
    BacktestReport,
    PerformanceMetrics,
    RobustnessReport,
    StrategyState,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _metrics(sharpe: str = "1.5", max_dd: str = "10", trade_count: int = 20) -> PerformanceMetrics:
    return PerformanceMetrics(
        total_return_pct="15",
        sharpe=sharpe,
        win_rate_pct="55",
        max_drawdown_pct=max_dd,
        profit_factor="1.8",
        trade_count=trade_count,
    )


def _backtest(name: str, symbol: str, sharpe: str = "1.5", max_dd: str = "10") -> BacktestReport:
    return BacktestReport(
        strategy_name=name,
        symbol=symbol,
        train_metrics=_metrics(sharpe=sharpe, max_dd=max_dd),
        oos_metrics=_metrics(sharpe=sharpe, max_dd=max_dd),
        train_start=NOW,
        train_end=NOW,
        oos_start=NOW,
        oos_end=NOW,
    )


def _robustness(name: str, overall_score: str = "80") -> RobustnessReport:
    return RobustnessReport(
        strategy_name=name,
        parameter_sensitivity_score=overall_score,
        oos_stability_score=overall_score,
        overall_score=overall_score,
        verdict="PASS" if Decimal(overall_score) >= 50 else "FAIL",
        grid_points_tested=9,
    )


def test_killed_strategies_receive_no_allocation() -> None:
    candidates = [PortfolioCandidate(_backtest("A", "NVDA"), _robustness("A"), StrategyState.KILLED)]
    plan = build_allocation_plan(candidates)
    assert plan.entries == ()
    assert plan.cash_buffer_pct == 100


def test_no_candidates_means_all_cash() -> None:
    plan = build_allocation_plan([])
    assert plan.entries == ()
    assert plan.cash_buffer_pct == 100


def test_alive_and_watch_both_receive_allocation() -> None:
    candidates = [
        PortfolioCandidate(_backtest("A", "NVDA"), _robustness("A", "90"), StrategyState.ALIVE),
        PortfolioCandidate(_backtest("B", "AMD"), _robustness("B", "60"), StrategyState.WATCH),
    ]
    plan = build_allocation_plan(candidates)
    assert len(plan.entries) == 2
    assert plan.cash_buffer_pct == 20


def test_allocations_respect_the_cash_buffer() -> None:
    candidates = [
        PortfolioCandidate(_backtest("A", "NVDA"), _robustness("A", "90"), StrategyState.ALIVE),
        PortfolioCandidate(_backtest("B", "AMD"), _robustness("B", "80"), StrategyState.ALIVE),
    ]
    plan = build_allocation_plan(candidates, cash_buffer_pct=Decimal(30))
    total_allocated = sum(e.target_pct_of_equity for e in plan.entries)
    assert abs(total_allocated - 70) < Decimal("0.0001")
    assert plan.cash_buffer_pct == 30


def test_higher_robustness_and_sharpe_gets_more_allocation() -> None:
    candidates = [
        PortfolioCandidate(_backtest("Strong", "NVDA", sharpe="2.5", max_dd="5"), _robustness("Strong", "95"), StrategyState.ALIVE),
        PortfolioCandidate(_backtest("Weak", "AMD", sharpe="0.3", max_dd="25"), _robustness("Weak", "55"), StrategyState.ALIVE),
    ]
    plan = build_allocation_plan(candidates)
    by_name = {e.strategy_name: e.target_pct_of_equity for e in plan.entries}
    assert by_name["Strong"] > by_name["Weak"]


def test_watch_strategy_gets_less_than_identically_scored_alive() -> None:
    candidates = [
        PortfolioCandidate(_backtest("AliveOne", "NVDA"), _robustness("AliveOne", "80"), StrategyState.ALIVE),
        PortfolioCandidate(_backtest("WatchOne", "AMD"), _robustness("WatchOne", "80"), StrategyState.WATCH),
    ]
    plan = build_allocation_plan(candidates)
    by_name = {e.strategy_name: e.target_pct_of_equity for e in plan.entries}
    assert by_name["AliveOne"] > by_name["WatchOne"]
