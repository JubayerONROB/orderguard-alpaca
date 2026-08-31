"""HTTP routes: compile an instruction, validate a basket, approve, and submit.

Each route is a thin wrapper over `orderguard.compiler`, `orderguard.rules.engine`,
`orderguard.repair`, and `orderguard.broker` -- no rule logic or LLM calls belong here.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.post("/compile")
def compile_instruction() -> None:
    """Compiles a plain-English instruction into an OrderPlan via IntentCompiler."""
    raise NotImplementedError


@router.post("/validate")
def validate_plan() -> None:
    """Runs an OrderPlan through the RuleEngine (and RepairAgent if needed) and returns a RiskReport."""
    raise NotImplementedError


@router.post("/approve")
def approve_plan() -> None:
    """Records the trader's explicit approval of a (possibly repaired) OrderPlan."""
    raise NotImplementedError


@router.post("/submit")
def submit_plan() -> None:
    """Submits an approved OrderPlan's orders to the broker. Requires prior /approve."""
    raise NotImplementedError
