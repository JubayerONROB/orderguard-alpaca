"""Streamlit approval screen: the one place a human clears a basket for submission.

Renders a compiled OrderPlan alongside its RiskReport (including any repairs), and is
the sole path through which an approved plan is allowed to reach `/submit`. Nothing
is ever submitted without an explicit "APPROVE PAPER TRADE" click -- that is the
product, not a detail.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

# `eval/` is a plain directory at the repo root, not part of the installed `orderguard`
# package -- Streamlit Cloud (and some local invocations) don't reliably put the repo
# root on sys.path just because ui/app.py lives one level under it, so `import eval...`
# below would fail unpredictably depending on how/where the app was launched from.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from eval.cases import CASES_DIR, load_case
from eval.fixtures import FIXTURES_DIR, Fixture, load_fixture
from eval.llm_setup import build_llm_client
from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.broker.alpaca_client import AlpacaClient
from orderguard.compiler.intent_compiler import IntentCompiler
from orderguard.config import get_settings
from orderguard.llm.cassette import CassetteMode
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

    _render_account_panel(account)

    st.divider()
    instruction = st.text_input("Instruction", value=_default_instruction())

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
                        st.session_state.submission_results = [
                            "SIMULATED -- fixture mode, no broker connected. No real order was placed."
                        ]
                    else:
                        client = AlpacaClient(get_settings())
                        results = []
                        for order_id in final_plan.cancellations:
                            client.cancel_order(order_id)
                            results.append(f"Cancelled {order_id}")
                        for order in final_plan.orders:
                            submitted_id = client.submit_order(order)
                            results.append(f"Submitted {order.side.value} {order.symbol} -> order {submitted_id}")
                        st.session_state.submission_results = results
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


if __name__ == "__main__":
    main()
