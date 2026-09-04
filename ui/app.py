"""Streamlit approval screen: the one place a human clears a basket for submission.

Renders a compiled OrderPlan alongside its RiskReport (including any repairs), and is
the sole path through which an approved plan is allowed to reach `/submit`. Nothing
is ever submitted without an explicit "APPROVE PAPER TRADE" click -- that is the
product, not a detail.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

# `eval/` is a plain directory at the repo root, not part of the installed `orderguard`
# package -- Streamlit Cloud (and some local invocations) don't reliably put the repo
# root on sys.path just because ui/app.py lives one level under it, so `import eval...`
# below would fail unpredictably depending on how/where the app was launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
from alpaca.data.historical import StockHistoricalDataClient

from eval.cases import CASES_DIR, load_case
from eval.fixtures import FIXTURES_DIR, Fixture, load_fixture
from eval.llm_setup import build_llm_client
from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.broker.alpaca_client import AlpacaClient
from orderguard.compiler.intent_compiler import IntentCompiler
from orderguard.config import get_settings
from orderguard.llm.cassette import CassetteMode
from orderguard.research.adversary import stress_test
from orderguard.research.backtest_engine import InsufficientHistoryError, run_backtest
from orderguard.research.lifecycle import classify
from orderguard.research.market_intelligence import (
    DEFAULT_WATCHLIST,
    InsufficientBarsError,
    compute_regime,
    fetch_daily_bars,
)
from orderguard.research.performance_monitor import (
    DEFAULT_LOG_PATH,
    StrategyFill,
    attribute_performance,
    load_fills,
    log_fill,
)
from orderguard.research.portfolio_guard import check_portfolio_guard
from orderguard.research.schemas import (
    BacktestReport,
    MarketRegime,
    RobustnessReport,
    StrategyHypothesis,
    StrategyState,
)
from orderguard.research.strategy_discovery import (
    StrategyDiscovery,
    StrategyDiscoveryError,
)
from orderguard.rules._util import is_buy, is_sell, order_notional_value
from orderguard.rules.engine import RuleEngine
from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import AssetMeta, MarketClock, MarketSnapshot
from orderguard.schemas.order_plan import Order, OrderPlan
from orderguard.schemas.risk_report import Decision, Disposition, RiskReport, Severity
from orderguard.schemas.user_constraints import UserConstraints
from ui.rule_labels import ALL_RULE_CODES, rule_label

st.set_page_config(page_title="OrderGuard", layout="wide")

try:
    get_settings()
    CREDENTIALS_AVAILABLE = True
except Exception:  # noqa: BLE001 -- any missing/invalid config means "no credentials here",
    # not a page crash; this is what keeps a public deployment (no .env at all) fixture-only.
    CREDENTIALS_AVAILABLE = False

TRAJECTORY_PATH = Path(__file__).parent.parent / "eval" / "runs" / "trajectories" / "ui.jsonl"
DEFAULT_FIXTURE = "retail_rotation"

SEVERITY_COLOR = {
    Severity.BLOCKING: "#b3261e",
    Severity.WARNING: "#8a5a00",
    Severity.INFO: "#3b5bdb",
}
DISPOSITION_LABEL = {
    Disposition.REPAIRED: "Automatically fixed",
    Disposition.BLOCKED: "Blocked",
    Disposition.WARNED: "Flagged for you",
    Disposition.ACCEPTED_BY_USER: "You accepted this",
}
DECISION_LABEL = {
    Decision.ALLOW: "ALLOW",
    Decision.ALLOW_WITH_REPAIRS: "ALLOW WITH REPAIRS",
    Decision.BLOCK: "BLOCKED",
}


def _load_live_state() -> tuple[AccountState, MarketSnapshot]:
    """Live mode: real Alpaca account, market snapshot seeded with asset eligibility
    for symbols already known (held positions + open orders). A symbol the compiler
    newly introduces (not currently held or open) won't have eligibility data yet --
    see `_augment_market_with_live_assets`, called after compiling, to fill that in
    for exactly the symbols the compiled basket actually touches."""
    client = AlpacaClient(get_settings())
    account = client.get_account_state()
    clock = client._client.get_clock()
    known_symbols = {p.symbol for p in account.positions} | {oo.symbol for oo in account.open_orders}
    market = MarketSnapshot(
        as_of=account.as_of,
        quotes=(),
        assets=_fetch_asset_meta(client, known_symbols),
        clock=MarketClock(
            timestamp=clock.timestamp, is_open=clock.is_open, next_open=clock.next_open, next_close=clock.next_close
        ),
    )
    return account, market


def _fetch_asset_meta(client: AlpacaClient, symbols: set[str]) -> tuple[AssetMeta, ...]:
    """Fetches real Alpaca eligibility flags for `symbols`. A symbol that fails to
    look up is simply omitted -- R7 then fails closed for it (no data = not eligible),
    which is the safe default, not a silent pass."""
    assets = []
    for symbol in symbols:
        try:
            asset = client._client.get_asset(symbol)
        except Exception:  # noqa: BLE001, S112 -- an unlookupable symbol should fail R7 closed, not crash the page
            continue
        assets.append(
            AssetMeta(
                symbol=symbol,
                tradable=bool(asset.tradable),
                fractionable=bool(asset.fractionable),
                shortable=bool(asset.shortable),
                asset_class=str(asset.asset_class),
            )
        )
    return tuple(assets)


def _augment_market_with_live_assets(market: MarketSnapshot, symbols: set[str]) -> MarketSnapshot:
    """Extends `market.assets` with real eligibility data for any of `symbols` not
    already covered -- called after compiling, once the actual traded symbols are
    known, so a newly-introduced symbol (not currently held or open) still gets real
    R7 data instead of being treated as ineligible for lack of a lookup."""
    known = {a.symbol for a in market.assets}
    missing = symbols - known
    if not missing:
        return market
    client = AlpacaClient(get_settings())
    return market.model_copy(update={"assets": market.assets + _fetch_asset_meta(client, missing)})


def _get_data_client() -> StockHistoricalDataClient:
    settings = get_settings()
    return StockHistoricalDataClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key)


def _synthetic_bars(symbol: str, bar_count: int = 260) -> list[SimpleNamespace]:
    """Deterministic (seeded on the symbol) pseudo-random walk -- lets fixture mode
    demo the research pipeline stage-by-stage with no live market data connection and
    no live API calls. Clearly not real history; the UI labels it as such."""
    rng = random.Random(symbol)
    price = Decimal(100)
    now = datetime.now(timezone.utc)
    bars = []
    for i in range(bar_count):
        drift = Decimal(str(rng.uniform(-0.015, 0.018)))
        price = max(Decimal(1), price * (1 + drift))
        high = price * (1 + Decimal(str(rng.uniform(0, 0.01))))
        low = price * (1 - Decimal(str(rng.uniform(0, 0.01))))
        bars.append(
            SimpleNamespace(
                open=price,
                high=high,
                low=low,
                close=price,
                volume=int(rng.uniform(500_000, 2_000_000)),
                timestamp=now - timedelta(days=bar_count - i),
            )
        )
    return bars


def _get_regimes(fixture_mode: bool, symbols: list[str]) -> tuple[MarketRegime, ...]:
    regimes = []
    for symbol in symbols:
        bars = _synthetic_bars(symbol) if fixture_mode else fetch_daily_bars(_get_data_client(), symbol)
        try:
            regimes.append(compute_regime(symbol, bars))
        except InsufficientBarsError:
            continue
    return tuple(regimes)


def _get_backtest_bars(fixture_mode: bool, symbol: str) -> list[SimpleNamespace]:
    if fixture_mode:
        return _synthetic_bars(symbol)
    return fetch_daily_bars(_get_data_client(), symbol)


def _render_regimes(regimes: tuple[MarketRegime, ...]) -> None:
    st.caption("Market regimes")
    rows = [
        {
            "Symbol": r.symbol,
            "Trend": r.trend,
            "Volatility": r.volatility_regime,
            "Volume": r.volume_state,
            "Price": f"${r.current_price}",
            "20d SMA": f"${r.sma_20}",
            "Realized vol (ann.)": f"{r.realized_vol_20d * 100:.1f}%",
        }
        for r in regimes
    ]
    st.table(rows)


def _render_hypothesis(hypothesis: StrategyHypothesis) -> None:
    st.caption(f'Hypothesis: "{hypothesis.name}"')
    st.write(hypothesis.rationale)
    st.write(f"Universe: {', '.join(hypothesis.universe)}")
    st.write(f"Entry: {hypothesis.entry.model_dump_json(exclude_none=True)}")
    st.write(f"Exit: {hypothesis.exit.model_dump_json(exclude_none=True)}")


def _render_backtest_report(report: BacktestReport) -> None:
    st.caption("Backtest report (chronological 70/30 train / out-of-sample split)")
    rows = [
        {
            "Window": label,
            "Return": f"{m.total_return_pct:.1f}%",
            "Sharpe": f"{m.sharpe:.2f}",
            "Win rate": f"{m.win_rate_pct:.1f}%",
            "Max drawdown": f"{m.max_drawdown_pct:.1f}%",
            "Profit factor": (f"{m.profit_factor:.2f}" if m.profit_factor is not None else "n/a"),
            "Trades": m.trade_count,
        }
        for label, m in (("Train", report.train_metrics), ("Out-of-sample", report.oos_metrics))
    ]
    st.table(rows)


def _render_robustness_report(report: RobustnessReport, state: StrategyState) -> None:
    st.caption("Adversarial robustness (perturbed-parameter grid)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Sensitivity score", f"{report.parameter_sensitivity_score:.0f}/100")
    c2.metric("OOS stability score", f"{report.oos_stability_score:.0f}/100")
    c3.metric("Overall score", f"{report.overall_score:.0f}/100")
    c4.metric("Lifecycle state", state.value.upper())
    if report.verdict == "PASS":
        st.success(f"Adversary verdict: {report.verdict} ({report.grid_points_tested} grid points tested)")
    else:
        st.error(f"Adversary verdict: {report.verdict} ({report.grid_points_tested} grid points tested)")


def _fixture_names() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir())


def _default_instruction() -> str:
    try:
        return load_case(CASES_DIR / "case_003.json").instruction
    except Exception:  # noqa: BLE001 -- defensive fallback if case_003.json ever moves
        return "Close out my energy names and put it all into NVDA and AMD, split evenly, nothing over 15% per position."


def _order_size_text(order: Order) -> str:
    if order.qty is not None:
        return f"{order.qty} sh"
    return f"${order.notional}"


def _pct(value: Decimal, of: Decimal) -> str:
    if of == 0:
        return "n/a"
    return f"{(value / of * 100):.1f}%"


SUCCESSFUL_RUN_URL = "https://github.com/JubayerONROB/orderguard-alpaca/actions/runs/33855220353"
BLOCKED_RUN_URL = "https://github.com/JubayerONROB/orderguard-alpaca/actions/runs/33852216976"


def _render_live_proof() -> None:
    """Two real, live `paper-demo.yml` (Tier 3) runs against the real paper account and
    real Agnes LLM -- not a demo dressed up to look real. Full console log, uploaded
    report, and trajectory JSONL for both live under `docs/tier3-evidence/`; this just
    surfaces the headline facts where anyone opening the app sees them immediately."""
    with st.expander("Proof: two real Tier 3 runs (research pipeline -> real Alpaca paper order)", expanded=True):
        st.caption(
            "Same pipeline, same real account, same day. The only difference was whether the "
            "compiled order was eligible to execute in the current session -- the deterministic "
            "layer decides that, not the model that proposed the trade."
        )
        col1, col2 = st.columns(2)
        with col1:
            st.success("ALLOW -- order submitted and filled")
            st.markdown(
                '**"MSFT Bullish Momentum Pullback"** -- momentum entry, fixed-hold exit  \n'
                "Backtest OOS: +16.3% return, Sharpe 1.74 -- Adversary: PASS (60/100) -- Lifecycle: WATCH  \n"
                "Compiled: extended-hours limit order, 9 sh MSFT @ $512.70  \n"
                "Submitted -> order `ae7ab3ce-6700-44d6-a5f1-e05e1792f355`  \n"
                "**Filled** at $508.2684 avg (confirmed against the live account afterward)"
            )
            st.markdown(f"[View the full run log]({SUCCESSFUL_RUN_URL})")
        with col2:
            st.error("BLOCK -- nothing submitted")
            st.markdown(
                '**"MSFT Bullish Momentum Capture"** -- momentum entry, ATR stop  \n'
                "Backtest OOS: +24.5% return, Sharpe 2.32 -- Adversary: PASS (71/100) -- Lifecycle: WATCH  \n"
                "Compiled: plain market order (no extended-hours request), $5,001 of MSFT  \n"
                "**R6_SESSION blocked it**: market closed, order not extended-hours eligible  \n"
                "No order was submitted. No order exists from this run."
            )
            st.markdown(f"[View the full run log]({BLOCKED_RUN_URL})")


def _render_account_panel(account: AccountState) -> None:
    st.subheader("Account")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"${account.equity:,.2f}")
    c2.metric("Cash", f"${account.cash:,.2f}")
    c3.metric("Buying power", f"${account.buying_power:,.2f}")
    c4.metric("Day trades used", f"{account.daytrade_count} / 3")
    c5.metric("Account type", account.account_type.capitalize())

    if account.positions:
        st.caption("Positions")
        rows = [
            {
                "Symbol": p.symbol,
                "Qty": str(p.qty),
                "Price": f"${p.current_price}",
                "Value": f"${p.qty * p.current_price:,.2f}",
                "% of equity": _pct(p.qty * p.current_price, account.equity),
                "Opened": p.opened_at.date().isoformat(),
            }
            for p in account.positions
        ]
        st.table(rows)
    else:
        st.caption("Positions: none")

    if account.open_orders:
        st.caption("Open orders")
        rows = [
            {
                "Symbol": oo.symbol,
                "Side": oo.side,
                "Type": oo.order_type,
                "Size": (f"{oo.qty} sh" if oo.qty is not None else f"${oo.notional}"),
                "Limit": f"${oo.limit_price}" if oo.limit_price else "-",
                "Submitted": oo.submitted_at.date().isoformat(),
                "Order ID": oo.order_id,
            }
            for oo in account.open_orders
        ]
        st.table(rows)
    else:
        st.caption("Open orders: none")


def _render_decision(decision: Decision) -> None:
    label = DECISION_LABEL[decision]
    if decision == Decision.ALLOW:
        st.success(label)
    elif decision == Decision.ALLOW_WITH_REPAIRS:
        st.warning(label)
    else:
        st.error(label)


def _render_rule_findings(report: RiskReport) -> None:
    fired_ids = {f.rule_id for f in report.rules_fired}

    if report.rules_fired:
        st.caption("Rule findings")
        rows_html = ["<table style='width:100%; border-collapse:collapse;'>"]
        rows_html.append(
            "<tr style='text-align:left; border-bottom:1px solid #ddd;'>"
            "<th style='padding:6px 8px;'>Check</th><th style='padding:6px 8px;'>What happened</th>"
            "<th style='padding:6px 8px;'>Details</th></tr>"
        )
        for f in report.rules_fired:
            color = SEVERITY_COLOR.get(f.severity, "#666")
            rows_html.append(
                "<tr style='border-bottom:1px solid #eee;'>"
                f"<td style='padding:6px 8px; white-space:nowrap;'>"
                f"<span style='display:inline-block;width:8px;height:8px;border-radius:50%;"
                f"background:{color};margin-right:6px;'></span>{rule_label(f.rule_id)}</td>"
                f"<td style='padding:6px 8px; white-space:nowrap;'>{DISPOSITION_LABEL.get(f.disposition, f.disposition.value)}</td>"
                f"<td style='padding:6px 8px;'>{f.explanation}</td>"
                "</tr>"
            )
        rows_html.append("</table>")
        st.markdown("".join(rows_html), unsafe_allow_html=True)

    passed = [c for c in ALL_RULE_CODES if c not in fired_ids]
    with st.expander(f"{len(passed)} check{'s' if len(passed) != 1 else ''} passed"):
        for code in passed:
            st.write(rule_label(code))


def _render_basket(naive_plan: OrderPlan, final_plan: OrderPlan, account: AccountState) -> None:
    st.caption("Final basket")
    naive_by_key: dict[tuple[str, str], Order] = {(o.symbol, o.side.value): o for o in naive_plan.orders}

    if not final_plan.orders:
        st.write("No orders.")
    else:
        rows = []
        for order in final_plan.orders:
            naive = naive_by_key.get((order.symbol, order.side.value))
            change = ""
            if naive is not None and _order_size_text(naive) != _order_size_text(order):
                change = f"resized from {_order_size_text(naive)} to {_order_size_text(order)}"
            elif naive is None:
                change = "added by repair"
            rows.append(
                {
                    "Symbol": order.symbol,
                    "Side": order.side.value,
                    "Type": order.order_type.value,
                    "Size": _order_size_text(order),
                    "Limit": f"${order.limit_price}" if order.limit_price else "-",
                    "Change": change,
                }
            )
        st.table(rows)

    dropped = [
        o for (sym, side), o in naive_by_key.items() if not any(f.symbol == sym and f.side.value == side for f in final_plan.orders)
    ]
    if dropped:
        st.caption("Deferred (not included in this basket):")
        for o in dropped:
            st.write(f"{o.side.value} {o.symbol}, {_order_size_text(o)} -- not executed today")

    if final_plan.cancellations:
        st.caption("Proposed cancellations")
        open_by_id = {oo.order_id: oo for oo in account.open_orders}
        for order_id in final_plan.cancellations:
            oo = open_by_id.get(order_id)
            if oo is not None:
                st.write(f"Cancel {oo.order_type} {oo.side} {oo.symbol} ({order_id})")
            else:
                st.write(f"Cancel order {order_id}")


def _render_undeployed_cash(naive_plan: OrderPlan, final_plan: OrderPlan, market: MarketSnapshot, report: RiskReport) -> None:
    concentration_capped = any(f.rule_id == "R3_CONCENTRATION" for f in report.rules_fired)
    if not concentration_capped:
        return
    try:
        final_sells = sum((order_notional_value(o, market) for o in final_plan.orders if is_sell(o)), start=Decimal(0))
        final_buys = sum((order_notional_value(o, market) for o in final_plan.orders if is_buy(o)), start=Decimal(0))
        naive_sells = sum((order_notional_value(o, market) for o in naive_plan.orders if is_sell(o)), start=Decimal(0))
        naive_buys = sum((order_notional_value(o, market) for o in naive_plan.orders if is_buy(o)), start=Decimal(0))
    except Exception:  # noqa: BLE001 -- best-effort display, never worth crashing the page over
        return
    undeployed = (final_sells - final_buys) - (naive_sells - naive_buys)
    if undeployed > 0:
        st.caption("Cash left undeployed")
        st.write(f"~${undeployed:,.2f} will remain in cash -- your position size cap didn't allow the full proceeds to be redeployed.")


def main() -> None:
    st.title("OrderGuard")
    st.caption("Review a plain-English instruction before anything reaches your broker.")

    _render_live_proof()

    with st.sidebar:
        st.header("Mode")
        mode_options = ["Demo / Fixture", "Alpaca Paper"]
        if CREDENTIALS_AVAILABLE:
            mode_choice = st.radio("Data source", mode_options, index=0)
            fixture_mode = mode_choice == "Demo / Fixture"
        else:
            st.radio("Data source", mode_options, index=0, disabled=True)
            fixture_mode = True
            st.caption(
                "This deployment runs from frozen fixtures with cassette replay only -- "
                "it has no broker credentials, so live mode is unavailable here."
            )

        if fixture_mode:
            st.caption("○ FIXTURE / DEMO MODE")
        else:
            st.caption("● ALPACA PAPER CONNECTED")

        fixture_name = None
        if fixture_mode:
            names = _fixture_names()
            default_idx = names.index(DEFAULT_FIXTURE) if DEFAULT_FIXTURE in names else 0
            fixture_name = st.selectbox("Fixture", names, index=default_idx)

        max_position_pct = st.number_input("Max position size (% of equity)", min_value=1, max_value=100, value=15)

    if fixture_mode:
        fixture: Fixture = load_fixture(fixture_name)
        account, market = fixture.account, fixture.market
    else:
        account, market = _load_live_state()

    constraints = UserConstraints(max_position_pct=Decimal(max_position_pct))

    st.session_state.setdefault("session_starting_equity", account.equity)

    _render_account_panel(account)

    st.divider()
    st.subheader("Research a strategy")
    st.caption(
        "AI proposes a hypothesis, grounded in real regime data; a deterministic backtest and "
        "adversary validate it before you ever see an instruction to approve."
    )
    if fixture_mode:
        st.caption(
            "Fixture mode uses a deterministic synthetic price series (not real market data) so "
            "the pipeline can be demoed with no live connection or API calls."
        )
    research_symbols = st.multiselect("Watchlist", list(DEFAULT_WATCHLIST), default=list(DEFAULT_WATCHLIST), key="research_symbols")
    research_prompt = st.text_input(
        "Research prompt",
        value="Find a short-term strategy suited to the current regime.",
        key="research_prompt",
    )

    if st.button("Run research pipeline"):
        st.session_state.research_ran = False
        if not research_symbols:
            st.error("Pick at least one symbol for the watchlist.")
        else:
            with st.spinner("Computing regimes, proposing a hypothesis, backtesting, and stress-testing..."):
                try:
                    regimes = _get_regimes(fixture_mode, research_symbols)
                    if not regimes:
                        st.error("No symbol in the watchlist had enough history to compute a regime.")
                    else:
                        llm_mode = CassetteMode.REPLAY if fixture_mode else CassetteMode.AUTO
                        llm_client = build_llm_client(llm_mode)
                        trajectory = TrajectoryLogger(TRAJECTORY_PATH)
                        discovery = StrategyDiscovery(llm_client, trajectory=trajectory)
                        hypothesis = discovery.discover(research_prompt, regimes)
                        symbol = hypothesis.universe[0]
                        bars = _get_backtest_bars(fixture_mode, symbol)
                        backtest_report = run_backtest(hypothesis, symbol, bars)
                        robustness_report = stress_test(hypothesis, symbol, bars)
                        state = classify(robustness_report)

                        st.session_state.research_ran = True
                        st.session_state.research_regimes = regimes
                        st.session_state.research_hypothesis = hypothesis
                        st.session_state.research_backtest = backtest_report
                        st.session_state.research_robustness = robustness_report
                        st.session_state.research_state = state
                except StrategyDiscoveryError as e:
                    st.error(f"Strategy discovery failed: {e}")
                except InsufficientHistoryError as e:
                    st.error(f"Not enough historical bars to backtest: {e}")
                except Exception as e:  # noqa: BLE001 -- research pipeline failures shouldn't crash the page
                    st.error(f"Research pipeline failed: {e}")

    if st.session_state.get("research_ran"):
        _render_regimes(st.session_state.research_regimes)
        hypothesis: StrategyHypothesis = st.session_state.research_hypothesis
        backtest_report: BacktestReport = st.session_state.research_backtest
        robustness_report: RobustnessReport = st.session_state.research_robustness
        state: StrategyState = st.session_state.research_state

        _render_hypothesis(hypothesis)
        _render_backtest_report(backtest_report)
        _render_robustness_report(robustness_report, state)

        guard_verdict = check_portfolio_guard(
            strategy_state=state,
            session_starting_equity=st.session_state.session_starting_equity,
            current_equity=account.equity,
        )
        if not guard_verdict.allowed:
            st.warning("Portfolio guard would block using this idea: " + "; ".join(guard_verdict.reasons))

        if st.button("Use this idea", disabled=not guard_verdict.allowed):
            suggested_notional = (account.equity * Decimal(5) / 100).quantize(Decimal(1))
            generated_instruction = (
                f'Buy approximately ${suggested_notional} of {hypothesis.universe[0]} based on the '
                f'"{hypothesis.name}" strategy ({hypothesis.entry.kind} entry, {hypothesis.exit.kind} exit).'
            )
            st.session_state.instruction_text = generated_instruction
            st.session_state.active_strategy = {
                "name": hypothesis.name,
                "oos_sharpe": backtest_report.oos_metrics.sharpe,
                "oos_return_pct": backtest_report.oos_metrics.total_return_pct,
            }
            st.rerun()

    st.divider()
    st.session_state.setdefault("instruction_text", _default_instruction())
    instruction = st.text_input("Instruction", key="instruction_text")

    if st.button("Review this", type="primary"):
        llm_mode = CassetteMode.REPLAY if fixture_mode else CassetteMode.AUTO
        llm_client = build_llm_client(llm_mode)
        trajectory = TrajectoryLogger(TRAJECTORY_PATH)
        compiler = IntentCompiler(llm_client, trajectory=trajectory)
        try:
            naive_plan = compiler.compile(instruction, account, market, constraints)
        except Exception as e:  # noqa: BLE001 -- any compiler failure should show a friendly message, not crash the page
            st.error(f"Could not compile this instruction: {e}")
            st.session_state.reviewed = False
            return
        if not fixture_mode:
            market = _augment_market_with_live_assets(market, {o.symbol for o in naive_plan.orders})
        final_plan, report = RuleEngine().evaluate(naive_plan, account, market, constraints)

        st.session_state.reviewed = True
        st.session_state.submitted = False
        st.session_state.discarded = False
        st.session_state.account = account
        st.session_state.market = market
        st.session_state.naive_plan = naive_plan
        st.session_state.final_plan = final_plan
        st.session_state.report = report
        st.session_state.fixture_mode = fixture_mode

    if st.session_state.get("reviewed") and not st.session_state.get("submitted") and not st.session_state.get("discarded"):
        report: RiskReport = st.session_state.report
        naive_plan: OrderPlan = st.session_state.naive_plan
        final_plan: OrderPlan = st.session_state.final_plan
        account = st.session_state.account
        market = st.session_state.market

        st.divider()
        _render_decision(report.decision)
        _render_rule_findings(report)
        _render_basket(naive_plan, final_plan, account)
        _render_undeployed_cash(naive_plan, final_plan, market, report)

        st.divider()
        if report.decision == Decision.BLOCK:
            if st.button("Dismiss"):
                st.session_state.reviewed = False
        else:
            col1, col2 = st.columns(2)
            with col1:
                if st.button("APPROVE PAPER TRADE", type="primary"):
                    trajectory = TrajectoryLogger(TRAJECTORY_PATH)
                    trajectory.log_event("human_approval", {"plan": final_plan.model_dump(mode="json")})
                    if st.session_state.fixture_mode:
                        submitted_ids = {o.symbol: "SIMULATED" for o in final_plan.orders}
                        st.session_state.submission_results = [
                            "SIMULATED -- fixture mode, no broker connected. No real order was placed."
                        ]
                    else:
                        client = AlpacaClient(get_settings())
                        results = []
                        submitted_ids = {}
                        for order_id in final_plan.cancellations:
                            client.cancel_order(order_id)
                            results.append(f"Cancelled {order_id}")
                        for order in final_plan.orders:
                            submitted_id = client.submit_order(order)
                            submitted_ids[order.symbol] = submitted_id
                            results.append(f"Submitted {order.side.value} {order.symbol} -> order {submitted_id}")
                        st.session_state.submission_results = results

                    active_strategy = st.session_state.get("active_strategy")
                    if active_strategy is not None:
                        for order in final_plan.orders:
                            log_fill(
                                StrategyFill(
                                    strategy_name=active_strategy["name"],
                                    symbol=order.symbol,
                                    side=order.side.value,
                                    size_text=_order_size_text(order),
                                    order_id=str(submitted_ids.get(order.symbol, "SIMULATED")),
                                    backtest_oos_sharpe=active_strategy["oos_sharpe"],
                                    backtest_oos_return_pct=active_strategy["oos_return_pct"],
                                    logged_at=datetime.now(timezone.utc),
                                )
                            )
                        st.session_state.active_strategy = None

                    st.session_state.submitted = True
            with col2:
                if st.button("Discard"):
                    trajectory = TrajectoryLogger(TRAJECTORY_PATH)
                    trajectory.log_event("human_rejection", {"plan": final_plan.model_dump(mode="json")})
                    st.session_state.discarded = True

    if st.session_state.get("submitted"):
        st.divider()
        if st.session_state.get("fixture_mode"):
            st.warning("Simulated -- fixture mode. Nothing was sent to a real broker.")
        else:
            st.success("Submitted.")
        for line in st.session_state.get("submission_results", []):
            st.write(line)
        if st.button("Start over"):
            st.session_state.reviewed = False
            st.session_state.submitted = False

    if st.session_state.get("discarded"):
        st.divider()
        st.info("Discarded. Nothing was sent to your broker.")
        if st.button("Start over", key="start_over_discard"):
            st.session_state.reviewed = False
            st.session_state.discarded = False

    st.divider()
    with st.expander("Performance (this session's research-driven trades)"):
        fills = load_fills(DEFAULT_LOG_PATH)
        if not fills:
            st.write("No research-driven trades logged yet this session.")
        else:
            attributions = attribute_performance(fills, account)
            rows = [
                {
                    "Strategy": a.strategy_name,
                    "Symbols": ", ".join(a.symbols),
                    "Fills": a.fill_count,
                    "Live unrealized P&L": f"${a.live_unrealized_pnl:,.2f}",
                    "Status": a.status,
                }
                for a in attributions
            ]
            st.table(rows)


if __name__ == "__main__":
    main()
