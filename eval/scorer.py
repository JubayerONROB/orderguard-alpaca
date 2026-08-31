"""Scores one system's output against a case's ground truth, and aggregates across cases.

Per case: `decision_match`, `rules_match` (exact set equality), `orders_match`
(field-by-field, order-independent), and `case_pass` (all three). Across cases:
`primary_score` (% cases passing), `catch_rate` (recall on cases with a non-empty
`expected.rules_fired`), `false_block_rate` (% of expected-ALLOW cases the system
blocked or repaired instead), plus latency and LLM-call totals.

`catch_rate` is keyed on `rules_match`, not on `decision != ALLOW`: a case can expect
ALLOW while still expecting a rule to fire (a WARNING-severity rule, e.g. wash sale,
never blocks). Keying catch_rate off decision would let a system that misses the
warning entirely still "catch" that case, since its decision matches trivially.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from eval.cases import EvalCase, ExpectedCancellation
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import Decision, RiskReport

MONEY_TOLERANCE = Decimal("0.01")
"""Max absolute difference allowed between two Decimal money/qty fields to still count
as equal. Guards against float-to-Decimal round-trip noise in fixtures, not against
genuine sizing errors."""


def _decimal_close(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= MONEY_TOLERANCE


def _order_key(order: Order) -> tuple[str, str]:
    return (order.symbol, order.side.value)


def _orders_equal(actual: Order, expected: Order) -> bool:
    return (
        actual.symbol == expected.symbol
        and actual.side == expected.side
        and actual.order_type == expected.order_type
        and actual.time_in_force == expected.time_in_force
        and _decimal_close(actual.qty, expected.qty)
        and _decimal_close(actual.notional, expected.notional)
        and _decimal_close(actual.limit_price, expected.limit_price)
    )


def compare_orders(
    actual_orders: tuple[Order, ...],
    expected_orders: tuple[Order, ...],
    actual_cancellations: frozenset[str],
    expected_cancellations: tuple[ExpectedCancellation, ...],
) -> bool:
    """True iff the actual basket's new orders and cancellations both match expected.

    New orders are compared order-independently: both lists are sorted by
    (symbol, side) and compared pairwise, field by field, with `MONEY_TOLERANCE` on
    Decimal fields. Cancellations are compared as a set of `order_id`.
    """
    if len(actual_orders) != len(expected_orders):
        return False
    actual_sorted = sorted(actual_orders, key=_order_key)
    expected_sorted = sorted(expected_orders, key=_order_key)
    if not all(_orders_equal(a, e) for a, e in zip(actual_sorted, expected_sorted)):
        return False

    expected_cancel_ids = frozenset(c.order_id for c in expected_cancellations)
    return actual_cancellations == expected_cancel_ids


@dataclass(frozen=True)
class CaseScore:
    """Scoring outcome for one case."""

    case_id: str
    decision_match: bool
    rules_match: bool
    orders_match: bool
    case_pass: bool
    expected_decision: Decision
    actual_decision: Decision
    expected_has_violation: bool
    """True iff `case.expected.rules_fired` is non-empty -- i.e. at least one rule was
    supposed to fire, whether or not it ultimately blocked the basket. Drives
    `catch_rate`'s denominator."""
    latency_s: float
    llm_calls: int


def score_case(
    case: EvalCase,
    actual_plan: OrderPlan,
    actual_report: RiskReport,
    latency_s: float,
    llm_calls: int,
) -> CaseScore:
    """Scores one system run against `case`'s expected outcome."""
    decision_match = actual_report.decision == case.expected.decision
    rules_match = frozenset(f.rule_id for f in actual_report.rules_fired) == frozenset(case.expected.rules_fired)
    orders_match = compare_orders(
        actual_plan.orders,
        case.expected.orders,
        frozenset(actual_plan.cancellations),
        case.expected.cancellations,
    )
    return CaseScore(
        case_id=case.id,
        decision_match=decision_match,
        rules_match=rules_match,
        orders_match=orders_match,
        case_pass=decision_match and rules_match and orders_match,
        expected_decision=case.expected.decision,
        actual_decision=actual_report.decision,
        expected_has_violation=len(case.expected.rules_fired) > 0,
        latency_s=latency_s,
        llm_calls=llm_calls,
    )


@dataclass(frozen=True)
class Summary:
    """Aggregate scoring across a full eval run."""

    total_cases: int
    primary_score: float
    """% of cases where `case_pass` is True, in [0, 100]."""
    catch_rate: float | None
    """% of cases with a non-empty `expected.rules_fired` where the system's
    `rules_fired` exactly matched (recall on "did the system detect what it was
    supposed to detect"), in [0, 100]. None if no such cases exist in the run."""
    false_block_rate: float | None
    """% of expected-ALLOW cases the system blocked or repaired instead, in [0, 100].
    None if no expected-ALLOW cases exist in the run."""
    mean_latency_s: float
    total_llm_calls: int


def aggregate(scores: list[CaseScore]) -> Summary:
    """Aggregates per-case scores into run-level metrics."""
    total_cases = len(scores)
    if total_cases == 0:
        return Summary(
            total_cases=0,
            primary_score=0.0,
            catch_rate=None,
            false_block_rate=None,
            mean_latency_s=0.0,
            total_llm_calls=0,
        )

    primary_score = 100.0 * sum(s.case_pass for s in scores) / total_cases

    has_violation = [s for s in scores if s.expected_has_violation]
    catch_rate = (
        100.0 * sum(s.rules_match for s in has_violation) / len(has_violation) if has_violation else None
    )

    expect_allow = [s for s in scores if s.expected_decision == Decision.ALLOW]
    false_block_rate = (
        100.0 * sum(s.actual_decision != Decision.ALLOW for s in expect_allow) / len(expect_allow)
        if expect_allow
        else None
    )

    mean_latency_s = sum(s.latency_s for s in scores) / total_cases
    total_llm_calls = sum(s.llm_calls for s in scores)

    return Summary(
        total_cases=total_cases,
        primary_score=primary_score,
        catch_rate=catch_rate,
        false_block_rate=false_block_rate,
        mean_latency_s=mean_latency_s,
        total_llm_calls=total_llm_calls,
    )


def format_report(scores: list[CaseScore], summary: Summary) -> str:
    """Renders a per-case table plus a summary block as plain text."""
    lines: list[str] = []
    header = f"{'case':<12} {'decision':<9} {'rules':<7} {'orders':<8} {'pass':<6} {'latency_s':<10}"
    lines.append(header)
    lines.append("-" * len(header))
    for s in scores:
        lines.append(
            f"{s.case_id:<12} {'OK' if s.decision_match else 'FAIL':<9} "
            f"{'OK' if s.rules_match else 'FAIL':<7} {'OK' if s.orders_match else 'FAIL':<8} "
            f"{'PASS' if s.case_pass else 'FAIL':<6} {s.latency_s:<10.4f}"
        )
    lines.append("")
    lines.append("summary:")
    lines.append(f"  total_cases       = {summary.total_cases}")
    lines.append(f"  primary_score     = {summary.primary_score:.1f}%")
    catch_str = f"{summary.catch_rate:.1f}%" if summary.catch_rate is not None else "n/a"
    lines.append(f"  catch_rate        = {catch_str}")
    fb_str = f"{summary.false_block_rate:.1f}%" if summary.false_block_rate is not None else "n/a"
    lines.append(f"  false_block_rate  = {fb_str}")
    lines.append(f"  mean_latency_s    = {summary.mean_latency_s:.4f}")
    lines.append(f"  total_llm_calls   = {summary.total_llm_calls}")
    return "\n".join(lines)
