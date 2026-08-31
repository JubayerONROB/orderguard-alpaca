"""Loads and validates `eval/cases/case_*.json` files.

A case pins one instruction + fixture pair to a ground-truth outcome so that malformed
cases fail loudly at load time, not silently at scoring time.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from orderguard.schemas.order_plan import Order
from orderguard.schemas.risk_report import Decision
from orderguard.schemas.user_constraints import UserConstraints

CASES_DIR = Path(__file__).parent


class CaseLoadError(Exception):
    """Raised when a case file is missing, not valid JSON, or fails schema validation."""


class ExpectedOrderAction(str, Enum):
    """Whether an expected basket entry places a new order or cancels an existing one."""

    PLACE = "place"
    CANCEL = "cancel"


class ExpectedCancellation(BaseModel):
    """One existing open order the expected basket cancels.

    Referenced by `order_id` against the case's fixture `AccountState.open_orders`, so
    the ground truth is unambiguous even if two open orders share a symbol.
    """

    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    """Redundant with `order_id` -> fixture lookup, but kept so the case JSON is
    self-documenting without cross-referencing the fixture."""

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class ExpectedOutcome(BaseModel):
    """Ground truth for one case: the decision, which rules fire, and the final basket."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    rules_fired: tuple[str, ...] = ()
    """Rule codes in `{id}_{name}` form, e.g. "R2_PDT", "R3_CONCENTRATION"."""
    orders: tuple[Order, ...] = ()
    """New orders the final (possibly repaired) basket places."""
    cancellations: tuple[ExpectedCancellation, ...] = ()
    """Existing open orders the final basket cancels. Kept separate from `orders`
    because a cancellation isn't sized/typed the way a new `Order` is."""


class EvalCase(BaseModel):
    """One eval case: an instruction, its fixture, user constraints, and expected outcome."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    fixture: str = Field(min_length=1)
    user_constraints: UserConstraints = UserConstraints()
    expected: ExpectedOutcome
    naive_plan: tuple[Order, ...] = ()
    """What an unguarded single-pass system (no risk checks) would produce from
    `instruction` alone -- the UNSAFE basket the rule engine must catch and either
    repair or block. Equal to `expected.orders` for a case where nothing fires."""
    notes: str = ""
    """Why this case exists and, for any case involving money, the arithmetic that
    justifies the expected outcome -- ground truth must be auditable, not asserted."""


def load_case(path: Path) -> EvalCase:
    """Loads and validates a single case file.

    Raises:
        CaseLoadError: the file is missing, not valid JSON, or fails schema validation.
    """
    if not path.is_file():
        raise CaseLoadError(f"case file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CaseLoadError(f"invalid JSON in case file {path}: {e}") from e
    try:
        return EvalCase.model_validate(payload)
    except ValidationError as e:
        raise CaseLoadError(f"invalid case in {path}: {e}") from e


def load_all_cases(cases_dir: Path = CASES_DIR) -> list[EvalCase]:
    """Loads every `case_*.json` file in `cases_dir`, sorted by filename.

    Raises:
        CaseLoadError: any case file fails to load (see `load_case`).
    """
    return [load_case(p) for p in sorted(cases_dir.glob("case_*.json"))]
