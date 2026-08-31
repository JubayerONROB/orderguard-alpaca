"""Provider-agnostic LLM access for the intent compiler and repair agent.

`LLMClient` is the only interface either of those modules should depend on -- never a
provider SDK directly. `AgnesClient` is the real implementation, built against Agnes's
OpenAI-compatible surface (`agnes.json` declares `"compatible_with": "openai"`: a
`base_url` + Bearer `api_key`, one `/chat/completions`-style endpoint, model names
selected by string). `MockLLMClient` replays canned responses from disk so the compiler,
repair agent, and eval harness can run in `tests/` and `eval/` with zero network calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMClient(Protocol):
    """A single structured-completion call, provider details hidden behind this method."""

    def complete(self, system: str, user: str, response_schema: type[SchemaT]) -> SchemaT:
        """Runs one completion and validates the model's output against `response_schema`.

        Args:
            system: system prompt.
            user: user-turn content (e.g. the trader's instruction plus grounding context).
            response_schema: Pydantic model the response must validate against.

        Returns:
            An instance of `response_schema` built from the model's output.
        """
        ...


AGNES_DEFAULT_TEMPERATURE = 0
"""Default sampling temperature for Agnes completions. The compiler and repair agent
are the only components that call an LLM, and eval reproducibility depends on the
model being as deterministic as the provider allows -- 0 is that ceiling, not a
tuning choice, so it's the default rather than something callers pick per-call."""


class AgnesTransportError(Exception):
    """Raised when every retry against Agnes's HTTP endpoint fails (network, timeout,
    non-2xx status). Distinct from a schema-validation failure -- see
    `compiler/intent_compiler.py` for how those two failure modes are handled
    differently (this one isn't retried at the compiler level at all; `max_retries`
    below already covers transient transport failures)."""


class AgnesClient:
    """`LLMClient` backed by Agnes's OpenAI-compatible chat-completions endpoint.

    Built from `agnes.json`'s declared shape: Bearer-token auth against `base_url`,
    JSON-mode/structured output requested via `response_schema`, and model selection
    via a plain string (e.g. `default_model`).

    `max_retries` here covers only transport-level failures (timeouts, non-2xx,
    connection errors) -- retrying a schema-invalid response is a different concern,
    handled one level up by the caller (e.g. `IntentCompiler`), since only the caller
    knows how to usefully append the validation error to the next prompt.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int,
        max_retries: int,
        temperature: float = AGNES_DEFAULT_TEMPERATURE,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._temperature = temperature

    def complete(self, system: str, user: str, response_schema: type[SchemaT]) -> SchemaT:
        schema = response_schema.model_json_schema()
        user_with_schema = (
            f"{user}\n\n---\n"
            f"Respond with ONLY a single JSON object matching this JSON Schema. "
            f"No prose, no markdown code fences, no explanation -- just the JSON object.\n\n"
            f"{json.dumps(schema)}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_with_schema},
        ]

        last_error: Exception | None = None
        response = None
        for _ in range(self._max_retries + 1):
            try:
                response = httpx.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self._model,
                        "temperature": self._temperature,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError as e:
                last_error = e
                response = None
                continue

        if response is None:
            raise AgnesTransportError(
                f"Agnes completion failed after {self._max_retries + 1} attempt(s): {last_error}"
            ) from last_error

        content = response.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)  # json.JSONDecodeError propagates to the caller
        return response_schema.model_validate(payload)  # pydantic.ValidationError propagates to the caller


class MockLLMClient:
    """`LLMClient` that replays a canned response from disk instead of calling a model.

    Each call pops the next response file from `responses_dir` (sorted by filename),
    parses it as JSON, and validates it against `response_schema`. Used by `tests/` and
    `eval/baselines/` to keep those runs network-free and deterministic.
    """

    def __init__(self, responses_dir: Path) -> None:
        self._responses_dir = responses_dir
        self._remaining_responses = iter(sorted(responses_dir.glob("*.json")))

    def complete(self, system: str, user: str, response_schema: type[SchemaT]) -> SchemaT:
        response_path = next(self._remaining_responses)
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        return response_schema.model_validate(payload)
