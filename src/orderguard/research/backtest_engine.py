"""Deterministic, pure simulation of a `StrategyHypothesis` over historical daily bars.

No LLM, no I/O -- given a hypothesis and a chronologically-ordered (oldest first) bar
sequence for one symbol, this splits the series chronologically 70/30 (never randomly --
order matters for time series) and independently simulates the entry/exit rule over each
window, producing a `BacktestReport`. This is a single, non-walk-forward split, stated
plainly as a scope cut rather than silently simplified.

Entry/exit evaluation reuses the same `EntryRule`/`ExitRule` vocabulary strategy_discovery
is constrained to -- see schemas.py's module docstring for why that vocabulary exists.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from orderguard.research.schemas import (
    BacktestReport,
    EntryRule,
    ExitRule,
    PerformanceMetrics,
    StrategyHypothesis,
)

TRAIN_SPLIT_FRACTION = Decimal("0.7")
TRADING_DAYS_PER_YEAR = 252
MIN_BARS = 60
"""Below this, a 70/30 split leaves too little of either window to mean anything."""


class BacktestBar(Protocol):
    """The shape the simulator needs -- richer than market_intelligence.Bar because
    ATR needs high/low. Real alpaca-py bars satisfy both protocols already."""

    open: float | Decimal
    high: float | Decimal
    low: float | Decimal
    close: float | Decimal
    volume: int
    timestamp: datetime


class InsufficientHistoryError(Exception):
    """Raised when fewer than `MIN_BARS` bars are available for a symbol."""

    def __init__(self, symbol: str, bars_available: int, bars_needed: int = MIN_BARS) -> None:
        self.symbol = symbol
        self.bars_available = bars_available
        self.bars_needed = bars_needed
        super().__init__(f"{symbol}: only {bars_available} bars available, need at least {bars_needed}")


def _dec_list(bars: Sequence[BacktestBar], attr: str) -> list[Decimal]:
    return [Decimal(str(getattr(b, attr))) for b in bars]


def _true_range(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], i: int) -> Decimal:
    if i == 0:
        return highs[i] - lows[i]
    return max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))


def _atr(highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], end_index: int, lookback: int) -> Decimal:
    """Average true range over the `lookback` sessions ending at (and including) `end_index`."""
    start = max(0, end_index - lookback + 1)
    trs = [_true_range(highs, lows, closes, i) for i in range(start, end_index + 1)]
    return sum(trs) / len(trs) if trs else Decimal(0)


def _sma(values: list[Decimal], end_index: int, window: int) -> Decimal | None:
    start = end_index - window + 1
    if start < 0:
        return None
    return sum(values[start : end_index + 1]) / window


def _entry_signal(entry: EntryRule, closes: list[Decimal], volumes: list[int], i: int) -> bool:
    if entry.kind == "breakout":
        lb = entry.lookback_days
        if i < lb:
            return False
        prior_high = max(closes[i - lb : i])
        avg_vol = sum(volumes[i - lb : i]) / lb
        volume_ok = avg_vol == 0 or Decimal(volumes[i]) >= entry.volume_multiple * Decimal(str(avg_vol))
        return closes[i] > prior_high and volume_ok

    if entry.kind == "momentum_threshold":
        lb = entry.lookback_days
        if i < lb or closes[i - lb] == 0:
            return False
        trailing_return_pct = (closes[i] - closes[i - lb]) / closes[i - lb] * 100
        return trailing_return_pct > entry.threshold_pct

    if entry.kind == "ma_crossover":
        fast, slow = entry.fast_ma_days, entry.slow_ma_days
        if i < slow:
            return False
        fast_now, slow_now = _sma(closes, i, fast), _sma(closes, i, slow)
        fast_prev, slow_prev = _sma(closes, i - 1, fast), _sma(closes, i - 1, slow)
        if None in (fast_now, slow_now, fast_prev, slow_prev):
            return False
        return fast_prev <= slow_prev and fast_now > slow_now

    return False


def _exit_index(
    exit_rule: ExitRule, highs: list[Decimal], lows: list[Decimal], closes: list[Decimal], entry_index: int, window_end: int
) -> int:
    """Index (> entry_index, <= window_end) at which the position exits. If nothing
    triggers, the position is marked-to-market at `window_end`."""
    if exit_rule.kind == "fixed_hold_days":
        return min(entry_index + exit_rule.hold_days, window_end)

    if exit_rule.kind == "atr_stop":
        atr = _atr(highs, lows, closes, entry_index, exit_rule.atr_lookback_days)
        stop_price = closes[entry_index] - exit_rule.atr_multiple * atr
        for i in range(entry_index + 1, window_end + 1):
            if closes[i] <= stop_price:
                return i
        return window_end

    if exit_rule.kind == "trailing_stop_pct":
        peak = closes[entry_index]
        for i in range(entry_index + 1, window_end + 1):
            peak = max(peak, closes[i])
            if closes[i] <= peak * (1 - exit_rule.trailing_pct / 100):
                return i
        return window_end

    return window_end


def _simulate_window(
    entry: EntryRule,
    exit_rule: ExitRule,
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    volumes: list[int],
    start: int,
    end: int,
) -> tuple[list[Decimal], list[Decimal]]:
    """One pass over [start, end] inclusive: at most one open position at a time.
    Returns (per-trade returns, daily close-to-close returns while in a position --
    0 on flat days, used for Sharpe/drawdown)."""
    trades: list[Decimal] = []
    daily_returns = [Decimal(0)] * (end - start + 1)
    i = start
    while i <= end:
        if _entry_signal(entry, closes, volumes, i):
            exit_i = _exit_index(exit_rule, highs, lows, closes, i, end)
            if exit_i > i:
                trades.append((closes[exit_i] - closes[i]) / closes[i])
                for d in range(i + 1, exit_i + 1):
                    daily_returns[d - start] = (closes[d] - closes[d - 1]) / closes[d - 1]
                i = exit_i + 1
                continue
        i += 1
    return trades, daily_returns


def _metrics_from(trades: list[Decimal], daily_returns: list[Decimal]) -> PerformanceMetrics:
    equity = Decimal(1)
    peak = Decimal(1)
    max_dd = Decimal(0)
    for r in daily_returns:
        equity *= 1 + r
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    nonzero = [float(r) for r in daily_returns if r != 0]
    if len(nonzero) >= 2:
        stdev = statistics.pstdev(nonzero)
        sharpe = Decimal(str(statistics.mean(nonzero) / stdev * (TRADING_DAYS_PER_YEAR**0.5))) if stdev > 0 else Decimal(0)
    else:
        sharpe = Decimal(0)

    trade_count = len(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    win_rate_pct = (Decimal(len(wins)) / trade_count * 100) if trade_count else Decimal(0)
    gross_profit = sum(wins) if wins else Decimal(0)
    gross_loss = -sum(losses) if losses else Decimal(0)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    return PerformanceMetrics(
        total_return_pct=(equity - 1) * 100,
        sharpe=sharpe,
        win_rate_pct=win_rate_pct,
        max_drawdown_pct=max_dd,
        profit_factor=profit_factor,
        trade_count=trade_count,
    )


def run_backtest(hypothesis: StrategyHypothesis, symbol: str, bars: Sequence[BacktestBar]) -> BacktestReport:
    """Chronologically splits `bars` 70/30 and simulates `hypothesis` independently over
    each window.

    Raises:
        InsufficientHistoryError: fewer than `MIN_BARS` bars given.
    """
    if len(bars) < MIN_BARS:
        raise InsufficientHistoryError(symbol=symbol, bars_available=len(bars))

    closes = _dec_list(bars, "close")
    highs = _dec_list(bars, "high")
    lows = _dec_list(bars, "low")
    volumes = [int(b.volume) for b in bars]
    timestamps = [b.timestamp for b in bars]

    n = len(bars)
    split = int(n * TRAIN_SPLIT_FRACTION)
    train_start, train_end = 0, split - 1
    oos_start, oos_end = split, n - 1

    train_trades, train_daily = _simulate_window(
        hypothesis.entry, hypothesis.exit, highs, lows, closes, volumes, train_start, train_end
    )
    oos_trades, oos_daily = _simulate_window(
        hypothesis.entry, hypothesis.exit, highs, lows, closes, volumes, oos_start, oos_end
    )

    return BacktestReport(
        strategy_name=hypothesis.name,
        symbol=symbol,
        train_metrics=_metrics_from(train_trades, train_daily),
        oos_metrics=_metrics_from(oos_trades, oos_daily),
        train_start=timestamps[train_start],
        train_end=timestamps[train_end],
        oos_start=timestamps[oos_start],
        oos_end=timestamps[oos_end],
    )
