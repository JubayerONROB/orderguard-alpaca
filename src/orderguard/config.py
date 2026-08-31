"""Loads configuration from environment / .env and fails loudly on missing keys.

No key here has a silent default. If a required setting is absent, constructing
`Settings` raises -- OrderGuard should never run against a half-configured
environment, especially one where a broker or LLM key is unexpectedly empty.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application configuration, loaded from `.env`.

    Every field is required (no `= None` / `= ""` defaults) so that a missing
    environment variable surfaces as a startup-time validation error instead
    of a confusing runtime failure deep inside a client.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    agnes_base_url: str = Field(alias="AGNES_BASE_URL")
    agnes_api_key: str = Field(alias="AGNES_API_KEY")
    agnes_model_flash: str = Field(alias="AGNES_MODEL_FLASH")
    agnes_model_pro: str = Field(alias="AGNES_MODEL_PRO")
    agnes_model_turbo: str = Field(alias="AGNES_MODEL_TURBO")
    agnes_default_model: str = Field(alias="AGNES_DEFAULT_MODEL")
    agnes_timeout: int = Field(alias="AGNES_TIMEOUT")
    agnes_max_retries: int = Field(alias="AGNES_MAX_RETRIES")

    alpaca_environment: str = Field(alias="ALPACA_ENVIRONMENT")
    alpaca_endpoint: str = Field(alias="ALPACA_ENDPOINT")
    alpaca_api_key: str = Field(alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(alias="ALPACA_SECRET_KEY")
    alpaca_paper: bool = Field(alias="ALPACA_PAPER")
    """A second, independent confirmation that this is a paper account, checked
    alongside `alpaca_environment`/`alpaca_endpoint` by AlpacaClient's construction-time
    guard (see broker/alpaca_client.py) before it will connect to anything. Required,
    no default -- an operator must say so explicitly, not have it assumed."""


@lru_cache
def get_settings() -> Settings:
    """Returns the process-wide `Settings` singleton, loading it on first call."""
    return Settings()
