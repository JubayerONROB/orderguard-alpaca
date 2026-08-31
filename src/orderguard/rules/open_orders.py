"""R4: conflicts and stacking between the basket and existing unfilled orders on the account.

Detection runs against the ORIGINAL account state and is unconditional: it fires
whenever the account has an existing open order on a symbol the basket also touches,
REGARDLESS of whether the plan already proposes cancelling it. If the plan already
cancels it, R4 still fires (disposition REPAIRED, explanation noting the cancellation
was already present) -- otherwise a compiler that pre-emptively cancels a stacking
order on its own initiative would make the conflict invisible: it never entered the
rule engine's field of view, so the trader would never learn it existed. Remediation
(adding a cancellation if one isn't already there) is a separate step from detection.

REPAIR: cancel-and-resize is arithmetically unique -- see CLAUDE.md's repair principle.
`always_repairable = True`: unlike R2/R6 (which can genuinely fail to repair if
deferral would empty the basket), cancelling never removes a basket order, so this
rule can never end up BLOCKED -- a repair() that changes nothing means the fix was
already present, not that no fix exists.
"""

from __future__ import annotations

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


def _stacking_open_order_ids(plan: OrderPlan, state: AccountState) -> list[str]:
    """Open order ids that stack with a basket order on the same symbol.

    Unconditional: does NOT exclude ids already present in `plan.cancellations` --
    see module docstring for why detection must not depend on remediation already
    having happened.

    Any side combination counts as stacking: an unfilled order on a symbol makes that
    symbol's position outcome ambiguous the moment a new basket order also touches it,
    regardless of whether the two orders are the same side.
    """
    basket_symbols = {o.symbol for o in plan.orders}
    return [oo.order_id for oo in state.open_orders if oo.symbol in basket_symbols]


class OpenOrdersRule:
    """Fails if the basket conflicts with (or double-stacks) an existing open order on a symbol."""

    id = "R4"
    name = "open_orders"
    severity = Severity.BLOCKING
    always_repairable = True

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        stacking_ids = _stacking_open_order_ids(plan, state)
        if not stacking_ids:
            return RuleResult(
                rule_id=self.id,
                rule_name=self.name,
                passed=True,
                severity=self.severity,
                explanation="no basket order shares a symbol with an existing open order",
            )

        stacking_id = stacking_ids[0]
        open_order = next(oo for oo in state.open_orders if oo.order_id == stacking_id)
        order_index = next(i for i, o in enumerate(plan.orders) if o.symbol == open_order.symbol)
        already_cancelled = stacking_id in set(plan.cancellations)
        status_note = (
            "; this basket already proposes cancelling it"
            if already_cancelled
            else "; not yet cancelled by this basket"
        )
        return RuleResult(
            rule_id=self.id,
            rule_name=self.name,
            passed=False,
            severity=self.severity,
            order_index=order_index,
            explanation=(
                f"{open_order.symbol} has an existing open order ({open_order.order_type} {open_order.side}, "
                f"id {open_order.order_id}, submitted {open_order.submitted_at}) that a new {open_order.symbol} "
                f"order in this basket would stack with{status_note}"
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
        stacking_ids = _stacking_open_order_ids(plan, state)
        already_cancelled = set(plan.cancellations)
        missing = [i for i in stacking_ids if i not in already_cancelled]
        if not missing:
            return plan  # already fully cancelled -- engine treats this as REPAIRED via always_repairable
        new_cancellations = tuple(plan.cancellations) + tuple(missing)
        return plan.model_copy(update={"cancellations": new_cancellations})
