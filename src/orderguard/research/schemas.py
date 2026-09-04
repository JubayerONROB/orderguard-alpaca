"""Contracts for the research pipeline: market regime, strategy hypotheses, backtest
and robustness reports, lifecycle state, and capital allocation.

`EntryRule`/`ExitRule` are deliberately a constrained, enumerated vocabulary rather than
a free-form strategy DSL -- the LLM (strategy_discovery.py) must express any idea using
one of these shapes so that backtest_engine.py can actually simulate it deterministically.
A hypothesis the engine can't execute isn't useful, however creative it sounds.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MarketRegime(BaseModel):
    """Deterministic, indicator-based read of one symbol's current regime.

    Every field here is computed from real historical bars by market_intelligence.py --
    nothing in this model is an LLM opinion.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    trend: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    volatility_regime: Literal["LOW", "NORMAL", "HIGH"]
    volume_state: Literal["BELOW_AVERAGE", "AVERAGE", "ABOVE_AVERAGE"]
    current_price: Decimal = Field(gt=0)
    sma_20: Decimal = Field(gt=0)
    realized_vol_20d: Decimal = Field(ge=0)
    """Annualized stdev of daily returns over the trailing 20 sessions, as a fraction (0.31 = 31%)."""
    avg_volume_20d: int = Field(ge=0)
    current_volume: int = Field(ge=0)
    as_of: datetime

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class EntryRule(BaseModel):
    """One of three deterministically-simulatable entry conditions."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["breakout", "ma_crossover", "momentum_threshold"]
    lookback_days: int = Field(gt=0)
    threshold_pct: Decimal | None = Field(default=None)
    """momentum_threshold: minimum trailing `lookback_days` return (as a percent, e.g.
    5 means 5%) required to enter."""
    volume_multiple: Decimal | None = Field(default=None, gt=0)
    """breakout: today's volume must be at least this multiple of the `lookback_days` average."""
    fast_ma_days: int | None = Field(default=None, gt=0)
    slow_ma_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_required_params(self) -> EntryRule:
        if self.kind == "momentum_threshold" and self.threshold_pct is None:
            raise ValueError("momentum_threshold entry requires threshold_pct")
        if self.kind == "breakout" and self.volume_multiple is None:
            raise ValueError("breakout entry requires volume_multiple")
        if self.kind == "ma_crossover" and (self.fast_ma_days is None or self.slow_ma_days is None):
            raise ValueError("ma_crossover entry requires fast_ma_days and slow_ma_days")
        if (
            self.kind == "ma_crossover"
            and self.fast_ma_days is not None
            and self.slow_ma_days is not None
            and self.fast_ma_days >= self.slow_ma_days
        ):
            raise ValueError("ma_crossover requires fast_ma_days < slow_ma_days")
        return self


class ExitRule(BaseModel):
    """One of three deterministically-simulatable exit conditions."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["atr_stop", "trailing_stop_pct", "fixed_hold_days"]
    atr_multiple: Decimal | None = Field(default=None, gt=0)
    atr_lookback_days: int | None = Field(default=None, gt=0)
    trailing_pct: Decimal | None = Field(default=None, gt=0)
    hold_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_required_params(self) -> ExitRule:
        if self.kind == "atr_stop" and (self.atr_multiple is None or self.atr_lookback_days is None):
            raise ValueError("atr_stop exit requires atr_multiple and atr_lookback_days")
        if self.kind == "trailing_stop_pct" and self.trailing_pct is None:
            raise ValueError("trailing_stop_pct exit requires trailing_pct")
        if self.kind == "fixed_hold_days" and self.hold_days is None:
            raise ValueError("fixed_hold_days exit requires hold_days")
        return self


class StrategyHypothesis(BaseModel):
    """The LLM's proposal: a name, a rationale, and a concrete, simulatable rule pair.

    Proposing this is all strategy_discovery.py does -- it never claims the strategy
    works. Whether it does is backtest_engine.py's and adversary.py's job, not the
    model's.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    universe: tuple[str, ...] = Field(min_length=1)
    entry: EntryRule
    exit: ExitRule
    generated_at: datetime

    @field_validator("universe")
    @classmethod
    def _uppercase_universe(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(s.upper() for s in v)


class PerformanceMetrics(BaseModel):
    """Backtest performance over one window (train or out-of-sample)."""

    model_config = ConfigDict(frozen=True)

    total_return_pct: Decimal
    sharpe: Decimal
    """Annualized, computed from daily strategy returns (0 if fewer than 2 trades)."""
    win_rate_pct: Decimal = Field(ge=0, le=100)
    max_drawdown_pct: Decimal = Field(ge=0)
    profit_factor: Decimal | None = None
    """Gross profit / gross loss. None when there were no losing trades to divide by,
    or no trades at all -- never a fabricated number."""
    trade_count: int = Field(ge=0)


class BacktestReport(BaseModel):
    """Chronological 70/30 train/out-of-sample backtest result for one hypothesis."""

    model_config = ConfigDict(frozen=True)

    strategy_name: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    train_metrics: PerformanceMetrics
    oos_metrics: PerformanceMetrics
    train_start: datetime
    train_end: datetime
    oos_start: datetime
    oos_end: datetime

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class RobustnessReport(BaseModel):
    """Adversarial parameter-sensitivity result: how much the strategy degrades under
    perturbation, and how stable train performance is versus out-of-sample."""

    model_config = ConfigDict(frozen=True)

    strategy_name: str = Field(min_length=1)
    parameter_sensitivity_score: Decimal = Field(ge=0, le=100)
    oos_stability_score: Decimal = Field(ge=0, le=100)
    overall_score: Decimal = Field(ge=0, le=100)
    verdict: Literal["PASS", "FAIL"]
    grid_points_tested: int = Field(ge=0)


class StrategyState(str, Enum):
    """Lifecycle classification derived from a RobustnessReport (see lifecycle.py)."""

    ALIVE = "alive"
    WATCH = "watch"
    KILLED = "killed"


class AllocationEntry(BaseModel):
    """One strategy's share of the portfolio."""

    model_config = ConfigDict(frozen=True)

    strategy_name: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    target_pct_of_equity: Decimal = Field(ge=0, le=100)
    rationale: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class AllocationPlan(BaseModel):
    """The portfolio manager's output: how much equity to put behind each ALIVE/WATCH
    strategy, with a reserved cash buffer."""

    model_config = ConfigDict(frozen=True)

    entries: tuple[AllocationEntry, ...] = ()
    cash_buffer_pct: Decimal = Field(ge=0, le=100)
