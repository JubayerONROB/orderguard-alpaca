"""Proposes a strategy hypothesis from a trader's research prompt and real market
regime data. Mirrors compiler/intent_compiler.py's pattern exactly: same `LLMClient`
protocol (so this is cassette-cached and replay-testable for free), same one-retry-on-
schema-failure behavior, same "don't ask the model to echo back what we already know"
principle (it produces a `DiscoveredHypothesis`, not the full `StrategyHypothesis` --
`generated_at` is filled in by this module, not invented by the model).

This is the second of the (now three) places in OrderGuard allowed to call an LLM. Like
the compiler, it produces an UNVALIDATED proposal -- it does not claim the strategy
works. That's backtest_engine.py's and adversary.py's job.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from orderguard.audit.trajectory import TrajectoryLogger
from orderguard.llm.client import LLMClient
from orderguard.research.schemas import (
    EntryRule,
    ExitRule,
    MarketRegime,
    StrategyHypothesis,
)

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "discovery_v1.md"


class StrategyDiscoveryError(Exception):
    """Raised when discovery fails to produce a valid hypothesis after its one retry."""


class DiscoveredHypothesis(BaseModel):
    """The shape the LLM actually produces -- `generated_at` is filled in by this
    module afterward, not asked of the model."""

    model_config = ConfigDict(frozen=True)

    name: str
    rationale: str
    universe: tuple[str, ...]
    entry: EntryRule
    exit: ExitRule


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def render_regime_context(research_prompt: str, regimes: tuple[MarketRegime, ...]) -> str:
    """Renders the computed regimes as compact text, not raw JSON -- same reasoning as
    `compiler.intent_compiler.render_context`."""
    lines: list[str] = []
    lines.append("Market regimes (all computed from real historical data, not opinion):")
    for r in regimes:
        lines.append(
            f"  {r.symbol}: {r.trend}, {r.volatility_regime} volatility "
            f"({r.realized_vol_20d * 100:.1f}% annualized), {r.volume_state} volume "
            f"(price ${r.current_price}, 20d SMA ${r.sma_20})"
        )
    lines.append(f'\nTrader\'s research prompt: "{research_prompt}"')
    return "\n".join(lines)


class StrategyDiscovery:
    """Turns (research_prompt, MarketRegime tuple) into a StrategyHypothesis."""

    def __init__(self, llm_client: LLMClient, trajectory: TrajectoryLogger | None = None) -> None:
        self._llm_client = llm_client
        self._trajectory = trajectory

    def _log(self, event_type: str, payload: dict) -> None:
        if self._trajectory is not None:
            self._trajectory.log_event(event_type, payload)

    def discover(self, research_prompt: str, regimes: tuple[MarketRegime, ...]) -> StrategyHypothesis:
        """Proposes one hypothesis grounded in `regimes`.

        On a schema-validation failure, retries ONCE with the error text appended. A
        second failure is a hard error (`StrategyDiscoveryError`), never a silent
        fallback hypothesis.
        """
        system_prompt = _load_system_prompt()
        user_prompt = render_regime_context(research_prompt, regimes)

        self._log("discovery_prompt", {"system": system_prompt, "user": user_prompt})

        try:
            discovered = self._llm_client.complete(system_prompt, user_prompt, DiscoveredHypothesis)
        except (ValidationError, json.JSONDecodeError) as e:
            self._log("discovery_validation_error", {"error": str(e), "attempt": 1})
            retry_prompt = (
                f"{user_prompt}\n\n---\n"
                f"Your previous response was invalid: {e}\n"
                f"Respond again with ONLY the corrected JSON object."
            )
            self._log("discovery_retry_prompt", {"user": retry_prompt})
            try:
                discovered = self._llm_client.complete(system_prompt, retry_prompt, DiscoveredHypothesis)
            except (ValidationError, json.JSONDecodeError) as e2:
                self._log("discovery_hard_failure", {"error": str(e2), "attempt": 2})
                raise StrategyDiscoveryError(
                    f"strategy discovery failed to produce a valid hypothesis after 1 retry: {e2}"
                ) from e2

        self._log("discovery_response", {"hypothesis": discovered.model_dump(mode="json")})

        return StrategyHypothesis(
            name=discovered.name,
            rationale=discovered.rationale,
            universe=discovered.universe,
            entry=discovered.entry,
            exit=discovered.exit,
            generated_at=datetime.now(timezone.utc),
        )
