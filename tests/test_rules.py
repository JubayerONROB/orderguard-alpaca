"""Unit tests for each rule module (R1-R7), with minimal inputs independent of eval cases."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from orderguard.rules.asset_eligibility import AssetEligibilityRule
from orderguard.rules.buying_power import BuyingPowerRule
from orderguard.rules.concentration import ConcentrationRule
from orderguard.rules.open_orders import OpenOrdersRule
from orderguard.rules.pdt import PdtRule
from orderguard.rules.session import SessionRule
from orderguard.rules.wash_sale import WashSaleRule
from orderguard.schemas.account_state import AccountState, Activity, OpenOrder, Position
from orderguard.schemas.market_snapshot import (
    AssetMeta,
    MarketClock,
    MarketSnapshot,
    Quote,
)
from orderguard.schemas.order_plan import Order, OrderPlan
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
    defaults = {
        "as_of": NOW,
        "clock": MarketClock(timestamp=NOW, is_open=True, next_open=NOW, next_close=NOW),
    }
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _plan(orders: tuple[Order, ...] = (), cancellations: tuple[str, ...] = ()) -> OrderPlan:
    return OrderPlan(account_id="acct_1", source_instruction="test", orders=orders, cancellations=cancellations)


# R1 buying_power ------------------------------------------------------------------


def test_buying_power_passes_when_basket_fits() -> None:
    state = _state(buying_power=Decimal("5000.00"))
    plan = _plan((Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("4000.00")),))
    result = BuyingPowerRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


def test_buying_power_fails_when_basket_exceeds() -> None:
    state = _state(buying_power=Decimal("5000.00"))
    plan = _plan((Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("8000.00")),))
    result = BuyingPowerRule().check(plan, state, _market(), CONSTRAINTS)
    assert not result.passed
    assert "8000.00" in result.explanation
    assert "5000.00" in result.explanation


def test_buying_power_nets_same_basket_sale() -> None:
    """A buy funded by a sale in the same basket must net against post-sale funds (case_018)."""
    state = _state(
        buying_power=Decimal("3000.00"),
        positions=(
            Position(
                symbol="AVGO",
                qty=Decimal(10),
                avg_entry_price=Decimal("450.00"),
                current_price=Decimal("500.00"),
                market_value=Decimal("5000.00"),
                cost_basis=Decimal("4500.00"),
                unrealized_pl=Decimal("500.00"),
                asset_class="us_equity",
                fractionable=True,
                shortable=True,
                tradable=True,
                opened_at=NOW,
            ),
        ),
    )
    plan = _plan(
        (
            Order(symbol="AVGO", side="sell", order_type="market", qty=Decimal(10)),
            Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("4000.00")),
        )
    )
    market = _market(quotes=(Quote(symbol="AVGO", last_price=Decimal("500.00"), as_of=NOW),))
    result = BuyingPowerRule().check(plan, state, market, CONSTRAINTS)
    assert result.passed  # 3000 (buying_power) + 5000 (sale proceeds) = 8000 >= 4000


# R2 pdt -----------------------------------------------------------------------


def test_pdt_passes_when_equity_above_threshold() -> None:
    state = _state(equity=Decimal("30000.00"), daytrade_count=3)
    result = PdtRule().check(_plan(), state, _market(), CONSTRAINTS)
    assert result.passed


def test_pdt_fails_on_same_day_close() -> None:
    state = _state(
        equity=Decimal("19000.00"),
        daytrade_count=3,
        positions=(
            Position(
                symbol="AAPL",
                qty=Decimal(50),
                avg_entry_price=Decimal("225.00"),
                current_price=Decimal("227.00"),
                market_value=Decimal("11350.00"),
                cost_basis=Decimal("11250.00"),
                unrealized_pl=Decimal("100.00"),
                asset_class="us_equity",
                fractionable=True,
                shortable=True,
                tradable=True,
                opened_at=NOW,
            ),
        ),
    )
    plan = _plan((Order(symbol="AAPL", side="sell", order_type="market", qty=Decimal(50)),))
    result = PdtRule().check(plan, state, _market(), CONSTRAINTS)
    assert not result.passed
    assert result.order_index == 0

    repaired = PdtRule().repair(plan, state, _market(), CONSTRAINTS, result)
    assert repaired == plan  # deferring the only order would empty the basket -> no-op


def test_pdt_repair_defers_when_other_orders_remain() -> None:
    state = _state(
        equity=Decimal("19000.00"),
        daytrade_count=3,
        positions=(
            Position(
                symbol="AAPL",
                qty=Decimal(50),
                avg_entry_price=Decimal("225.00"),
                current_price=Decimal("227.00"),
                market_value=Decimal("11350.00"),
                cost_basis=Decimal("11250.00"),
                unrealized_pl=Decimal("100.00"),
                asset_class="us_equity",
                fractionable=True,
                shortable=True,
                tradable=True,
                opened_at=NOW,
            ),
        ),
    )
    plan = _plan(
        (
            Order(symbol="AAPL", side="sell", order_type="market", qty=Decimal(50)),
            Order(symbol="MSFT", side="buy", order_type="market", notional=Decimal("1000.00")),
        )
    )
    result = PdtRule().check(plan, state, _market(), CONSTRAINTS)
    repaired = PdtRule().repair(plan, state, _market(), CONSTRAINTS, result)
    assert len(repaired.orders) == 1
    assert repaired.orders[0].symbol == "MSFT"


# R3 concentration ---------------------------------------------------------------


def test_concentration_passes_under_cap() -> None:
    state = _state(equity=Decimal("40000.00"))
    plan = _plan((Order(symbol="NFLX", side="buy", order_type="market", notional=Decimal("2000.00")),))
    result = ConcentrationRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


def test_concentration_fails_over_cap_and_repairs_to_whole_shares() -> None:
    state = _state(equity=Decimal("40000.00"))
    plan = _plan((Order(symbol="NFLX", side="buy", order_type="market", notional=Decimal("10000.00")),))
    market = _market(quotes=(Quote(symbol="NFLX", last_price=Decimal("600.00"), as_of=NOW),))
    result = ConcentrationRule().check(plan, state, market, CONSTRAINTS)
    assert not result.passed
    assert "15" in result.explanation

    repaired = ConcentrationRule().repair(plan, state, market, CONSTRAINTS, result)
    assert repaired.orders[0].qty == Decimal(10)  # floor(6000.00 headroom / 600.00) = 10
    assert repaired.orders[0].notional is None


def test_concentration_includes_pending_open_order_worst_case() -> None:
    """A pending open buy on the same symbol must count toward the worst case (case_017)."""
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
                order_id="ord_1",
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
    plan = _plan((Order(symbol="ORCL", side="buy", order_type="market", qty=Decimal(10)),))
    market = _market(quotes=(Quote(symbol="ORCL", last_price=Decimal("150.00"), as_of=NOW),))
    result = ConcentrationRule().check(plan, state, market, CONSTRAINTS)
    # 20 held (3000) + 30 pending @148 (4440) + 10 new (1500) = 8940 > cap 4500.
    assert not result.passed

    # Once the pending order is excluded (as if cancelled), the same basket passes.
    plan_with_cancel = _plan(
        (Order(symbol="ORCL", side="buy", order_type="market", qty=Decimal(10)),), cancellations=("ord_1",)
    )
    result_after_cancel = ConcentrationRule().check(plan_with_cancel, state, market, CONSTRAINTS)
    assert result_after_cancel.passed


# R4 open_orders -----------------------------------------------------------------


def test_open_orders_passes_with_no_conflict() -> None:
    state = _state()
    plan = _plan((Order(symbol="IBM", side="buy", order_type="market", qty=Decimal(15)),))
    result = OpenOrdersRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


def test_open_orders_fails_and_repairs_by_cancelling() -> None:
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
        )
    )
    plan = _plan((Order(symbol="IBM", side="buy", order_type="market", qty=Decimal(15)),))
    result = OpenOrdersRule().check(plan, state, _market(), CONSTRAINTS)
    assert not result.passed
    assert result.order_index == 0


def test_open_orders_still_fires_when_plan_already_cancels_it() -> None:
    """Detection must not depend on remediation already having happened: a compiler
    (or any upstream system) that pre-emptively cancels a stacking order must not make
    the conflict invisible to the rule engine -- it still fires, disposition REPAIRED,
    noting the cancellation was already present. This is the case_003 bug: R4 never
    fired because the compiler had already added the cancellation itself."""
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
        )
    )
    plan = _plan(
        (Order(symbol="IBM", side="buy", order_type="market", qty=Decimal(15)),),
        cancellations=("ord_ibm_1",),
    )
    result = OpenOrdersRule().check(plan, state, _market(), CONSTRAINTS)
    assert not result.passed  # still fires -- detection ignores pre-existing cancellations
    assert "already proposes cancelling" in result.explanation

    # repair() is a no-op (nothing left to cancel), but always_repairable means the
    # engine still dispositions this REPAIRED, not BLOCKED -- see test_engine.py.
    repaired = OpenOrdersRule().repair(plan, state, _market(), CONSTRAINTS, result)
    assert repaired == plan
    assert OpenOrdersRule().always_repairable is True

    repaired = OpenOrdersRule().repair(plan, state, _market(), CONSTRAINTS, result)
    assert repaired.cancellations == ("ord_ibm_1",)
    assert repaired.orders == plan.orders  # order itself is untouched, only cancellation added


# R5 wash_sale --------------------------------------------------------------------


def test_wash_sale_passes_with_no_recent_loss() -> None:
    state = _state()
    plan = _plan((Order(symbol="SOFI", side="buy", order_type="market", qty=Decimal(100)),))
    result = WashSaleRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


def test_wash_sale_fails_within_30_days_and_never_blocks() -> None:
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
    plan = _plan((Order(symbol="SOFI", side="buy", order_type="market", qty=Decimal(100)),))
    result = WashSaleRule().check(plan, state, _market(), CONSTRAINTS)
    assert not result.passed
    from orderguard.schemas.risk_report import Severity

    assert result.severity == Severity.WARNING


def test_wash_sale_ignores_loss_outside_window() -> None:
    state = _state(
        recent_activity=(
            Activity(
                activity_id="act_1",
                activity_type="fill",
                symbol="SOFI",
                qty=Decimal(200),
                price=Decimal("7.50"),
                realized_pl=Decimal("-500.00"),
                transaction_time=NOW.replace(month=7, day=1),
            ),
        )
    )
    plan = _plan((Order(symbol="SOFI", side="buy", order_type="market", qty=Decimal(100)),))
    result = WashSaleRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


# R6 session -----------------------------------------------------------------------


def test_session_passes_when_market_open() -> None:
    state = _state()
    plan = _plan((Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.00")),))
    result = SessionRule().check(plan, state, _market(), CONSTRAINTS)
    assert result.passed


def test_session_fails_when_closed_and_blocks_single_order() -> None:
    state = _state()
    plan = _plan((Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.00")),))
    closed_market = _market(clock=MarketClock(timestamp=NOW, is_open=False, next_open=NOW, next_close=NOW))
    result = SessionRule().check(plan, state, closed_market, CONSTRAINTS)
    assert not result.passed

    repaired = SessionRule().repair(plan, state, closed_market, CONSTRAINTS, result)
    assert repaired == plan  # deferring the only order would empty the basket -> no-op


def test_session_extended_hours_order_passes_when_closed() -> None:
    state = _state()
    plan = _plan(
        (Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.00"), extended_hours=True),)
    )
    closed_market = _market(clock=MarketClock(timestamp=NOW, is_open=False, next_open=NOW, next_close=NOW))
    result = SessionRule().check(plan, state, closed_market, CONSTRAINTS)
    assert result.passed


# R7 asset_eligibility --------------------------------------------------------------


def test_asset_eligibility_passes_for_tradable_buy() -> None:
    state = _state()
    plan = _plan((Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.00")),))
    market = _market(assets=(AssetMeta(symbol="AAPL", tradable=True, fractionable=True, shortable=True, asset_class="us_equity"),))
    result = AssetEligibilityRule().check(plan, state, market, CONSTRAINTS)
    assert result.passed


def test_asset_eligibility_fails_on_implied_short_not_shortable() -> None:
    state = _state()
    plan = _plan((Order(symbol="GME", side="sell", order_type="market", qty=Decimal(50)),))
    market = _market(assets=(AssetMeta(symbol="GME", tradable=True, fractionable=False, shortable=False, asset_class="us_equity"),))
    result = AssetEligibilityRule().check(plan, state, market, CONSTRAINTS)
    assert not result.passed
    assert "short" in result.explanation.lower()


def test_asset_eligibility_sell_within_held_qty_is_not_a_short() -> None:
    state = _state(
        positions=(
            Position(
                symbol="GME",
                qty=Decimal(50),
                avg_entry_price=Decimal("20.00"),
                current_price=Decimal("20.00"),
                market_value=Decimal("1000.00"),
                cost_basis=Decimal("1000.00"),
                unrealized_pl=Decimal("0.00"),
                asset_class="us_equity",
                fractionable=False,
                shortable=False,
                tradable=True,
                opened_at=NOW,
            ),
        )
    )
    plan = _plan((Order(symbol="GME", side="sell", order_type="market", qty=Decimal(50)),))
    market = _market(assets=(AssetMeta(symbol="GME", tradable=True, fractionable=False, shortable=False, asset_class="us_equity"),))
    result = AssetEligibilityRule().check(plan, state, market, CONSTRAINTS)
    assert result.passed
