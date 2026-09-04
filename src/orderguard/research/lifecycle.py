"""Classifies a strategy's lifecycle state from its adversary RobustnessReport. Pure,
trivial, fixed thresholds -- deliberately simple so there's nothing here that could
disagree with what the adversary already measured."""

from __future__ import annotations

from decimal import Decimal

from orderguard.research.schemas import RobustnessReport, StrategyState

ALIVE_THRESHOLD = Decimal(75)
WATCH_THRESHOLD = Decimal(50)
"""overall_score >= 75 -> ALIVE, 50 <= overall_score < 75 -> WATCH, < 50 -> KILLED."""


def classify(robustness: RobustnessReport) -> StrategyState:
    if robustness.overall_score >= ALIVE_THRESHOLD:
        return StrategyState.ALIVE
    if robustness.overall_score >= WATCH_THRESHOLD:
        return StrategyState.WATCH
    return StrategyState.KILLED
