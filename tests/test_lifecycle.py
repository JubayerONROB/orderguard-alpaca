"""Boundary tests for lifecycle.classify -- the thresholds themselves are the entire
behavior here, so the tests exist to pin them exactly."""

from __future__ import annotations

from decimal import Decimal

from orderguard.research.lifecycle import classify
from orderguard.research.schemas import RobustnessReport, StrategyState


def _report(overall_score: str) -> RobustnessReport:
    return RobustnessReport(
        strategy_name="Test Strategy",
        parameter_sensitivity_score=Decimal(overall_score),
        oos_stability_score=Decimal(overall_score),
        overall_score=Decimal(overall_score),
        verdict="PASS" if Decimal(overall_score) >= 50 else "FAIL",
        grid_points_tested=9,
    )


def test_score_at_75_is_alive() -> None:
    assert classify(_report("75")) == StrategyState.ALIVE


def test_score_just_below_75_is_watch() -> None:
    assert classify(_report("74.99")) == StrategyState.WATCH


def test_score_at_50_is_watch() -> None:
    assert classify(_report("50")) == StrategyState.WATCH


def test_score_just_below_50_is_killed() -> None:
    assert classify(_report("49.99")) == StrategyState.KILLED


def test_score_of_100_is_alive() -> None:
    assert classify(_report("100")) == StrategyState.ALIVE


def test_score_of_0_is_killed() -> None:
    assert classify(_report("0")) == StrategyState.KILLED
