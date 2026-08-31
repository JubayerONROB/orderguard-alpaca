"""The fair baseline: one model call, no rule engine, no repair step.

Same model, same temperature, same account state, same market snapshot, same user
constraints as the compiler gets (via the identical `render_context` renderer). The
one deliberate difference is the task: instead of "compile literally, the engine will
check it," this prompt asks the model to produce a SAFE basket directly AND to flag
any account-rule problems itself, and it is told all seven rules by name and
definition. It returns a RiskReport-shaped JSON directly -- no deterministic engine
behind it. This is the comparison `run_eval.py --system agent` is scored against, so
weakening this prompt would make the whole report dishonest.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from orderguard.compiler.intent_compiler import render_context
from orderguard.llm.client import LLMClient
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import Order
from orderguard.schemas.risk_report import Decision, FiredRule
from orderguard.schemas.user_constraints import UserConstraints

SYSTEM_PROMPT = """You are a trading assistant with full responsibility for both
compiling a trader's instruction into an order basket AND checking that basket against
their account's risk rules, in one pass. There is no second system checking your work.

Given the trader's account state, current market data, their stated constraints, and
their instruction, you must:
1. Compile the instruction into a concrete, executable basket of orders (or an empty
   basket if no action is warranted), grounded only in symbols/prices/eligibility you
   were actually given.
2. Check that basket against the seven account rules below, exactly as a deterministic
   rule engine would, and either fix what you can fix or leave it blocked.
3. Report your final decision and every rule that fired along the way.

The seven rules, by id and definition:

R1 BUYING_POWER (severity: blocking) -- the basket's total buy notional, summed across
all orders and netted against any same-basket sell proceeds, must not exceed the
account's buying_power. A buy funded by a sale in the SAME basket must be evaluated
against POST-SALE funds (buying_power + that sale's proceeds), not pre-trade buying
power alone. No repair exists for a shortfall: if it fails, the whole basket is BLOCKed.

R2 PDT (severity: blocking) -- if equity is under $25,000, closing a position that was
opened the SAME calendar day as the account snapshot is a "day trade." If the existing
day-trade count plus new day trades from this basket would exceed 3 (i.e. reach a 4th),
this rule fails. Repair: defer (drop) just the day-trade-creating order(s) if other
orders remain in the basket; if dropping them would leave the basket empty, BLOCK
instead -- do not silently drop the trader's only instruction.

R3 CONCENTRATION (severity: blocking) -- for every symbol the basket buys, the WORST
CASE value (currently held position + any of that symbol's own pending open orders
that could still fill, unless this basket cancels them + this basket's buy) must not
exceed the user's max_position_pct of account equity. Repair: resize the buy down to
the largest WHOLE-SHARE quantity (rounding DOWN, never up) that keeps the position at
or under the cap -- always express the repaired order in shares (qty), never notional,
regardless of how the original order was sized.

R4 OPEN_ORDERS (severity: blocking) -- if any basket order shares a symbol with an
existing open (unfilled) order that this basket doesn't already cancel, that's a
stacking conflict, regardless of side. Repair: cancel the stale open order (add its
order_id to `cancellations`); this always succeeds.

R5 WASH_SALE (severity: warning) -- if a basket buy repurchases a symbol that was sold
at a realized loss within the last 30 days, flag it. This NEVER blocks and is NEVER
repaired (there's no resize that fixes purchase timing) -- it's surfaced to the trader
as a warning only.

R6 SESSION (severity: blocking) -- if the market is closed, any basket order that isn't
flagged extended-hours-eligible is invalid for the current session. Repair: defer (drop)
the invalid order(s) if other orders remain; if dropping them would empty the basket,
BLOCK instead.

R7 ASSET_ELIGIBILITY (severity: blocking) -- every order's symbol must be tradable. A
sell order whose quantity exceeds the currently held quantity is an implied short; if
the symbol isn't shortable, this rule fails. No repair exists for an eligibility
failure: if it fails, BLOCK.

Decision logic: if ANY rule ends up BLOCKED (failed and unrepairable), the overall
decision is "block" and the final basket must have NO orders and NO cancellations --
nothing reaches the broker. If nothing is BLOCKED but at least one rule was REPAIRED,
the decision is "allow_with_repairs" and the final basket is the repaired one. If
nothing was repaired or blocked (only WARNING-severity rules may still have fired), the
decision is "allow" and the final basket is your original compiled basket.

`rules_fired` must list every rule that failed at ANY point during your reasoning (on
your first compiled basket, even if you then fixed it), each as an object with:
- rule_id: the combined code, e.g. "R2_PDT", "R3_CONCENTRATION"
- severity: one of "info", "warning", "blocking" (matching the rule's fixed severity above)
- disposition: one of "repaired", "blocked", "warned" (never "accepted_by_user" -- that's
  reserved for a human override you cannot make)
- explanation: a human-readable string naming the SPECIFIC values involved (e.g. "NVDA
  would reach 33.9% of post-trade equity ($6,240 of $18,400), exceeding your 15% cap
  ($2,760)") -- a generic message like "cap exceeded" is not acceptable
- order_index: the index into your FINAL `orders` list this concerns, or null if it's
  not attributable to one order

A rule that never fired must not appear in `rules_fired` at all.
"""


class BaselineResponse(BaseModel):
    """The RiskReport-shaped JSON the baseline returns directly -- no engine behind it."""

    model_config = ConfigDict(frozen=True)

    orders: tuple[Order, ...] = ()
    cancellations: tuple[str, ...] = ()
    decision: Decision
    rules_fired: tuple[FiredRule, ...] = ()


def run_single_prompt_baseline(
    instruction: str,
    account_state: AccountState,
    market_snapshot: MarketSnapshot,
    user_constraints: UserConstraints,
    llm_client: LLMClient,
) -> BaselineResponse:
    """Runs the single-prompt baseline: one model call, asked to compile AND self-check.

    Unlike `IntentCompiler`, there is no retry-with-error-appended here and no
    deterministic re-check afterward -- whatever the model returns (after schema
    validation) is final, by design: this is what "no engine behind it" means.
    """
    user_prompt = render_context(instruction, account_state, market_snapshot, user_constraints)
    try:
        return llm_client.complete(SYSTEM_PROMPT, user_prompt, BaselineResponse)
    except (ValidationError, json.JSONDecodeError) as e:
        raise BaselineError(f"baseline failed to produce a valid response: {e}") from e


class BaselineError(Exception):
    """Raised when the baseline's single call fails schema validation. No retry, by
    design -- retrying would no longer be a fair "one model call" comparison."""
