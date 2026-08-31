"""R1: basket notional, summed across all orders and netted against same-basket sales,
vs available buying power.

BLOCKING, no repair: per CLAUDE.md's repair principle, a buying-power shortfall is an
external limit, not something derived from what the trader stated, so there's no
principled way to auto-resize it -- unlike R3 (concentration), where the trader gave
OrderGuard an explicit cap to round down to.
"""

from __future__ import annotations

from decimal import Decimal

from orderguard.rules._util import is_buy, is_sell, order_notional_value
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


class BuyingPowerRule:
    """Fails if the basket's buy notional, net of same-basket sell proceeds, exceeds
    buying power.

    Netting matters: a buy funded by a sale in the same basket must be checked against
    POST-SALE funds, not pre-trade buying power alone -- checking each order in
    isolation would wrongly block an affordable basket (case_018) and wrongly allow an
    unaffordable one where each order individually fits (case_016).
    """

    id = "R1"
    name = "buying_power"
    severity = Severity.BLOCKING

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        total_buys = sum((order_notional_value(o, market) for o in plan.orders if is_buy(o)), start=Decimal(0))
        total_sells = sum((order_notional_value(o, market) for o in plan.orders if is_sell(o)), start=Decimal(0))
        net_needed = total_buys - total_sells

        if net_needed <= state.buying_power:
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=True,
                severity=self.severity,
                explanation=(
                    f"basket needs {net_needed} net buying power ({total_buys} in buys, "
                    f"{total_sells} in same-basket sell proceeds netted off), within the "
                    f"{state.buying_power} available"
                ),
            )

        buy_indices = [i for i, o in enumerate(plan.orders) if is_buy(o)]
        settlement_note = (
            f" ({state.unsettled_cash} of cash is unsettled and excluded from a cash account's buying power)"
            if state.account_type == "cash" and state.unsettled_cash > 0
            else ""
        )
        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=False,
            severity=self.severity,
            order_index=buy_indices[0] if buy_indices else None,
            explanation=(
                f"basket needs {net_needed} net buying power ({total_buys} in buys, "
                f"{total_sells} in same-basket sell proceeds netted off), exceeding the "
                f"{state.buying_power} available{settlement_note}"
            ),
        )
