"""Cassette-based caching for LLM calls: every call is keyed on a hash of
(model, temperature, system prompt, user prompt) and stored as
`eval/cassettes/<hash>.json`, committed to the repo.

Three modes:
  live    - always call the wrapped client; every response is written to its cassette.
  replay  - only read cassettes; a miss is a loud error, never falls through to live.
  auto    - replay on hit, live (and write) on miss. Default for local development.

This matters for three reasons: judges reproduce the headline eval number with no API
key (replay mode), repeat eval runs cost nothing, and a cached run is deterministic
regardless of provider sampling.
"""

from __future__ import annotations

import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from orderguard.llm.client import LLMClient

SchemaT = TypeVar("SchemaT", bound=BaseModel)

CASSETTES_DIR = Path(__file__).resolve().parents[3] / "eval" / "cassettes"
MODE_ENV_VAR = "ORDERGUARD_LLM_MODE"
DEFAULT_MODE = "auto"


class CassetteMode(str, Enum):
    LIVE = "live"
    REPLAY = "replay"
    AUTO = "auto"


class CassetteMissError(Exception):
    """Raised in REPLAY mode when no cassette exists for a call. Never falls through
    to a live call -- a miss here means the run is not reproducible without one."""


def resolve_mode(cli_value: str | None = None) -> CassetteMode:
    """Resolves the active mode: an explicit CLI value wins, then the env var, then
    `auto`."""
    raw = cli_value or os.environ.get(MODE_ENV_VAR, DEFAULT_MODE)
    return CassetteMode(raw)


def cache_key(model: str, temperature: float, system: str, user: str) -> str:
    """Deterministic key for one (model, temperature, system, user) call."""
    payload = json.dumps(
        {"model": model, "temperature": temperature, "system": system, "user": user},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CachedLLMClient:
    """Wraps an `LLMClient`, caching every `complete()` call as a cassette file."""

    def __init__(
        self,
        inner: LLMClient,
        model: str,
        temperature: float,
        mode: CassetteMode = CassetteMode.AUTO,
        cassettes_dir: Path = CASSETTES_DIR,
    ) -> None:
        self._inner = inner
        self._model = model
        self._temperature = temperature
        self._mode = mode
        self._cassettes_dir = cassettes_dir

    def complete(self, system: str, user: str, response_schema: type[SchemaT]) -> SchemaT:
        key = cache_key(self._model, self._temperature, system, user)
        path = self._cassettes_dir / f"{key}.json"

        if self._mode == CassetteMode.REPLAY:
            if not path.is_file():
                raise CassetteMissError(
                    f"no cassette for key {key} and replay mode never calls live "
                    f"(model={self._model!r}, temperature={self._temperature})"
                )
            return self._load(path, response_schema)

        if self._mode == CassetteMode.AUTO and path.is_file():
            return self._load(path, response_schema)

        result = self._inner.complete(system, user, response_schema)
        self._save(path, key, system, user, result)
        return result

    def _load(self, path: Path, response_schema: type[SchemaT]) -> SchemaT:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return response_schema.model_validate(payload["response"])

    def _save(self, path: Path, key: str, system: str, user: str, result: BaseModel) -> None:
        self._cassettes_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "key": key,
                    "model": self._model,
                    "temperature": self._temperature,
                    "system": system,
                    "user": user,
                    "response": result.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
