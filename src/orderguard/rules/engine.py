"""Runs all rules (R1-R7) over a basket, applies deterministic repairs where the repair
principle allows one, and derives the overall Decision.

Repair order matters: R2/R6 (deferral, removes basket orders) run before R4
(cancel-and-resize, cancels stale open orders) before R3 (concentration resize),
because each earlier repair can change what a later rule sees -- e.g. cancelling a
stale open order (R4) changes the worst-case position value R3 must check against
(case_017), and deferring a day-trade order (R2) changes which symbols even have a
buy in the basket for R3 to look at (case_003).

Two passes, not one: every repairable rule is first checked against the ORIGINAL
(unrepaired) plan to determine which rules fired at all. Only then does the
sequential repair loop run. A single combined pass would miss a rule an EARLIER
repair fixes as a side effect -- e.g. cancelling a stale open order (R4) can bring a
position back under the concentration cap (R3) with no resize needed, so R3 never
"fails" if it's only ever checked after R4 has already run. Checking against the
original plan first is what lets R3 still register as fired (disposition REPAIRED)
in that scenario (case_017), consistent with "every rule that failed at any point
during the run."

See CLAUDE.md's repair principle for which rules are repairable and why.
"""

from __future__ import annotations

from orderguard.rules.asset_eligibility import AssetEligibilityRule
from orderguard.rules.buying_power import BuyingPowerRule
from orderguard.rules.concentration import ConcentrationRule
from orderguard.rules.open_orders import OpenOrdersRule
from orderguard.rules.pdt import PdtRule
from orderguard.rules.session import SessionRule
from orderguard.rules.wash_sale import WashSaleRule
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import (
    Decision,
    Disposition,
    FiredRule,
    RiskReport,
    Severity,
)
from orderguard.schemas.user_constraints import UserConstraints

# Repairable rules, in the order their repairs must be applied (see module docstring).
_REPAIR_ORDER = (PdtRule(), SessionRule(), OpenOrdersRule(), ConcentrationRule())
# Non-repairable rules: R1/R7 BLOCK outright on failure, R5 only ever WARNs.
_NON_REPAIRABLE = (BuyingPowerRule(), WashSaleRule(), AssetEligibilityRule())
# All seven, in id order, for the final "every rule, pass or fail" pass.
_ALL_RULES = (
    BuyingPowerRule(),
    PdtRule(),
    ConcentrationRule(),
    OpenOrdersRule(),
    WashSaleRule(),
    SessionRule(),
    AssetEligibilityRule(),
)


def _code(rule) -> str:
    return f"{rule.id}_{rule.name.upper()}"


class RuleEngine:
    """Evaluates a basket against all seven rules, repairing what the repair principle
    allows and blocking what it doesn't."""

    def evaluate(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> tuple[OrderPlan, RiskReport]:
        """Runs the full repair-then-decide pipeline over `plan`.

        Returns:
            `(final_plan, report)`. When `report.decision` is BLOCK, `final_plan` has
            no orders and no cancellations -- nothing in a blocked basket reaches the
            broker, regardless of how far repair got before an unrepairable rule
            stopped it.
        """
        original_failures = {}
        for rule in _REPAIR_ORDER:
            result = rule.check(plan, state, market, constraints)
            if not result.passed:
                original_failures[rule.id] = result

        working_plan = plan
        fired: list[FiredRule] = []

        for rule in _REPAIR_ORDER:
            current = rule.check(working_plan, state, market, constraints)
            if current.passed:
                if rule.id in original_failures:
                    # Failed on the original plan, but an earlier repair already fixed
                    # it as a side effect (e.g. R4 cancelling an open order can bring
                    # R3 back under cap with no resize needed) -- still counts as fired.
                    source = original_failures[rule.id]
                    fired.append(
                        FiredRule(
                            rule_id=_code(rule),
                            severity=rule.severity,
                            disposition=Disposition.REPAIRED,
                            explanation=source.explanation,
                            order_index=source.order_index,
                        )
                    )
                continue

            repaired_plan = rule.repair(working_plan, state, market, constraints, current)
            if repaired_plan == working_plan and not rule.always_repairable:
                # A genuine no-fix case (e.g. R2/R6 when deferral would empty the basket).
                disposition = Disposition.BLOCKED
            else:
                # Either repair() changed the plan, or (always_repairable rules only)
                # the fix was already present -- both count as REPAIRED.
                disposition = Disposition.REPAIRED
                working_plan = repaired_plan
            source = original_failures.get(rule.id, current)
            fired.append(
                FiredRule(
                    rule_id=_code(rule),
                    severity=rule.severity,
                    disposition=disposition,
                    explanation=source.explanation,
                    order_index=source.order_index,
                )
            )

        for rule in _NON_REPAIRABLE:
            result = rule.check(working_plan, state, market, constraints)
            if result.passed:
                continue
            disposition = Disposition.BLOCKED if rule.severity == Severity.BLOCKING else Disposition.WARNED
            fired.append(
                FiredRule(
                    rule_id=_code(rule),
                    severity=rule.severity,
                    disposition=disposition,
                    explanation=result.explanation,
                    order_index=result.order_index,
                )
            )

        final_results = tuple(rule.check(working_plan, state, market, constraints) for rule in _ALL_RULES)

        if any(f.disposition == Disposition.BLOCKED for f in fired):
            decision = Decision.BLOCK
            final_plan = plan.model_copy(update={"orders": (), "cancellations": ()})
        elif any(f.disposition == Disposition.REPAIRED for f in fired):
            decision = Decision.ALLOW_WITH_REPAIRS
            final_plan = working_plan
        else:
            decision = Decision.ALLOW
            final_plan = working_plan

        report = RiskReport(
            plan_id=plan.plan_id,
            decision=decision,
            rule_results=final_results,
            rules_fired=tuple(fired),
        )
        return final_plan, report
