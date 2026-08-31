# OrderGuard

OrderGuard is a pre-trade intent compiler and risk gate for self-directed retail traders.

The user types a plain-English instruction (e.g. "close out my energy names and put it all
into NVDA and AMD, split evenly, nothing over 15% per position"). OrderGuard reads their live
Alpaca paper account, compiles the instruction into a concrete basket of orders, validates
that basket against a deterministic rule set, proposes repairs for what it can fix, blocks
what it cannot, and presents the whole thing for one human approval before any order is sent.

## Rule engine

Each rule is its own module under `src/orderguard/rules/`:

| ID | Module | Checks |
|----|--------|--------|
| R1 | `buying_power.py` | basket notional vs available buying power, summed across the basket |
| R2 | `pdt.py` | day trades this basket creates + existing count vs the $25k equity rule |
| R3 | `concentration.py` | resulting position weight vs user cap, against post-trade equity |
| R4 | `open_orders.py` | conflicts and stacking with existing unfilled orders on same symbol |
| R5 | `wash_sale.py` | repurchase within 30 days of a realized loss on the same security |
| R6 | `session.py` | market clock, extended-hours eligibility, order type validity |
| R7 | `asset_eligibility.py` | tradable, fractionable, shortable flags |

R1, R2, R3, and R4 are **basket-level**: they cannot be evaluated on one order in isolation.
That is the technical heart of the project — the engine must reason about the whole proposed
basket together, not order-by-order.

## The repair principle

Repair only when the correction is uniquely determined by a constraint the user themselves
stated, or by a pure timing shift that preserves the rest of the basket. Otherwise block.

    R3 CONCENTRATION  -> REPAIR. The user stated the cap; rounding down enforces their words.
    R4 OPEN_ORDERS    -> REPAIR. Cancel-and-resize is arithmetically unique.
    R2 PDT            -> REPAIR by deferral IF other orders in the basket still execute today.
                         If deferral would empty the basket, BLOCK. (case_003 vs case_002.)
    R6 SESSION        -> Identical logic to R2. (case_013 blocks: single order, empty basket.)
    R1 BUYING_POWER   -> BLOCK. An external limit, not user intent. No principled resize exists.
    R7 ELIGIBILITY    -> BLOCK. Nothing to repair.
    R5 WASH_SALE      -> WARN. A tax consequence the user may knowingly accept.

## Hard architectural constraints

- The rule engine (`src/orderguard/rules/`) is **100% deterministic Python with zero LLM
  calls**. Same input, same output, every run. Only the intent compiler
  (`src/orderguard/compiler/`) and the repair proposer (`src/orderguard/repair/`) call a
  model.
- **No order, cancellation, or resize reaches the broker without explicit human approval**
  through the UI approval step.
- The eval harness (`eval/`) runs entirely from frozen fixtures with **no network calls**.

## Contracts

The schemas in `src/orderguard/schemas/` are the contract that the rule engine, the eval
scorer, the API, and the UI all key off:

- `account_state.py` — `AccountState`, `Position`, `OpenOrder`, `Activity`
- `order_plan.py` — `Order`, `OrderPlan`
- `risk_report.py` — `Severity`, `Decision`, `RuleResult`, `Repair`, `RiskReport`

Money is always `Decimal`, never `float`. `RiskReport` must carry, per rule: rule id,
pass/fail, severity, the offending order index (if any), and a human-readable explanation
string naming the specific value that triggered it — that explanation is what the trader
reads before approving, so it is part of the contract, not a debug aid.

## Secrets

Real credentials live only in `.env` (gitignored). `.env.example` documents key names with
empty values and is the only version committed. `config.py` loads `.env` and fails loudly on
any missing key — it does not silently fall back.

## Framework provenance

`.claude/agents/`, `.claude/rules/`, `.claude/commands/`, `.claude/skills/` were copied from
an external toolkit (see `.claude/PROVENANCE.md`) and are not OrderGuard-original code.
