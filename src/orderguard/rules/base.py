"""The `Rule` protocol every rule module (R1-R7) implements, and `RepairableRule` for
the subset (R2, R3, R4, R6) whose violations have a uniquely-determined fix -- see
CLAUDE.md's "repair principle" for which rules get which treatment and why.

A rule always sees the *whole* basket, not one order at a time -- R1 (buying_power),
R2 (pdt), R3 (concentration), and R4 (open_orders) are only meaningful at that
granularity. Every rule is a pure function of its four arguments: no I/O, no LLM
calls, no clock reads (the market clock comes from `MarketSnapshot`, not `datetime.now()`).
Same inputs must produce byte-identical output on every run.
"""

from __future__ import annotations

from typing import Protocol

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RuleResult, Severity
from orderguard.schemas.user_constraints import UserConstraints


class Rule(Protocol):
    """A single deterministic risk check, evaluated over an entire OrderPlan."""

    id: str
    """e.g. "R1"."""
    name: str
    """e.g. "buying_power". Combined with `id` as "R1_BUYING_POWER" to form the code
    used in `FiredRule.rule_id` and eval case `expected.rules_fired`."""
    severity: Severity

    def check(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
    ) -> RuleResult:
        """Evaluates this rule against the full basket and current account/market state.

        Must be pure and deterministic: same arguments in, same `RuleResult` out.
        """
        ...


class RepairableRule(Rule, Protocol):
    """A `Rule` whose failures have a mechanically-unique fix (see CLAUDE.md)."""

    always_repairable: bool
    """True for R3/R4: their repair can never fail once attempted (a resize or a
    cancellation always succeeds), so a `repair()` call that changes nothing means the
    fix was already present, not that no fix exists -- the engine dispositions these
    REPAIRED even on a no-op. False for R2/R6: deferring the offending order(s) can
    genuinely fail (if it would empty the basket), so a no-op there really does mean
    BLOCKED."""

    def repair(
        self,
        plan: OrderPlan,
        state: AccountState,
        market: MarketSnapshot,
        constraints: UserConstraints,
        result: RuleResult,
    ) -> OrderPlan:
        """Returns a new plan with this rule's violation addressed.

        Only called when `result.passed` is False (from a prior `check()` call against
        `plan`). May return `plan` unchanged either because the fix was already present
        (`always_repairable = True`) or because THIS instance of the failure has no
        valid repair at all (`always_repairable = False`, e.g. R2/R6 when deferring the
        offending order(s) would leave the basket empty) -- the engine tells these two
        cases apart via `always_repairable`, not by inspecting the returned plan.
        """
        ...
