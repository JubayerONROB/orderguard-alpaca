"""Constructs the cassette-cached Agnes client eval systems share.

In `live`/`auto` mode, loads real settings from `.env` (base URL, API key, model) --
those modes can genuinely reach the network. In `replay` mode, `.env` is never read at
all: the underlying `AgnesClient` is built from a placeholder that's guaranteed never
to be called (`CachedLLMClient` raises `CassetteMissError` on a miss before ever
touching it), using `_REPLAY_MODEL_FALLBACK` for the `model` field specifically because
that value is part of the cassette cache key and must match what recorded the cassette.
This is what makes "replay needs no API key" (see docs/REPRODUCTION.md) literally true
rather than aspirational -- constructing `Settings()` unconditionally here would fail
loudly on a machine with no `.env` at all, even for a pure cache replay.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from orderguard.config import get_settings
from orderguard.llm.cassette import CachedLLMClient, CassetteMode, resolve_mode
from orderguard.llm.client import AGNES_DEFAULT_TEMPERATURE, AgnesClient, LLMClient

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_REPLAY_MODEL_FALLBACK = "agnes-2.5-flash"
"""Must match the `AGNES_DEFAULT_MODEL` value used to record `eval/cassettes/` (see
`.env.example`'s AGNES_MODEL_FLASH) -- not a secret, just an identifier, and part of the
cassette cache key, so replay mode needs the real value here even without real credentials."""


class CountingLLMClient:
    """Wraps an `LLMClient`, counting every `complete()` call (cache hit or miss).

    Used by eval systems to report `total_llm_calls` -- a logical count of how many
    times the system invoked the LLM interface, independent of whether the cassette
    cache actually touched the network.
    """

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.call_count = 0

    def complete(self, system: str, user: str, response_schema: type[SchemaT]) -> SchemaT:
        self.call_count += 1
        return self._inner.complete(system, user, response_schema)


def build_llm_client(mode: CassetteMode | None = None) -> CachedLLMClient:
    """Builds the shared cached Agnes client for eval systems.

    Args:
        mode: overrides the resolved mode (CLI flag > env var > "auto") when given.
    """
    resolved_mode = mode if mode is not None else resolve_mode()

    if resolved_mode == CassetteMode.REPLAY:
        inner = AgnesClient(
            base_url="unused-in-replay-mode",
            api_key="unused-in-replay-mode",
            model=_REPLAY_MODEL_FALLBACK,
            timeout=1,
            max_retries=0,
            temperature=AGNES_DEFAULT_TEMPERATURE,
        )
        return CachedLLMClient(
            inner=inner,
            model=_REPLAY_MODEL_FALLBACK,
            temperature=AGNES_DEFAULT_TEMPERATURE,
            mode=resolved_mode,
        )

    settings = get_settings()
    inner = AgnesClient(
        base_url=settings.agnes_base_url,
        api_key=settings.agnes_api_key,
        model=settings.agnes_default_model,
        timeout=settings.agnes_timeout,
        max_retries=settings.agnes_max_retries,
        temperature=AGNES_DEFAULT_TEMPERATURE,
    )
    return CachedLLMClient(
        inner=inner,
        model=settings.agnes_default_model,
        temperature=AGNES_DEFAULT_TEMPERATURE,
        mode=resolved_mode,
    )
