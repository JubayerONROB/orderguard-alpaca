"""R7: tradable, fractionable, and shortable flags for every symbol in the basket.

An implied short is detected as a SELL order whose quantity exceeds the currently held
quantity for that symbol (no explicit "short" flag exists on `Order` -- selling more
than you hold is definitionally opening a short position). When that's the case, the
symbol's `shortable` flag is checked; if false, the order is ineligible.

BLOCK, no repair: an eligibility failure has nothing to resize -- either the broker
will let you short/trade it, or it won't -- see CLAUDE.md's repair principle.
"""

from __future__ import annotations

from decimal import Decimal

from orderguard.rules._util import get_position, is_sell
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import AssetMeta, MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


def _asset_meta(market: MarketSnapshot, symbol: str) -> AssetMeta | None:
    for asset in market.assets:
        if asset.symbol == symbol:
            return asset
    return None


def _held_qty(state: AccountState, symbol: str) -> Decimal:
    position = get_position(state, symbol)
    return position.qty if position is not None else Decimal(0)


class AssetEligibilityRule:
    """Fails if an order requires a flag (tradable/shortable) the asset lacks."""

    id = "R7"
    name = "asset_eligibility"
    severity = Severity.BLOCKING

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        for i, order in enumerate(plan.orders):
            asset = _asset_meta(market, order.symbol)
            if asset is None:
                return RuleResult(
                    rule_id=self.id,
                    rule_name=self.name,
                    passed=False,
                    severity=self.severity,
                    order_index=i,
                    explanation=f"no asset eligibility data for {order.symbol}",
                )

            if not asset.tradable:
                return RuleResult(
                    rule_id=self.id,
                    rule_name=self.name,
                    passed=False,
                    severity=self.severity,
                    order_index=i,
                    explanation=f"{order.symbol} is not tradable",
                )

            if is_sell(order):
                held = _held_qty(state, order.symbol)
                order_qty = order.qty if order.qty is not None else None
                if order_qty is not None and order_qty > held and not asset.shortable:
                    return RuleResult(
                        rule_id=self.id,
                        rule_name=self.name,
                        passed=False,
                        severity=self.severity,
                        order_index=i,
                        explanation=(
                            f"sell qty {order_qty} for {order.symbol} exceeds held qty {held}, "
                            f"an implied short, but {order.symbol} is not shortable"
                        ),
                    )

        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=True,
            severity=self.severity,
            explanation="every order's symbol is tradable, and any implied short is on a shortable asset",
        )
