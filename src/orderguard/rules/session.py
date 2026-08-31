"""R6: market clock, extended-hours eligibility, and order-type validity for the session.

Identical repair logic to R2 (PDT) -- see CLAUDE.md's repair principle: REPAIR by
deferral IF other orders in the basket still execute now; BLOCK if deferring the
session-invalid orders would empty the basket.
"""

from __future__ import annotations

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


def _is_session_invalid(order: Order, market: MarketSnapshot) -> bool:
    """True iff `order` can't execute in the current session: market closed and the
    order isn't flagged extended-hours eligible."""
    if market.clock.is_open:
        return False
    return not order.extended_hours


def _invalid_indices(plan: OrderPlan, market: MarketSnapshot) -> list[int]:
    return [i for i, o in enumerate(plan.orders) if _is_session_invalid(o, market)]


class SessionRule:
    """Fails if an order's type/extended-hours flag is invalid for the current market session."""

    id = "R6"
    name = "session"
    severity = Severity.BLOCKING
    always_repairable = False

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        invalid = _invalid_indices(plan, market)
        if not invalid:
            explanation = (
                f"market is open (as of {market.clock.timestamp})"
                if market.clock.is_open
                else "no orders in the basket require immediate execution while the market is closed"
            )
            return RuleResult(
                rule_id=self.id, rule_name=self.name, passed=True, severity=self.severity, explanation=explanation
            )

        i = invalid[0]
        order = plan.orders[i]
        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=False,
            severity=self.severity,
            order_index=i,
            explanation=(
                f"market is closed (next open {market.clock.next_open}) and the {order.symbol} order "
                f"is not extended-hours eligible"
            ),
        )

    def repair(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
        result: RuleResult,
    ) -> OrderPlan:
        invalid = set(_invalid_indices(plan, market))
        if not invalid:
            return plan
        remaining_orders = tuple(o for i, o in enumerate(plan.orders) if i not in invalid)
        if not remaining_orders:
            return plan
        return plan.model_copy(update={"orders": remaining_orders})
