"""Trader-specified constraints the rule engine enforces on their behalf.

Distinct from the rules themselves: a rule like R3 (concentration) is deterministic
logic, but the CAP it enforces is a value the trader chose, not something OrderGuard
invents. `UserConstraints` is that set of values.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class UserConstraints(BaseModel):
    """Trader-specified limits, e.g. from the compiled instruction's "nothing over 15%"."""

    model_config = ConfigDict(frozen=True)

    max_position_pct: Decimal = Field(default=Decimal(100), gt=0, le=100)
    """Max weight (as a whole percentage, e.g. 15 means 15%) any single position may
    reach as a share of account equity. Defaults to 100 (no cap) when the trader
    didn't state one."""
