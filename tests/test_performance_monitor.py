"""Unit tests for performance_monitor.py -- JSONL round-trip and P&L attribution."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from orderguard.research.performance_monitor import (
    MIN_FILLS_FOR_TREND,
    StrategyFill,
    attribute_performance,
    load_fills,
    log_fill,
)
from orderguard.schemas.account_state import AccountState, Position

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _fill(strategy_name: str = "NVDA Breakout", symbol: str = "NVDA", order_id: str = "order-1") -> StrategyFill:
    return StrategyFill(
        strategy_name=strategy_name,
        symbol=symbol,
        side="buy",
        size_text="$1,000",
        order_id=order_id,
        backtest_oos_sharpe=Decimal("1.2"),
        backtest_oos_return_pct=Decimal("8.5"),
        logged_at=NOW,
    )


def _position(symbol: str = "NVDA", unrealized_pl: str = "150.00") -> Position:
    return Position(
        symbol=symbol,
        qty=Decimal(10),
        avg_entry_price=Decimal(100),
        current_price=Decimal(115),
        market_value=Decimal(1150),
        cost_basis=Decimal(1000),
        unrealized_pl=Decimal(unrealized_pl),
        asset_class="us_equity",
        fractionable=True,
        shortable=True,
        tradable=True,
        opened_at=NOW,
    )


def _account(positions: tuple[Position, ...] = ()) -> AccountState:
    return AccountState(
        account_id="acct-1",
        as_of=NOW,
        account_type="margin",
        equity=Decimal(100_000),
        cash=Decimal(50_000),
        buying_power=Decimal(100_000),
        pattern_day_trader=False,
        daytrade_count=0,
        positions=positions,
    )


def test_log_and_load_round_trips(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy_log.jsonl"
    fill = _fill()
    log_fill(fill, log_path)

    loaded = load_fills(log_path)

    assert len(loaded) == 1
    assert loaded[0] == fill


def test_load_fills_from_nonexistent_log_returns_empty(tmp_path: Path) -> None:
    assert load_fills(tmp_path / "does_not_exist.jsonl") == ()


def test_multiple_fills_append_rather_than_overwrite(tmp_path: Path) -> None:
    log_path = tmp_path / "strategy_log.jsonl"
    log_fill(_fill(order_id="order-1"), log_path)
    log_fill(_fill(order_id="order-2"), log_path)

    loaded = load_fills(log_path)

    assert len(loaded) == 2
    assert {f.order_id for f in loaded} == {"order-1", "order-2"}


def test_attribution_below_trend_threshold_reports_insufficient_history() -> None:
    fills = (_fill(order_id="order-1"),)
    account = _account((_position(),))

    attributions = attribute_performance(fills, account)

    assert len(attributions) == 1
    assert attributions[0].fill_count == 1
    assert "insufficient live history" in attributions[0].status
    assert attributions[0].live_unrealized_pnl == Decimal("150.00")


def test_attribution_at_threshold_reports_a_real_summary() -> None:
    fills = tuple(_fill(order_id=f"order-{i}") for i in range(MIN_FILLS_FOR_TREND))
    account = _account((_position(),))

    attributions = attribute_performance(fills, account)

    assert attributions[0].fill_count == MIN_FILLS_FOR_TREND
    assert "insufficient" not in attributions[0].status


def test_symbol_with_no_open_position_contributes_zero_pnl() -> None:
    fills = (_fill(symbol="NVDA"),)
    account = _account(())  # position already closed / never filled

    attributions = attribute_performance(fills, account)

    assert attributions[0].live_unrealized_pnl == 0


def test_fills_are_grouped_by_strategy_not_flattened() -> None:
    fills = (
        _fill(strategy_name="Strategy A", symbol="NVDA", order_id="order-1"),
        _fill(strategy_name="Strategy B", symbol="AMD", order_id="order-2"),
    )
    account = _account((_position("NVDA", "150"), _position("AMD", "-50")))

    attributions = attribute_performance(fills, account)

    assert len(attributions) == 2
    by_name = {a.strategy_name: a for a in attributions}
    assert by_name["Strategy A"].live_unrealized_pnl == 150
    assert by_name["Strategy B"].live_unrealized_pnl == -50
