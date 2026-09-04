"""CI Tier 3 (.github/workflows/paper-demo.yml, workflow_dispatch only): runs the
ACTUAL end-to-end research -> compile -> rule-engine -> approve -> submit flow
non-interactively against the real Alpaca paper account and real Agnes LLM.

This is the "everything real" run. Step 6 below prints, unmissably, that approval is
being scripted for this CI demonstration and is not a substitute for the UI's
human-in-the-loop step -- that's still the only approval path a person should trust.

Unlike scripts/live_readonly_check.py, this script DOES call `submit_order` -- but only
if the deterministic rule engine's decision is not BLOCK (step 7). A BLOCK decision means
nothing is submitted, same as the UI.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.data.historical import StockHistoricalDataClient

from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.broker.alpaca_client import AlpacaClient
from orderguard.compiler.intent_compiler import IntentCompiler
from orderguard.config import get_settings
from orderguard.llm.client import AGNES_DEFAULT_TEMPERATURE, AgnesClient
from orderguard.research.adversary import stress_test
from orderguard.research.backtest_engine import run_backtest
from orderguard.research.lifecycle import classify
from orderguard.research.market_intelligence import (
    DEFAULT_WATCHLIST,
    fetch_daily_bars,
    get_market_regimes,
)
from orderguard.research.performance_monitor import (
    DEFAULT_LOG_PATH,
    StrategyFill,
    log_fill,
)
from orderguard.research.strategy_discovery import StrategyDiscovery
from orderguard.rules.engine import RuleEngine
from orderguard.schemas.market_snapshot import AssetMeta, MarketClock, MarketSnapshot
from orderguard.schemas.risk_report import Decision, RiskReport
from orderguard.schemas.user_constraints import UserConstraints

RESEARCH_PROMPT = "Find a short-term strategy suited to the current regime."
MAX_POSITION_PCT = Decimal(15)
NOTIONAL_PCT_OF_EQUITY = Decimal(5)

TRAJECTORY_PATH = Path(__file__).resolve().parent.parent / "eval" / "runs" / "trajectories" / "paper_demo.jsonl"
REPORT_PATH = Path(__file__).resolve().parent.parent / "eval" / "runs" / "paper_demo_report.txt"


def _print_risk_report(report: RiskReport) -> None:
    print(f"\nDecision: {report.decision.value.upper()}")
    if report.rules_fired:
        print("Rule findings:")
        for f in report.rules_fired:
            print(f"  [{f.disposition.value}] {f.rule_id}: {f.explanation}")
    else:
        print("Rule findings: none -- every rule passed on the original basket.")


def main() -> None:
    lines: list[str] = []

    def out(msg: str = "") -> None:
        print(msg)
        lines.append(msg)

    try:
        _run(out)
    finally:
        # Written even on a mid-run crash -- a partial record of what got as far as it
        # did is more useful as a downloadable artifact than nothing at all.
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nFull report written to {REPORT_PATH}")


def _run(out) -> None:
    settings = get_settings()
    trajectory = TrajectoryLogger(TRAJECTORY_PATH)

    out("=== Step 1: real Alpaca paper account ===")
    broker = AlpacaClient(settings)
    account = broker.get_account_state()
    out(f"account_id={account.account_id} equity=${account.equity} cash=${account.cash}")

    out("\n=== Step 2: research pipeline (live) ===")
    data_client = StockHistoricalDataClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key)
    regimes = get_market_regimes(data_client, DEFAULT_WATCHLIST)
    if not regimes:
        out("No symbol in the default watchlist had enough history to compute a regime -- aborting.")
        sys.exit(1)
    for r in regimes:
        out(f"  regime: {r.symbol} {r.trend} {r.volatility_regime}vol {r.volume_state}volume")

    llm_client = AgnesClient(
        base_url=settings.agnes_base_url,
        api_key=settings.agnes_api_key,
        model=settings.agnes_default_model,
        timeout=settings.agnes_timeout,
        max_retries=settings.agnes_max_retries,
        temperature=AGNES_DEFAULT_TEMPERATURE,
    )
    discovery = StrategyDiscovery(llm_client, trajectory=trajectory)
    hypothesis = discovery.discover(RESEARCH_PROMPT, regimes)
    out(f'  hypothesis: "{hypothesis.name}" -- {hypothesis.rationale}')

    symbol = hypothesis.universe[0]
    bars = fetch_daily_bars(data_client, symbol)
    backtest_report = run_backtest(hypothesis, symbol, bars)
    out(
        f"  backtest OOS: return={backtest_report.oos_metrics.total_return_pct:.1f}% "
        f"sharpe={backtest_report.oos_metrics.sharpe:.2f} trades={backtest_report.oos_metrics.trade_count}"
    )

    robustness_report = stress_test(hypothesis, symbol, bars)
    state = classify(robustness_report)
    out(f"  adversary verdict: {robustness_report.verdict} (overall score {robustness_report.overall_score:.0f}/100)")
    out(f"  lifecycle state: {state.value.upper()}")

    out("\n=== Step 3: compile the resulting instruction (live) ===")
    suggested_notional = (account.equity * NOTIONAL_PCT_OF_EQUITY / 100).quantize(Decimal(1))
    instruction = (
        f'Buy approximately ${suggested_notional} of {symbol} based on the '
        f'"{hypothesis.name}" strategy ({hypothesis.entry.kind} entry, {hypothesis.exit.kind} exit).'
    )
    out(f"  instruction: {instruction}")

    # Market eligibility data comes from the live Alpaca feed the same way ui/app.py's
    # live mode does -- reuse of its helper directly isn't possible without importing
    # streamlit, so this mirrors the same asset-lookup logic against the trading client.
    known_symbols = {p.symbol for p in account.positions} | {oo.symbol for oo in account.open_orders} | {symbol}
    assets = []
    for sym in known_symbols:
        try:
            asset = broker._client.get_asset(sym)
        except Exception:  # noqa: BLE001, S112 -- an unlookupable symbol fails R7 closed, not the whole run
            continue
        assets.append(
            AssetMeta(
                symbol=sym,
                tradable=bool(asset.tradable),
                fractionable=bool(asset.fractionable),
                shortable=bool(asset.shortable),
                asset_class=str(asset.asset_class),
            )
        )
    clock = broker._client.get_clock()
    market = MarketSnapshot(
        as_of=account.as_of,
        quotes=(),
        assets=tuple(assets),
        clock=MarketClock(timestamp=clock.timestamp, is_open=clock.is_open, next_open=clock.next_open, next_close=clock.next_close),
    )

    constraints = UserConstraints(max_position_pct=MAX_POSITION_PCT)
    compiler = IntentCompiler(llm_client, trajectory=trajectory)
    naive_plan = compiler.compile(instruction, account, market, constraints)

    out("\n=== Step 4: deterministic rule engine (R1-R7) ===")
    final_plan, report = RuleEngine().evaluate(naive_plan, account, market, constraints)

    out("\n=== Step 5: full RiskReport ===")
    _print_risk_report(report)
    out("\nFinal basket:")
    for o in final_plan.orders:
        size = f"{o.qty} sh" if o.qty is not None else f"${o.notional}"
        out(f"  {o.side.value} {o.symbol} {size}")
    if final_plan.cancellations:
        out("Cancellations:")
        for order_id in final_plan.cancellations:
            out(f"  cancel {order_id}")

    out("\n" + "=" * 78)
    out("APPROVAL IS BEING SCRIPTED FOR THIS CI DEMONSTRATION.")
    out("THIS IS NOT A SUBSTITUTE FOR THE UI'S HUMAN-IN-THE-LOOP APPROVAL STEP.")
    out("=" * 78)

    trajectory.log_event("ci_scripted_approval", {"plan": final_plan.model_dump(mode="json"), "source": "paper-demo.yml"})

    if report.decision == Decision.BLOCK:
        out("\n=== Step 7: decision is BLOCK -- nothing submitted to Alpaca. ===")
    else:
        out("\n=== Step 7: submitting to Alpaca paper ===")
        submitted_ids: dict[str, str] = {}
        for order_id in final_plan.cancellations:
            broker.cancel_order(order_id)
            out(f"  cancelled {order_id}")
        for order in final_plan.orders:
            submitted_id = broker.submit_order(order)
            submitted_ids[order.symbol] = submitted_id
            out(f"  submitted {order.side.value} {order.symbol} -> order {submitted_id}")

        out("\n=== Step 8: logging fill(s) to performance monitor ===")
        for order in final_plan.orders:
            size = f"{order.qty} sh" if order.qty is not None else f"${order.notional}"
            log_fill(
                StrategyFill(
                    strategy_name=hypothesis.name,
                    symbol=order.symbol,
                    side=order.side.value,
                    size_text=size,
                    order_id=submitted_ids.get(order.symbol, "UNKNOWN"),
                    backtest_oos_sharpe=backtest_report.oos_metrics.sharpe,
                    backtest_oos_return_pct=backtest_report.oos_metrics.total_return_pct,
                    logged_at=datetime.now(timezone.utc),
                ),
                DEFAULT_LOG_PATH,
            )
        out(f"  logged to {DEFAULT_LOG_PATH}")


if __name__ == "__main__":
    main()
