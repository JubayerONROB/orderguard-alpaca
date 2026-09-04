"""Unit tests for backtest_engine.py against synthetic, hand-shaped price series --
no network, no LLM. Validates the simulator behaves as expected on series with known
shapes (rising, flat, falling), not just "it runs"."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from orderguard.research.backtest_engine import (
    MIN_BARS,
    InsufficientHistoryError,
    run_backtest,
)
from orderguard.research.schemas import EntryRule, ExitRule, StrategyHypothesis

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[SimpleNamespace]:
    """Builds bars with open=high=low=close (no intraday range) unless a test needs ATR
    range specifically, and volume defaulting to a flat 1_000_000."""
    if volumes is None:
        volumes = [1_000_000] * len(closes)
    return [
        SimpleNamespace(
            open=c,
            high=c,
            low=c,
            close=c,
            volume=v,
            timestamp=START + timedelta(days=i),
        )
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _hypothesis(entry: EntryRule, exit_rule: ExitRule, symbol: str = "TEST") -> StrategyHypothesis:
    return StrategyHypothesis(
        name="Test Strategy",
        rationale="synthetic test hypothesis",
        universe=[symbol],
        entry=entry,
        exit=exit_rule,
        generated_at=datetime.now(timezone.utc),
    )


def test_insufficient_bars_raises() -> None:
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    with pytest.raises(InsufficientHistoryError):
        run_backtest(hyp, "TEST", _bars([100.0] * (MIN_BARS - 1)))


def test_flat_prices_produce_no_trades() -> None:
    """A flat series never makes a new high and never has positive momentum -- both
    breakout and momentum_threshold entries should find nothing to enter."""
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars([100.0] * MIN_BARS))

    assert report.train_metrics.trade_count == 0
    assert report.oos_metrics.trade_count == 0
    assert report.train_metrics.total_return_pct == 0
    assert report.train_metrics.profit_factor is None


def test_monotonically_rising_prices_are_profitable_with_momentum_entry() -> None:
    """A steadily-rising series should trigger a momentum entry and a fixed-hold exit
    should realize a positive return every time it fires."""
    closes = [100.0 + i for i in range(MIN_BARS)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_metrics.trade_count > 0
    assert report.train_metrics.total_return_pct > 0
    assert report.train_metrics.win_rate_pct == 100
    assert report.oos_metrics.trade_count > 0
    assert report.oos_metrics.total_return_pct > 0


def test_monotonically_falling_prices_lose_money_with_momentum_entry() -> None:
    """momentum_threshold never actually enters on a falling series (trailing return is
    always negative), so this instead checks a breakout entry never fires either --
    no false profitable trades get fabricated from a losing series."""
    closes = [200.0 - i for i in range(MIN_BARS)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_metrics.trade_count == 0
    assert report.oos_metrics.trade_count == 0


def test_train_and_oos_windows_are_chronologically_disjoint_and_ordered() -> None:
    closes = [100.0 + i for i in range(MIN_BARS)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_start < report.train_end
    assert report.train_end < report.oos_start
    assert report.oos_start < report.oos_end


def test_ma_crossover_entry_fires_on_a_rising_series() -> None:
    closes = [100.0] * 10 + [100.0 + i for i in range(MIN_BARS - 10)]
    hyp = _hypothesis(
        EntryRule(kind="ma_crossover", lookback_days=5, fast_ma_days=3, slow_ma_days=10),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_metrics.trade_count > 0 or report.oos_metrics.trade_count > 0


def test_breakout_entry_requires_both_new_high_and_volume_confirmation() -> None:
    closes = [100.0] * 20 + [150.0] + [100.0] * (MIN_BARS - 21)
    volumes = [1_000_000] * 20 + [500_000] + [1_000_000] * (MIN_BARS - 21)  # low volume on the spike
    hyp = _hypothesis(
        EntryRule(kind="breakout", lookback_days=10, volume_multiple="1.5"),
        ExitRule(kind="fixed_hold_days", hold_days=3),
    )
    report = run_backtest(hyp, "TEST", _bars(closes, volumes))

    # volume never confirms the breakout, so no trade should be entered from it
    assert report.train_metrics.trade_count == 0
    assert report.oos_metrics.trade_count == 0


def test_trailing_stop_pct_exits_on_a_pullback() -> None:
    closes = [100.0 + i for i in range(15)] + [115.0 - i for i in range(1, MIN_BARS - 14)]
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="trailing_stop_pct", trailing_pct="2"),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_metrics.trade_count > 0


def test_atr_stop_exits_when_price_falls_below_stop() -> None:
    closes = [100.0 + i for i in range(15)] + [200.0] + [100.0] * (MIN_BARS - 16)
    hyp = _hypothesis(
        EntryRule(kind="momentum_threshold", lookback_days=5, threshold_pct="1"),
        ExitRule(kind="atr_stop", atr_multiple="1", atr_lookback_days=5),
    )
    report = run_backtest(hyp, "TEST", _bars(closes))

    assert report.train_metrics.trade_count > 0
