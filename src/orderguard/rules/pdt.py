"""R2: day trades this basket creates, plus existing count, vs the $25k PDT equity rule.

REPAIR by deferral IF other orders in the basket still execute today; BLOCK if deferring
the day-trade orders would empty the basket (see CLAUDE.md's repair principle -- this is
a pure timing shift, not a resize, so it's uniquely determined: just don't do THAT trade
today).
"""

from __future__ import annotations

from decimal import Decimal

from orderguard.rules._util import get_position, is_sell
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints

PDT_EQUITY_THRESHOLD = Decimal(25000)
"""Below this equity, Reg T's pattern-day-trader restriction applies."""
PDT_MAX_DAY_TRADES = 3
"""A 4th day trade within the rolling 5-business-day window trips the restriction."""


def _is_day_trade_order(order: Order, state: AccountState) -> bool:
    """True iff `order` closes a position opened the same calendar day as `state.as_of`."""
    if not is_sell(order):
        return False
    position = get_position(state, order.symbol)
    if position is None:
        return False
    return position.opened_at.date() == state.as_of.date()


def _day_trade_indices(plan: OrderPlan, state: AccountState) -> list[int]:
    return [i for i, o in enumerate(plan.orders) if _is_day_trade_order(o, state)]


class PdtRule:
    """Fails if the basket would push a sub-$25k account over 3 day trades in 5 days."""

    id = "R2"
    name = "pdt"
    severity = Severity.BLOCKING
    always_repairable = False

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        if state.equity >= PDT_EQUITY_THRESHOLD:
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=True,
                severity=self.severity,
                explanation=f"equity {state.equity} >= {PDT_EQUITY_THRESHOLD} PDT threshold, day trades unrestricted",
            )

        dt_indices = _day_trade_indices(plan, state)
        projected = state.daytrade_count + len(dt_indices)

        if not dt_indices:
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=True,
                severity=self.severity,
                explanation=f"basket creates 0 day trades; {state.daytrade_count} already used, under the {PDT_MAX_DAY_TRADES}-trade limit",
            )

        if state.pattern_day_trader or projected > PDT_MAX_DAY_TRADES:
            i = dt_indices[0]
            order = plan.orders[i]
            reason = (
                "account is already flagged pattern_day_trader"
                if state.pattern_day_trader
                else f"would be day trade #{projected} ({state.daytrade_count} used + {len(dt_indices)} in this basket), exceeding the {PDT_MAX_DAY_TRADES}-trade limit"
            )
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=False,
                severity=self.severity,
                order_index=i,
                explanation=(
                    f"{order.symbol} was opened today (same calendar day as as_of); closing it now "
                    f"{reason} for an account with equity {state.equity} < {PDT_EQUITY_THRESHOLD}"
                ),
            )

        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=True,
            severity=self.severity,
            explanation=f"basket creates {len(dt_indices)} day trade(s); {state.daytrade_count}+{len(dt_indices)}={projected} <= {PDT_MAX_DAY_TRADES} limit",
        )

    def repair(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
        result: RuleResult,
    ) -> OrderPlan:
        dt_indices = _day_trade_indices(plan, state)
        if not dt_indices:
            return plan

        excess = (state.daytrade_count + len(dt_indices)) - PDT_MAX_DAY_TRADES
        if state.pattern_day_trader:
            excess = len(dt_indices)
        if excess <= 0:
            return plan

        defer_indices = set(dt_indices[:excess])
        remaining_orders = tuple(o for i, o in enumerate(plan.orders) if i not in defer_indices)
        if not remaining_orders:
            return plan
        return plan.model_copy(update={"orders": remaining_orders})
