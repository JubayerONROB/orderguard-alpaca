"""Systems the eval harness can run: `null`, `rules_only`, `baseline`, `agent`.

Each implements `System.run(case, fixture) -> SystemResult`. `NullSystem` establishes
the scoring floor (no LLM, no rules). `RuleEngineSystem` isolates rule-engine accuracy
by running `case.naive_plan` straight through `RuleEngine`. `BaselineSystem` and
`AgentSystem` both call the (cassette-cached) LLM -- see `eval/baselines/single_prompt.py`
and `orderguard.compiler.intent_compiler` for what each actually asks the model to do.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from eval.baselines.single_prompt import run_single_prompt_baseline
from eval.cases import EvalCase
from eval.fixtures import Fixture
from eval.llm_setup import CountingLLMClient, build_llm_client
from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.compiler.intent_compiler import IntentCompiler
from orderguard.llm.cassette import CassetteMode
from orderguard.rules.engine import RuleEngine
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import Decision, RiskReport

TRAJECTORIES_DIR = Path(__file__).parent / "runs" / "trajectories"


@dataclass(frozen=True)
class SystemResult:
    """One system's output for one case, plus the metadata the scorer aggregates."""

    plan: OrderPlan
    report: RiskReport
    latency_s: float
    llm_calls: int


class System(Protocol):
    """A thing the eval harness can run one case through."""

    def run(self, case: EvalCase, fixture: Fixture) -> SystemResult: ...


class NullSystem:
    """Always returns ALLOW with an empty basket and no rule evaluations.

    Calls no LLM, evaluates no rules, makes no network call. This is the harness's
    floor: any real system should score far above what NullSystem achieves, since
    NullSystem is right only on cases whose expected decision is also ALLOW with
    zero orders.
    """

    def run(self, case: EvalCase, fixture: Fixture) -> SystemResult:
        start = time.perf_counter()
        plan = OrderPlan(
            account_id=fixture.account.account_id,
            source_instruction=case.instruction,
            orders=(),
        )
        report = RiskReport(plan_id=plan.plan_id, decision=Decision.ALLOW, rule_results=())
        latency_s = time.perf_counter() - start
        return SystemResult(plan=plan, report=report, latency_s=latency_s, llm_calls=0)


class RuleEngineSystem:
    """Runs `case.naive_plan` through `RuleEngine` directly, skipping the LLM compiler.

    Isolates rule-engine accuracy from compiler accuracy: `naive_plan` is what an
    unguarded single-pass system would have produced from the instruction, so this
    system answers "given that basket, does the deterministic rule engine reach the
    right decision?" independent of whether an LLM would compile the instruction
    correctly in the first place.
    """

    def __init__(self) -> None:
        self._engine = RuleEngine()

    def run(self, case: EvalCase, fixture: Fixture) -> SystemResult:
        start = time.perf_counter()
        naive = OrderPlan(
            account_id=fixture.account.account_id,
            source_instruction=case.instruction,
            orders=case.naive_plan,
        )
        final_plan, report = self._engine.evaluate(naive, fixture.account, fixture.market, case.user_constraints)
        latency_s = time.perf_counter() - start
        return SystemResult(plan=final_plan, report=report, latency_s=latency_s, llm_calls=0)


class BaselineSystem:
    """Single-prompt baseline: one model call, no rule engine -- see
    `eval/baselines/single_prompt.py` for why this is the fair comparison for `agent`.
    """

    def __init__(self, mode: CassetteMode | None = None) -> None:
        self._mode = mode

    def run(self, case: EvalCase, fixture: Fixture) -> SystemResult:
        start = time.perf_counter()
        llm_client = CountingLLMClient(build_llm_client(self._mode))
        response = run_single_prompt_baseline(
            case.instruction, fixture.account, fixture.market, case.user_constraints, llm_client
        )
        plan = OrderPlan(
            account_id=fixture.account.account_id,
            source_instruction=case.instruction,
            orders=response.orders,
            cancellations=response.cancellations,
        )
        report = RiskReport(
            plan_id=plan.plan_id,
            decision=response.decision,
            rule_results=(),
            rules_fired=response.rules_fired,
        )
        latency_s = time.perf_counter() - start
        return SystemResult(plan=plan, report=report, latency_s=latency_s, llm_calls=llm_client.call_count)


class AgentSystem:
    """Full OrderGuard pipeline: IntentCompiler -> RuleEngine.

    Mirrors `RuleEngineSystem` but compiles the basket from the instruction via a real
    (cassette-cached) LLM call instead of reading `case.naive_plan` -- this is the
    end-to-end system whose accuracy includes compiler accuracy, not just rule-engine
    accuracy.
    """

    def __init__(self, mode: CassetteMode | None = None) -> None:
        self._mode = mode
        self._engine = RuleEngine()

    def run(self, case: EvalCase, fixture: Fixture) -> SystemResult:
        start = time.perf_counter()
        llm_client = CountingLLMClient(build_llm_client(self._mode))
        trajectory = TrajectoryLogger(TRAJECTORIES_DIR / f"{case.id}.jsonl")
        compiler = IntentCompiler(llm_client, trajectory=trajectory)

        naive_plan = compiler.compile(case.instruction, fixture.account, fixture.market, case.user_constraints)
        final_plan, report = self._engine.evaluate(
            naive_plan, fixture.account, fixture.market, case.user_constraints
        )
        latency_s = time.perf_counter() - start
        return SystemResult(plan=final_plan, report=report, latency_s=latency_s, llm_calls=llm_client.call_count)
