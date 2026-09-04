"""A second, separate deterministic gate -- NOT part of rules/engine.py's R1-R7 -- that
runs *before* an instruction derived from a researched strategy ever reaches the
compiler. See the plan's "one deliberate architecture deviation": R1-R7's signature and
two-phase repair logic are load-bearing for 22 eval cases and 63+ tests, and neither
strategy-lifecycle state nor portfolio drawdown is something a basket *repair* can fix --
both should stop the trader before an order is even compiled, not get folded into
rule-checking.

Both checks here BLOCK outright, no repair -- matching R1/R7's own precedent in the
existing repair principle for "external limit, not user intent."
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from orderguard.research.schemas import StrategyState

DEFAULT_MAX_DRAWDOWN_PCT = Decimal(5)
"""Refuse to proceed once session equity has fallen more than this many percent below
the session's starting equity. Tracked in-session only (st.session_state) -- not
persisted across restarts, since a hackathon demo is one session; a real limitation,
stated plainly rather than hidden."""


class PortfolioGuardVerdict(BaseModel):
    """The guard's decision -- always exactly one of these two shapes, never a partial
    or repaired result (there is nothing to repair here)."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reasons: tuple[str, ...] = ()


def check_portfolio_guard(
    strategy_state: StrategyState,
    session_starting_equity: Decimal,
    current_equity: Decimal,
    max_drawdown_pct: Decimal = DEFAULT_MAX_DRAWDOWN_PCT,
) -> PortfolioGuardVerdict:
    """Two independent checks, both must pass:

    1. Strategy state gate -- refuses if the strategy backing this instruction is KILLED.
    2. Daily drawdown circuit breaker -- refuses if current equity has fallen more than
       `max_drawdown_pct` below `session_starting_equity`.
    """
    reasons: list[str] = []

    if strategy_state == StrategyState.KILLED:
        reasons.append(
            "strategy is KILLED (failed adversarial robustness testing) -- cannot be used to place an order"
        )

    if session_starting_equity > 0:
        drawdown_pct = (session_starting_equity - current_equity) / session_starting_equity * 100
        if drawdown_pct > max_drawdown_pct:
            reasons.append(
                f"session drawdown {drawdown_pct:.2f}% exceeds the {max_drawdown_pct}% circuit breaker "
                f"(started at {session_starting_equity}, now {current_equity})"
            )

    return PortfolioGuardVerdict(allowed=len(reasons) == 0, reasons=tuple(reasons))
