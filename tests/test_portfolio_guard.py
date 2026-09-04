"""Unit tests for portfolio_guard.py -- the separate pre-flight gate, not R1-R7."""

from __future__ import annotations

from decimal import Decimal

from orderguard.research.portfolio_guard import (
    DEFAULT_MAX_DRAWDOWN_PCT,
    check_portfolio_guard,
)
from orderguard.research.schemas import StrategyState


def test_alive_strategy_within_drawdown_is_allowed() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.ALIVE,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(99_000),
    )
    assert verdict.allowed is True
    assert verdict.reasons == ()


def test_killed_strategy_is_blocked() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.KILLED,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(100_000),
    )
    assert verdict.allowed is False
    assert any("KILLED" in r for r in verdict.reasons)


def test_watch_strategy_is_allowed() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.WATCH,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(100_000),
    )
    assert verdict.allowed is True


def test_drawdown_beyond_threshold_is_blocked() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.ALIVE,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(94_000),  # 6% drawdown, default threshold 5%
    )
    assert verdict.allowed is False
    assert any("drawdown" in r for r in verdict.reasons)


def test_drawdown_exactly_at_threshold_is_allowed() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.ALIVE,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(95_000),  # exactly 5%, not "exceeds"
    )
    assert verdict.allowed is True


def test_both_failures_report_both_reasons() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.KILLED,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(90_000),
    )
    assert verdict.allowed is False
    assert len(verdict.reasons) == 2


def test_custom_drawdown_threshold_is_respected() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.ALIVE,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(89_000),  # 11% drawdown
        max_drawdown_pct=Decimal(10),
    )
    assert verdict.allowed is False


def test_equity_gain_is_never_a_drawdown() -> None:
    verdict = check_portfolio_guard(
        strategy_state=StrategyState.ALIVE,
        session_starting_equity=Decimal(100_000),
        current_equity=Decimal(110_000),
    )
    assert verdict.allowed is True


def test_default_threshold_constant_matches_documented_value() -> None:
    assert DEFAULT_MAX_DRAWDOWN_PCT == 5
