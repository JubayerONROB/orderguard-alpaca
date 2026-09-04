"""Unit tests for strategy_discovery.py against MockLLMClient -- no network, no real
Agnes credentials. Mirrors how the compiler is tested (see the existing pattern in
tests/, e.g. test_engine.py's use of hand-built inputs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orderguard.llm.client import MockLLMClient
from orderguard.research.schemas import MarketRegime
from orderguard.research.strategy_discovery import (
    StrategyDiscovery,
    StrategyDiscoveryError,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)

VALID_HYPOTHESIS = {
    "name": "NVDA Volatility Breakout",
    "rationale": "NVDA is in a HIGH-volatility BULLISH regime with ABOVE_AVERAGE volume, "
    "favoring a breakout entry.",
    "universe": ["NVDA"],
    "entry": {"kind": "breakout", "lookback_days": 20, "volume_multiple": "1.5"},
    "exit": {"kind": "atr_stop", "atr_multiple": "2", "atr_lookback_days": 14},
}

INVALID_HYPOTHESIS = {
    "name": "Missing required fields",
    "rationale": "This is missing universe/entry/exit.",
}


def _regime(symbol: str = "NVDA") -> MarketRegime:
    return MarketRegime(
        symbol=symbol,
        trend="BULLISH",
        volatility_regime="HIGH",
        volume_state="ABOVE_AVERAGE",
        current_price="170.00",
        sma_20="160.00",
        realized_vol_20d="0.45",
        avg_volume_20d=1_000_000,
        current_volume=2_000_000,
        as_of=NOW,
    )


def _write_responses(tmp_path: Path, *payloads: dict) -> Path:
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    for i, payload in enumerate(payloads):
        (responses_dir / f"{i:04d}.json").write_text(json.dumps(payload), encoding="utf-8")
    return responses_dir


def test_discover_returns_hypothesis_grounded_in_regime(tmp_path: Path) -> None:
    responses_dir = _write_responses(tmp_path, VALID_HYPOTHESIS)
    llm_client = MockLLMClient(responses_dir)
    discovery = StrategyDiscovery(llm_client)

    hypothesis = discovery.discover("find a momentum idea", (_regime(),))

    assert hypothesis.name == "NVDA Volatility Breakout"
    assert hypothesis.universe == ("NVDA",)
    assert hypothesis.entry.kind == "breakout"
    assert hypothesis.exit.kind == "atr_stop"
    assert hypothesis.generated_at is not None  # filled in by discovery, not the model


def test_discover_retries_once_on_validation_failure(tmp_path: Path) -> None:
    responses_dir = _write_responses(tmp_path, INVALID_HYPOTHESIS, VALID_HYPOTHESIS)
    llm_client = MockLLMClient(responses_dir)
    discovery = StrategyDiscovery(llm_client)

    hypothesis = discovery.discover("find a momentum idea", (_regime(),))

    assert hypothesis.name == "NVDA Volatility Breakout"


def test_discover_raises_after_second_failure(tmp_path: Path) -> None:
    responses_dir = _write_responses(tmp_path, INVALID_HYPOTHESIS, INVALID_HYPOTHESIS)
    llm_client = MockLLMClient(responses_dir)
    discovery = StrategyDiscovery(llm_client)

    with pytest.raises(StrategyDiscoveryError):
        discovery.discover("find a momentum idea", (_regime(),))


def test_render_regime_context_names_the_symbol_and_regime() -> None:
    from orderguard.research.strategy_discovery import render_regime_context

    text = render_regime_context("find something bullish", (_regime(),))
    assert "NVDA" in text
    assert "BULLISH" in text
    assert "HIGH" in text
    assert "find something bullish" in text


def test_universe_is_uppercased_via_schema(tmp_path: Path) -> None:
    lowercase = dict(VALID_HYPOTHESIS, universe=["nvda"])
    responses_dir = _write_responses(tmp_path, lowercase)
    llm_client = MockLLMClient(responses_dir)
    discovery = StrategyDiscovery(llm_client)

    hypothesis = discovery.discover("find a momentum idea", (_regime(),))
    assert hypothesis.universe == ("NVDA",)
