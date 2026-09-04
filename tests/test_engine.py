"""Unit tests for rules/engine.py's repair orchestration, independent of eval cases."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from orderguard.rules.engine import RuleEngine
from orderguard.schemas.account_state import AccountState, OpenOrder, Position
from orderguard.schemas.market_snapshot import (
    AssetMeta,
    MarketClock,
    MarketSnapshot,
    Quote,
)
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import Decision, Disposition
from orderguard.schemas.user_constraints import UserConstraints

NOW = datetime(2026, 8, 27, 14, 30, tzinfo=timezone.utc)
CONSTRAINTS = UserConstraints(max_position_pct=Decimal(15))


def _state(**overrides) -> AccountState:
    defaults = {
        "account_id": "acct_1",
        "as_of": NOW,
        "account_type": "margin",
        "equity": Decimal("50000.00"),
        "cash": Decimal("50000.00"),
        "buying_power": Decimal("50000.00"),
        "pattern_day_trader": False,
        "daytrade_count": 0,
    }
    defaults.update(overrides)
    return AccountState(**defaults)


def _market(**overrides) -> MarketSnapshot:
    defaults = {"as_of": NOW, "clock": MarketClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW)}
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _asset(symbol: str) -> AssetMeta:
    return AssetMeta(symbol=symbol, tradable=True, fractionable=True, shortable=True, asset_class="us_equity")


def test_engine_allows_clean_basket() -> None:
    state = _state()
    plan = OrderPlan(
        account_id="acct_1",
        source_instruction="buy MSFT",
        orders=(Order(symbol="MSFT", side="buy", order_type="market", notional=Decimal("2000.00")),),
    )
    market = _market(assets=(_asset("MSFT"),))
    final_plan, report = RuleEngine().evaluate(plan, state, market, CONSTRAINTS)
    assert report.decision == Decision.ALLOW
    assert report.rules_fired == ()
    assert final_plan.orders == plan.orders


def test_engine_blocks_unrepairable_violation() -> None:
    # equity large enough that the concentration cap (15% of 60000 = 9000) doesn't
    # also fire on an 8000 buy -- this test isolates R1 specifically.
    state = _state(equity=Decimal("60000.00"), buying_power=Decimal("5000.00"))
    plan = OrderPlan(
        account_id="acct_1",
        source_instruction="buy TSLA",
        orders=(Order(symbol="TSLA", side="buy", order_type="market", notional=Decimal("8000.00")),),
    )
    market = _market(assets=(_asset("TSLA"),))
    final_plan, report = RuleEngine().evaluate(plan, state, market, CONSTRAINTS)
    assert report.decision == Decision.BLOCK
    assert final_plan.orders == ()
    assert final_plan.cancellations == ()
    assert {f.rule_id for f in report.rules_fired} == {"R1_BUYING_POWER"}
    assert report.rules_fired[0].disposition == Disposition.BLOCKED


def test_engine_cascading_repair_credits_both_rules() -> None:
    """R4's cancellation fixing R3 as a side effect must still record R3 as fired
    (disposition REPAIRED), not silently drop it -- this is case_017's scenario."""
    state = _state(
        equity=Decimal("30000.00"),
        positions=(
            Position(
                symbol="ORCL",
                qty=Decimal(20),
                avg_entry_price=Decimal("150.00"),
                current_price=Decimal("150.00"),
                market_value=Decimal("3000.00"),
                cost_basis=Decimal("3000.00"),
                unrealized_pl=Decimal("0.00"),
                asset_class="us_equity",
                fractionable=True,
                shortable=True,
                tradable=True,
                opened_at=NOW,
            ),
        ),
        open_orders=(
            OpenOrder(
                order_id="ord_orcl_1",
                symbol="ORCL",
                side="buy",
                qty=Decimal(30),
                order_type="limit",
                limit_price=Decimal("148.00"),
                status="open",
                submitted_at=NOW,
            ),
        ),
    )
    plan = OrderPlan(
        account_id="acct_1",
        source_instruction="buy 10 more ORCL",
        orders=(Order(symbol="ORCL", side="buy", order_type="market", qty=Decimal(10)),),
    )
    market = _market(quotes=(Quote(symbol="ORCL", last_price=Decimal("150.00"), as_of=NOW),), assets=(_asset("ORCL"),))
    final_plan, report = RuleEngine().evaluate(plan, state, market, CONSTRAINTS)

    assert report.decision == Decision.ALLOW_WITH_REPAIRS
    fired_ids = {f.rule_id for f in report.rules_fired}
    assert fired_ids == {"R3_CONCENTRATION", "R4_OPEN_ORDERS"}
    assert all(f.disposition == Disposition.REPAIRED for f in report.rules_fired)
    assert final_plan.cancellations == ("ord_orcl_1",)
    assert final_plan.orders[0].qty == Decimal(10)  # unchanged: already fits once the stale order is gone


def test_engine_records_r4_when_naive_plan_already_cancels_it() -> None:
    """Reproduces the live case_003 bug: a compiled plan that ALREADY includes the
    cancellation for a stacking open order must still surface R4 in rules_fired
    (disposition REPAIRED) -- otherwise the trader never learns the conflict existed."""
    state = _state(
        open_orders=(
            OpenOrder(
                order_id="ord_ibm_1",
                symbol="IBM",
                side="buy",
                qty=Decimal(10),
                order_type="limit",
                limit_price=Decimal("190.00"),
                status="open",
                submitted_at=NOW,
            ),
        ),
    )
    plan = OrderPlan(
        account_id="acct_1",
        source_instruction="buy 15 more IBM",
        orders=(Order(symbol="IBM", side="buy", order_type="market", qty=Decimal(15)),),
        cancellations=("ord_ibm_1",),  # pre-emptively cancelled, e.g. by an overeager compiler
    )
    market = _market(quotes=(Quote(symbol="IBM", last_price=Decimal("190.00"), as_of=NOW),), assets=(_asset("IBM"),))
    final_plan, report = RuleEngine().evaluate(plan, state, market, CONSTRAINTS)

    assert report.decision == Decision.ALLOW_WITH_REPAIRS
    fired = {f.rule_id: f for f in report.rules_fired}
    assert "R4_OPEN_ORDERS" in fired
    assert fired["R4_OPEN_ORDERS"].disposition == Disposition.REPAIRED
    assert final_plan.cancellations == ("ord_ibm_1",)


def test_engine_warning_only_still_allows() -> None:
    from orderguard.schemas.account_state import Activity

    state = _state(
        recent_activity=(
            Activity(
                activity_id="act_1",
                activity_type="fill",
                symbol="SOFI",
                qty=Decimal(200),
                price=Decimal("7.50"),
                realized_pl=Decimal("-500.00"),
                transaction_time=NOW.replace(day=16),
            ),
        )
    )
    plan = OrderPlan(
        account_id="acct_1",
        source_instruction="buy SOFI",
        orders=(Order(symbol="SOFI", side="buy", order_type="market", qty=Decimal(100)),),
    )
    market = _market(quotes=(Quote(symbol="SOFI", last_price=Decimal("8.00"), as_of=NOW),), assets=(_asset("SOFI"),))
    final_plan, report = RuleEngine().evaluate(plan, state, market, CONSTRAINTS)

    assert report.decision == Decision.ALLOW
    assert {f.rule_id for f in report.rules_fired} == {"R5_WASH_SALE"}
    assert report.rules_fired[0].disposition == Disposition.WARNED
    assert final_plan.orders == plan.orders
