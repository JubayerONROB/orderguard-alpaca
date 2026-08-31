"""Proposes a corrected basket in response to a failing RiskReport, then re-checks it once.

This is the second of the two places in OrderGuard allowed to call an LLM (the other is
`compiler/intent_compiler.py`). By design it gets exactly one repair attempt per basket --
if the repaired basket still fails a BLOCKING rule after that single re-run through
`RuleEngine`, the overall decision is BLOCK, not an iterative retry loop.
"""

from __future__ import annotations

from orderguard.llm.client import LLMClient
from orderguard.rules.engine import RuleEngine
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.risk_report import RiskReport


class RepairAgent:
    """Proposes a corrected OrderPlan and re-evaluates it through the rule engine exactly once."""

    def __init__(self, llm_client: LLMClient, rule_engine: RuleEngine) -> None:
        self._llm_client = llm_client
        self._rule_engine = rule_engine

    def repair(
        self,
        plan: OrderPlan,
        failing_report: RiskReport,
        state: AccountState,
    ) -> tuple[OrderPlan, RiskReport]:
        """Proposes a corrected basket for `plan` and returns it with its one re-check RiskReport.

        Raises:
            NotImplementedError: business logic not yet implemented.
        """
        raise NotImplementedError
