"""Tests for eval/scorer.py arithmetic against hand-built cases."""

from __future__ import annotations

from decimal import Decimal

from eval.cases import EvalCase, ExpectedCancellation, ExpectedOutcome
from eval.scorer import aggregate, score_case
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import (
    Decision,
    Disposition,
    FiredRule,
    RiskReport,
    Severity,
)

CASE_ALLOW = EvalCase(
    id="case_hand_allow",
    title="hand-built allow case",
    instruction="buy $1,000 of AAPL",
    fixture="unused",
    expected=ExpectedOutcome(
        decision=Decision.ALLOW,
        rules_fired=(),
        orders=(Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.00")),),
    ),
)

CASE_BLOCK = EvalCase(
    id="case_hand_block",
    title="hand-built block case",
    instruction="sell my AAPL",
    fixture="unused",
    expected=ExpectedOutcome(decision=Decision.BLOCK, rules_fired=("R2_PDT",), orders=()),
)

CASE_ALLOW_WITH_WARNING = EvalCase(
    id="case_hand_warn",
    title="hand-built allow-with-warning case (mirrors case_012)",
    instruction="buy 100 shares of SOFI",
    fixture="unused",
    expected=ExpectedOutcome(
        decision=Decision.ALLOW,
        rules_fired=("R5_WASH_SALE",),
        orders=(Order(symbol="SOFI", side="buy", order_type="market", qty=Decimal(100)),),
    ),
)


def _plan(case: EvalCase, orders: tuple[Order, ...] = (), cancellations: tuple[str, ...] = ()) -> OrderPlan:
    return OrderPlan(
        account_id="acct_1",
        source_instruction=case.instruction,
        orders=orders,
        cancellations=cancellations,
    )


def test_score_case_pass_on_exact_match() -> None:
    plan = _plan(CASE_ALLOW, orders=CASE_ALLOW.expected.orders)
    report = RiskReport(plan_id=plan.plan_id, decision=Decision.ALLOW, rule_results=())
    score = score_case(CASE_ALLOW, plan, report, latency_s=0.01, llm_calls=1)
    assert score.decision_match
    assert score.rules_match
    assert score.orders_match
    assert score.case_pass


def test_score_case_fails_on_wrong_decision() -> None:
    plan = _plan(CASE_ALLOW, orders=CASE_ALLOW.expected.orders)
    report = RiskReport(plan_id=plan.plan_id, decision=Decision.BLOCK, rule_results=())
    score = score_case(CASE_ALLOW, plan, report, latency_s=0.01, llm_calls=1)
    assert not score.decision_match
    assert not score.case_pass


def test_score_case_fails_on_wrong_order_qty() -> None:
    wrong_order = Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("500.00"))
    plan = _plan(CASE_ALLOW, orders=(wrong_order,))
    report = RiskReport(plan_id=plan.plan_id, decision=Decision.ALLOW, rule_results=())
    score = score_case(CASE_ALLOW, plan, report, latency_s=0.01, llm_calls=1)
    assert score.decision_match
    assert not score.orders_match
    assert not score.case_pass


def test_score_case_orders_match_within_money_tolerance() -> None:
    close_order = Order(symbol="AAPL", side="buy", order_type="market", notional=Decimal("1000.005"))
    plan = _plan(CASE_ALLOW, orders=(close_order,))
    report = RiskReport(plan_id=plan.plan_id, decision=Decision.ALLOW, rule_results=())
    score = score_case(CASE_ALLOW, plan, report, latency_s=0.01, llm_calls=1)
    assert score.orders_match


def test_score_case_fails_on_missing_rule() -> None:
    plan = _plan(CASE_BLOCK)
    report = RiskReport(plan_id=plan.plan_id, decision=Decision.BLOCK, rule_results=())
    score = score_case(CASE_BLOCK, plan, report, latency_s=0.01, llm_calls=0)
    assert score.decision_match
    assert not score.rules_match
    assert not score.case_pass


def test_score_case_rules_match_on_exact_set() -> None:
    plan = _plan(CASE_BLOCK)
    report = RiskReport(
        plan_id=plan.plan_id,
        decision=Decision.BLOCK,
        rules_fired=(
            FiredRule(
                rule_id="R2_PDT",
                severity=Severity.BLOCKING,
                disposition=Disposition.BLOCKED,
                explanation="day trade #4 on sub-$25k equity account",
            ),
        ),
    )
    score = score_case(CASE_BLOCK, plan, report, latency_s=0.01, llm_calls=0)
    assert score.rules_match
    assert score.case_pass


def test_aggregate_on_one_pass_one_fail() -> None:
    plan_allow = _plan(CASE_ALLOW, orders=CASE_ALLOW.expected.orders)
    report_allow = RiskReport(plan_id=plan_allow.plan_id, decision=Decision.ALLOW, rule_results=())
    pass_score = score_case(CASE_ALLOW, plan_allow, report_allow, latency_s=0.02, llm_calls=2)

    plan_block = _plan(CASE_BLOCK)
    report_block_wrong = RiskReport(plan_id=plan_block.plan_id, decision=Decision.ALLOW, rule_results=())
    fail_score = score_case(CASE_BLOCK, plan_block, report_block_wrong, latency_s=0.04, llm_calls=1)

    summary = aggregate([pass_score, fail_score])
    assert summary.total_cases == 2
    assert summary.primary_score == 50.0
    # CASE_BLOCK is the only non-ALLOW case and it was missed -> catch_rate 0%.
    assert summary.catch_rate == 0.0
    # CASE_ALLOW is the only expected-ALLOW case and it was correctly allowed -> false_block_rate 0%.
    assert summary.false_block_rate == 0.0
    assert summary.mean_latency_s == 0.03
    assert summary.total_llm_calls == 3


def test_catch_rate_and_false_block_rate_definitions() -> None:
    """Demonstrates both the fix and the case it fixes: a system that misses the
    wash-sale warning entirely gets decision_match=True (ALLOW == ALLOW) but must NOT
    count as "caught" -- this is the exact loophole the old decision-based catch_rate
    definition let through."""
    # Case with a violation, correctly caught (rules_match True).
    plan_block = _plan(CASE_BLOCK)
    report_block_correct = RiskReport(
        plan_id=plan_block.plan_id,
        decision=Decision.BLOCK,
        rules_fired=(
            FiredRule(
                rule_id="R2_PDT",
                severity=Severity.BLOCKING,
                disposition=Disposition.BLOCKED,
                explanation="day trade #4 on sub-$25k equity account",
            ),
        ),
    )
    caught_score = score_case(CASE_BLOCK, plan_block, report_block_correct, latency_s=0.01, llm_calls=0)

    # Case with a violation (a warning), missed entirely -> not caught, even though
    # decision still happens to match (ALLOW == ALLOW).
    plan_warn = _plan(CASE_ALLOW_WITH_WARNING, orders=CASE_ALLOW_WITH_WARNING.expected.orders)
    report_missed_warning = RiskReport(plan_id=plan_warn.plan_id, decision=Decision.ALLOW, rules_fired=())
    missed_score = score_case(CASE_ALLOW_WITH_WARNING, plan_warn, report_missed_warning, latency_s=0.01, llm_calls=0)

    # Expected-ALLOW case (CASE_ALLOW), correctly allowed -> not a false block.
    plan_allow = _plan(CASE_ALLOW, orders=CASE_ALLOW.expected.orders)
    report_allow = RiskReport(plan_id=plan_allow.plan_id, decision=Decision.ALLOW, rules_fired=())
    allow_score = score_case(CASE_ALLOW, plan_allow, report_allow, latency_s=0.01, llm_calls=0)

    summary = aggregate([caught_score, missed_score, allow_score])

    # catch_rate denominator = cases with non-empty expected.rules_fired = {CASE_BLOCK, CASE_ALLOW_WITH_WARNING} = 2.
    # Of those, only CASE_BLOCK was correctly detected (rules_match True) -> 1/2 = 50%.
    assert summary.catch_rate == 50.0

    # false_block_rate denominator = expected-ALLOW cases = {CASE_ALLOW_WITH_WARNING, CASE_ALLOW} = 2.
    # Neither was blocked or repaired (both actual_decision == ALLOW) -> 0/2 = 0%.
    assert summary.false_block_rate == 0.0


def test_aggregate_empty_scores() -> None:
    summary = aggregate([])
    assert summary.total_cases == 0
    assert summary.primary_score == 0.0
    assert summary.catch_rate is None
    assert summary.false_block_rate is None


def test_expected_cancellation_field_names() -> None:
    cancel = ExpectedCancellation(order_id="ord_1", symbol="nvda")
    assert cancel.symbol == "NVDA"
