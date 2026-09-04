"""Logs every approved research-driven trade and attributes live P&L back to the
strategy that proposed it.

Session-scoped, JSONL-appended, deliberately honest about what it can and can't say: with
only one demo session's worth of fills there isn't enough history for a real Sharpe-decay
trend, so a strategy with fewer than `MIN_FILLS_FOR_TREND` logged fills reports
"insufficient live history yet, backtest-based classification stands" instead of a
fabricated trend line.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from orderguard.schemas.account_state import AccountState

DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "eval" / "runs" / "strategy_log.jsonl"
"""Matches the existing `eval/runs/` convention -- already gitignored."""

MIN_FILLS_FOR_TREND = 3


@dataclass(frozen=True)
class StrategyFill:
    """One approved, submitted order attributed to the strategy that proposed it."""

    strategy_name: str
    symbol: str
    side: str
    size_text: str
    order_id: str
    backtest_oos_sharpe: Decimal
    backtest_oos_return_pct: Decimal
    logged_at: datetime


@dataclass(frozen=True)
class StrategyAttribution:
    """Live performance rolled up per strategy, cross-referenced against current
    open positions."""

    strategy_name: str
    symbols: tuple[str, ...]
    fill_count: int
    live_unrealized_pnl: Decimal
    status: str


def log_fill(fill: StrategyFill, log_path: Path = DEFAULT_LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "strategy_name": fill.strategy_name,
        "symbol": fill.symbol,
        "side": fill.side,
        "size_text": fill.size_text,
        "order_id": fill.order_id,
        "backtest_oos_sharpe": str(fill.backtest_oos_sharpe),
        "backtest_oos_return_pct": str(fill.backtest_oos_return_pct),
        "logged_at": fill.logged_at.isoformat(),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_fills(log_path: Path = DEFAULT_LOG_PATH) -> tuple[StrategyFill, ...]:
    if not log_path.exists():
        return ()
    fills = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        fills.append(
            StrategyFill(
                strategy_name=record["strategy_name"],
                symbol=record["symbol"],
                side=record["side"],
                size_text=record["size_text"],
                order_id=record["order_id"],
                backtest_oos_sharpe=Decimal(record["backtest_oos_sharpe"]),
                backtest_oos_return_pct=Decimal(record["backtest_oos_return_pct"]),
                logged_at=datetime.fromisoformat(record["logged_at"]),
            )
        )
    return tuple(fills)


def attribute_performance(fills: tuple[StrategyFill, ...], account: AccountState) -> tuple[StrategyAttribution, ...]:
    """Groups `fills` by strategy and sums each strategy's currently-open positions'
    `unrealized_pl` -- a symbol with no open position (already closed or never filled)
    contributes 0, not an error."""
    positions_by_symbol = {p.symbol: p for p in account.positions}

    by_strategy: dict[str, list[StrategyFill]] = {}
    for fill in fills:
        by_strategy.setdefault(fill.strategy_name, []).append(fill)

    attributions = []
    for name, strategy_fills in sorted(by_strategy.items()):
        symbols = tuple(sorted({f.symbol for f in strategy_fills}))
        live_pnl = sum((positions_by_symbol[s].unrealized_pl for s in symbols if s in positions_by_symbol), Decimal(0))
        status = (
            "insufficient live history yet, backtest-based classification stands"
            if len(strategy_fills) < MIN_FILLS_FOR_TREND
            else f"{len(strategy_fills)} live fills logged"
        )
        attributions.append(
            StrategyAttribution(
                strategy_name=name,
                symbols=symbols,
                fill_count=len(strategy_fills),
                live_unrealized_pnl=live_pnl,
                status=status,
            )
        )
    return tuple(attributions)
