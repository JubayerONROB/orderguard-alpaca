"""Loads frozen fixture pairs (`account.json` + `market.json`) into validated Pydantic objects.

A fixture is a directory `eval/fixtures/<name>/` containing exactly those two files.
Loading never touches the network -- it is pure disk I/O plus Pydantic validation -- and
raises loudly (with the offending file path) on any schema mismatch, rather than
returning a partially-valid object.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.market_snapshot import MarketSnapshot

FIXTURES_DIR = Path(__file__).parent


class FixtureLoadError(Exception):
    """Raised when a fixture directory is missing a file or fails schema validation."""


class Fixture(BaseModel):
    """One frozen (account, market) snapshot pair, keyed by fixture name."""

    model_config = ConfigDict(frozen=True)

    name: str
    account: AccountState
    market: MarketSnapshot


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FixtureLoadError(f"fixture file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FixtureLoadError(f"invalid JSON in fixture file {path}: {e}") from e


def load_fixture(name: str, fixtures_dir: Path = FIXTURES_DIR) -> Fixture:
    """Loads and validates the `<name>` fixture from `fixtures_dir`.

    Args:
        name: fixture directory name, e.g. "retail_rotation".
        fixtures_dir: parent directory containing fixture subdirectories. Defaults to
            `eval/fixtures/` (this package's own directory).

    Raises:
        FixtureLoadError: a fixture file is missing, not valid JSON, or fails schema
            validation. The original `pydantic.ValidationError` (if any) is chained.
    """
    fixture_dir = fixtures_dir / name
    account_payload = _read_json(fixture_dir / "account.json")
    market_payload = _read_json(fixture_dir / "market.json")

    try:
        account = AccountState.model_validate(account_payload)
    except ValidationError as e:
        raise FixtureLoadError(f"invalid AccountState in {fixture_dir / 'account.json'}: {e}") from e

    try:
        market = MarketSnapshot.model_validate(market_payload)
    except ValidationError as e:
        raise FixtureLoadError(f"invalid MarketSnapshot in {fixture_dir / 'market.json'}: {e}") from e

    return Fixture(name=name, account=account, market=market)
