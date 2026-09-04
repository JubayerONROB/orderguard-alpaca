"""Turns a set of researched (BacktestReport, RobustnessReport, StrategyState) triples
into an AllocationPlan: how much of account equity to put behind each ALIVE/WATCH
strategy, with a reserved cash buffer. KILLED strategies never receive an allocation --
that's lifecycle.py's whole point.

Pure and deterministic: a fixed weighted score (OOS edge, robustness, drawdown), a fixed
WATCH haircut, normalized to percentages. Nothing here calls Alpaca or an LLM -- this
just produces numbers; portfolio_guard.py and OrderGuard's own rules are what actually
gate spending them.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from orderguard.research.schemas import (
    AllocationEntry,
    AllocationPlan,
    BacktestReport,
    RobustnessReport,
    StrategyState,
)

DEFAULT_CASH_BUFFER_PCT = Decimal(20)

SHARPE_WEIGHT = Decimal("0.4")
ROBUSTNESS_WEIGHT = Decimal("0.4")
DRAWDOWN_WEIGHT = Decimal("0.2")
"""Score components, must sum to 1 -- edge (OOS sharpe), how hard the adversary failed
to break it, and how gentle its OOS drawdown was."""

SHARPE_CAP = Decimal(3)
"""OOS sharpe is clamped to [0, SHARPE_CAP] before normalizing -- an extreme sharpe from
a handful of trades shouldn't dominate the whole allocation."""

WATCH_HAIRCUT = Decimal("0.5")
"""A WATCH strategy's score is halved relative to an identically-scored ALIVE one --
it still gets capital, just less of it, reflecting the lifecycle's own lower confidence."""


class PortfolioCandidate:
    """One strategy under consideration for allocation."""

    def __init__(self, backtest: BacktestReport, robustness: RobustnessReport, state: StrategyState) -> None:
        self.backtest = backtest
        self.robustness = robustness
        self.state = state


def _score(candidate: PortfolioCandidate) -> Decimal:
    oos = candidate.backtest.oos_metrics
    sharpe_component = max(Decimal(0), min(SHARPE_CAP, oos.sharpe)) / SHARPE_CAP
    robustness_component = candidate.robustness.overall_score / 100
    drawdown_component = 1 / (1 + oos.max_drawdown_pct / 100)

    score = SHARPE_WEIGHT * sharpe_component + ROBUSTNESS_WEIGHT * robustness_component + DRAWDOWN_WEIGHT * drawdown_component
    if candidate.state == StrategyState.WATCH:
        score *= WATCH_HAIRCUT
    return score


def _rationale(candidate: PortfolioCandidate, score: Decimal) -> str:
    oos = candidate.backtest.oos_metrics
    return (
        f"{candidate.state.value.upper()}, robustness {candidate.robustness.overall_score:.0f}/100, "
        f"OOS sharpe {oos.sharpe:.2f}, OOS max drawdown {oos.max_drawdown_pct:.1f}%, "
        f"allocation score {score:.3f}"
    )


def build_allocation_plan(
    candidates: Sequence[PortfolioCandidate], cash_buffer_pct: Decimal = DEFAULT_CASH_BUFFER_PCT
) -> AllocationPlan:
    """Only ALIVE/WATCH candidates ever receive capital -- KILLED ones (and anything
    else) are silently excluded, never zero-allocated as a token entry."""
    eligible = [c for c in candidates if c.state in (StrategyState.ALIVE, StrategyState.WATCH)]
    if not eligible:
        return AllocationPlan(entries=(), cash_buffer_pct=Decimal(100))

    scored = [(c, _score(c)) for c in eligible]
    total_score = sum(s for _, s in scored)
    if total_score <= 0:
        return AllocationPlan(entries=(), cash_buffer_pct=Decimal(100))

    investable_pct = Decimal(100) - cash_buffer_pct
    entries = tuple(
        AllocationEntry(
            strategy_name=c.backtest.strategy_name,
            symbol=c.backtest.symbol,
            target_pct_of_equity=(score / total_score) * investable_pct,
            rationale=_rationale(c, score),
        )
        for c, score in scored
    )
    return AllocationPlan(entries=entries, cash_buffer_pct=cash_buffer_pct)
