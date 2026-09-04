"""CI Tier 2 (.github/workflows/live-check.yml): proves live credentials actually work,
without ever mutating account state.

Two checks, both read-only:
  1. AlpacaClient constructs (paper-trading guard included) and fetches the real
     account/positions/open orders.
  2. The real AgnesClient runs exactly ONE intent-compiler call against a fixture
     account+market -- one call, not the (LLM-heavier) research pipeline -- and the
     result is asserted to be a valid OrderPlan.

Never calls `submit_order`, `cancel_order`, or anything else that changes state. A
missing or invalid credential is a loud failure (non-zero exit, exception printed),
never a silent skip -- this script exists specifically to catch "the secret is missing
or wrong" before it's discovered by a human clicking around the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from decimal import Decimal

from eval.fixtures import load_fixture
from orderguard.broker.alpaca_client import AlpacaClient, PaperTradingGuardError
from orderguard.compiler.intent_compiler import IntentCompiler
from orderguard.config import get_settings
from orderguard.llm.client import AGNES_DEFAULT_TEMPERATURE, AgnesClient
from orderguard.schemas.order_plan import OrderPlan
from orderguard.schemas.user_constraints import UserConstraints

FIXTURE_NAME = "retail_rotation"
"""Matches ui/app.py's own DEFAULT_FIXTURE -- any fixture would do, this just needs a
real account+market shape for the compiler to compile against."""

INSTRUCTION = "Buy $500 of AAPL"
"""A trivial instruction -- this check only cares that the compiler round-trips a real
LLM response into a valid OrderPlan, not that any particular basket comes out."""


def check_alpaca() -> None:
    print("=== Tier 2 check 1/2: Alpaca paper account (read-only) ===")
    settings = get_settings()
    try:
        client = AlpacaClient(settings)
    except PaperTradingGuardError as e:
        print(f"FAILED: paper-trading guard refused to construct AlpacaClient: {e}", file=sys.stderr)
        raise

    account = client.get_account_state()
    print(f"account_id={account.account_id} account_type={account.account_type}")
    print(f"equity=${account.equity} cash=${account.cash} buying_power=${account.buying_power}")
    print(f"positions={len(account.positions)} open_orders={len(account.open_orders)}")
    for p in account.positions:
        print(f"  position: {p.symbol} qty={p.qty} @ ${p.current_price}")
    for oo in account.open_orders:
        print(f"  open order: {oo.side} {oo.symbol} ({oo.order_type})")

    if settings.alpaca_environment != "paper" or not settings.alpaca_paper:
        raise RuntimeError("account fetched but ALPACA_ENVIRONMENT/ALPACA_PAPER do not confirm paper trading")
    print("OK: confirmed paper account, no state changed.\n")


def check_agnes() -> None:
    print("=== Tier 2 check 2/2: Agnes LLM, one live intent-compiler call ===")
    settings = get_settings()
    llm_client = AgnesClient(
        base_url=settings.agnes_base_url,
        api_key=settings.agnes_api_key,
        model=settings.agnes_default_model,
        timeout=settings.agnes_timeout,
        max_retries=settings.agnes_max_retries,
        temperature=AGNES_DEFAULT_TEMPERATURE,
    )
    fixture = load_fixture(FIXTURE_NAME)
    compiler = IntentCompiler(llm_client)
    constraints = UserConstraints(max_position_pct=Decimal(15))

    plan = compiler.compile(INSTRUCTION, fixture.account, fixture.market, constraints)
    if not isinstance(plan, OrderPlan):
        raise TypeError(f"expected OrderPlan, got {type(plan)!r}")
    print(f"compiled {len(plan.orders)} order(s) from {INSTRUCTION!r}:")
    for o in plan.orders:
        print(f"  {o.side.value} {o.symbol} {'qty=' + str(o.qty) if o.qty is not None else '$' + str(o.notional)}")
    print("OK: compiler round-tripped a real Agnes response into a valid OrderPlan.\n")


def main() -> None:
    try:
        check_alpaca()
        check_agnes()
    except Exception as e:  # noqa: BLE001 -- any failure here should fail the workflow loudly, not skip
        print(f"\nLIVE READ-ONLY CHECK FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    print("All Tier 2 live read-only checks passed. No account state was changed.")


if __name__ == "__main__":
    main()
