"""Round-trip tests for the core Pydantic contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from orderguard.schemas.account_state import AccountState, Position
from orderguard.schemas.market_snapshot import MarketClock, MarketSnapshot, Quote
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import Decision, RiskReport, RuleResult, Severity

NOW = datetime(2026, 8, 27, 14, 30, tzinfo=timezone.utc)


def test_account_state_round_trip() -> None:
    state = AccountState(
        account_id="acct_1",
        as_of=NOW,
        account_type="margin",
        equity=Decimal("10000.00"),
        cash=Decimal("10000.00"),
        buying_power=Decimal("10000.00"),
        pattern_day_trader=False,
        daytrade_count=0,
        positions=(
            Position(
                symbol="msft",
                qty=Decimal(10),
                avg_entry_price=Decimal("400.00"),
                current_price=Decimal("410.00"),
                market_value=Decimal("4100.00"),
                cost_basis=Decimal("4000.00"),
                unrealized_pl=Decimal("100.00"),
                asset_class="us_equity",
                fractionable=True,
                shortable=True,
                tradable=True,
                opened_at=NOW,
            ),
        ),
    )
    assert state.positions[0].symbol == "MSFT"

    round_tripped = AccountState.model_validate_json(state.model_dump_json())
    assert round_tripped == state


def test_order_requires_exactly_one_of_qty_or_notional() -> None:
    with pytest.raises(ValueError):
        Order(symbol="AAPL", side="buy", order_type="market", qty=Decimal(1), notional=Decimal(1))
    with pytest.raises(ValueError):
        Order(symbol="AAPL", side="buy", order_type="market")


def test_order_plan_allows_empty_orders() -> None:
    plan = OrderPlan(account_id="acct_1", source_instruction="do nothing", orders=())
    assert plan.orders == ()
    assert plan.cancellations == ()


def test_risk_report_round_trip() -> None:
    report = RiskReport(
        plan_id="plan_1",
        decision=Decision.ALLOW,
        rule_results=(
            RuleResult(
                rule_id="R1",
                rule_name="buying_power",
                passed=True,
                severity=Severity.BLOCKING,
                explanation="basket notional 2000.00 well under buying power 50000.00",
            ),
        ),
    )
    round_tripped = RiskReport.model_validate_json(report.model_dump_json())
    assert round_tripped == report


def test_market_snapshot_round_trip() -> None:
    snapshot = MarketSnapshot(
        as_of=NOW,
        quotes=(Quote(symbol="msft", last_price=Decimal("430.00"), as_of=NOW),),
        clock=MarketClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW),
    )
    assert snapshot.quotes[0].symbol == "MSFT"
    round_tripped = MarketSnapshot.model_validate_json(snapshot.model_dump_json())
    assert round_tripped == snapshot
