"""R5: repurchase within 30 days of a realized loss on the same security.

WARN, never repaired: a wash sale is a tax consequence the trader may knowingly accept
-- see CLAUDE.md's repair principle. There's no resize that fixes it (the problem is
purchase timing, not size), so it's surfaced but never blocks and never disposition
REPAIRED.
"""

from __future__ import annotations

from datetime import timedelta

from orderguard.rules._util import is_buy
from orderguard.schemas.account_state import AccountState, Activity
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints

WASH_SALE_WINDOW_DAYS = 30


def _recent_realized_loss(state: AccountState, symbol: str) -> Activity | None:
    """The most recent realized-loss fill on `symbol` within the wash-sale window, if any."""
    candidates = [
        a
        for a in state.recent_activity
        if a.symbol == symbol
        and a.realized_pl is not None
        and a.realized_pl < 0
        and (state.as_of - a.transaction_time) <= timedelta(days=WASH_SALE_WINDOW_DAYS)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.transaction_time)


class WashSaleRule:
    """Fails if a buy order in the basket would repurchase a symbol sold at a loss within 30 days."""

    id = "R5"
    name = "wash_sale"
    severity = Severity.WARNING

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        for i, order in enumerate(plan.orders):
            if not is_buy(order):
                continue
            loss = _recent_realized_loss(state, order.symbol)
            if loss is None:
                continue
            days_ago = (state.as_of - loss.transaction_time).days
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=False,
                severity=self.severity,
                order_index=i,
                explanation=(
                    f"{order.symbol} was sold at a realized loss of {loss.realized_pl} {days_ago} days ago "
                    f"({loss.transaction_time}), within the {WASH_SALE_WINDOW_DAYS}-day wash-sale window; "
                    f"this repurchase may not be deductible"
                ),
            )

        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=True,
            severity=self.severity,
            explanation=f"no buy in the basket repurchases a symbol sold at a loss within {WASH_SALE_WINDOW_DAYS} days",
        )

