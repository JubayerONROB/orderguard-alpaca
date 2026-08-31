"""Compiles a trader's plain-English instruction into a concrete OrderPlan.

This is one of the two places in OrderGuard allowed to call an LLM (the other is
`repair/repair_agent.py`). It resolves loose references in the instruction (e.g. "my
energy names", "split evenly") against the trader's actual `AccountState` and current
market data, and must ground every resulting order in real, current positions or
tradable symbols -- it does not invent tickers or prices.

The compiler produces the UNGUARDED plan. It does not check rules; `rules.engine`
does. See `compiler/prompts/compile_v2.md` for the system prompt (versioned there,
not inline here, so prompt changes are reviewable independent of code changes).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.llm.client import LLMClient
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.user_constraints import UserConstraints

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "compile_v2.md"


class CompilerError(Exception):
    """Raised when the compiler fails to produce a valid basket after its one retry."""


class CompiledBasket(BaseModel):
    """The shape the LLM actually produces: just the basket, not the full OrderPlan.

    `account_id`, `source_instruction`, and `plan_id` are already known to us -- asking
    the model to echo them back would only invite it to invent or mangle a value we
    already have.
    """

    model_config = ConfigDict(frozen=True)

    orders: tuple[Order, ...] = ()
    cancellations: tuple[str, ...] = ()


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _pct(value: Decimal, of: Decimal) -> str:
    if of == 0:
        return "n/a"
    return f"{(value / of * 100):.1f}%"


def render_context(
    instruction: str,
    account_state: AccountState,
    market_snapshot: MarketSnapshot,
    user_constraints: UserConstraints,
) -> str:
    """Renders account/market state as compact, human-readable text for the prompt.

    Not raw JSON: the model reasons better over prose-like structured text than over
    a dumped schema, and it's what a human reviewing the trajectory log would want to
    read too.
    """
    lines: list[str] = []

    lines.append(
        f"Account: {account_state.account_type} account, equity ${account_state.equity}, "
        f"cash ${account_state.cash}, buying power ${account_state.buying_power}"
    )
    if account_state.unsettled_cash > 0:
        lines.append(f"  (${account_state.unsettled_cash} of cash is unsettled)")
    lines.append(
        f"Day trades used: {account_state.daytrade_count} in the rolling 5-day window"
        f"{' (already flagged pattern day trader)' if account_state.pattern_day_trader else ''}"
    )

    lines.append("Positions:" if account_state.positions else "Positions: none")
    for p in account_state.positions:
        value = p.qty * p.current_price
        lines.append(
            f"  {p.symbol}: {p.qty} sh @ ${p.current_price} = ${value} "
            f"({_pct(value, account_state.equity)} of equity), opened {p.opened_at.date()}"
        )

    lines.append("Open orders:" if account_state.open_orders else "Open orders: none")
    for oo in account_state.open_orders:
        price_note = f" @ ${oo.limit_price}" if oo.limit_price else ""
        size_note = f"{oo.qty} sh" if oo.qty is not None else f"${oo.notional}"
        lines.append(
            f"  {oo.order_id}: {oo.order_type} {oo.side} {size_note} {oo.symbol}{price_note}, "
            f"submitted {oo.submitted_at.date()}, status {oo.status}"
        )

    lines.append(f"Market: {'OPEN' if market_snapshot.clock.is_open else 'CLOSED'} as of {market_snapshot.clock.timestamp}")
    for q in market_snapshot.quotes:
        lines.append(f"  {q.symbol}: last ${q.last_price}")
    for a in market_snapshot.assets:
        flags = []
        if not a.tradable:
            flags.append("NOT tradable")
        if not a.shortable:
            flags.append("NOT shortable")
        if not a.fractionable:
            flags.append("NOT fractionable")
        if flags:
            lines.append(f"  {a.symbol}: {', '.join(flags)}")

    lines.append(f"User constraint: max position size {user_constraints.max_position_pct}% of equity")
    lines.append(f'Instruction: "{instruction}"')

    return "\n".join(lines)


class IntentCompiler:
    """Turns (instruction, AccountState, MarketSnapshot, UserConstraints) into an OrderPlan."""

    def __init__(self, llm_client: LLMClient, trajectory: TrajectoryLogger | None = None) -> None:
        self._llm_client = llm_client
        self._trajectory = trajectory

    def _log(self, event_type: str, payload: dict) -> None:
        if self._trajectory is not None:
            self._trajectory.log_event(event_type, payload)

    def compile(
        self,
        instruction: str,
        account_state: AccountState,
        market_snapshot: MarketSnapshot,
        user_constraints: UserConstraints,
    ) -> OrderPlan:
        """Compiles one instruction into an OrderPlan grounded in the given account/market state.

        On a schema-validation failure, retries ONCE with the error text appended to
        the prompt. A second failure is a hard error (`CompilerError`), never a
        silent empty plan.
        """
        system_prompt = _load_system_prompt()
        user_prompt = render_context(instruction, account_state, market_snapshot, user_constraints)

        self._log("compiler_prompt", {"system": system_prompt, "user": user_prompt})

        try:
            basket = self._llm_client.complete(system_prompt, user_prompt, CompiledBasket)
        except (ValidationError, json.JSONDecodeError) as e:
            self._log("compiler_validation_error", {"error": str(e), "attempt": 1})
            retry_prompt = (
                f"{user_prompt}\n\n---\n"
                f"Your previous response was invalid: {e}\n"
                f"Respond again with ONLY the corrected JSON object."
            )
            self._log("compiler_retry_prompt", {"user": retry_prompt})
            try:
                basket = self._llm_client.complete(system_prompt, retry_prompt, CompiledBasket)
            except (ValidationError, json.JSONDecodeError) as e2:
                self._log("compiler_hard_failure", {"error": str(e2), "attempt": 2})
                raise CompilerError(
                    f"compiler failed to produce a valid basket after 1 retry: {e2}"
                ) from e2

        self._log("compiler_response", {"basket": basket.model_dump(mode="json")})

        return OrderPlan(
            account_id=account_state.account_id,
            source_instruction=instruction,
            orders=basket.orders,
            cancellations=basket.cancellations,
        )
