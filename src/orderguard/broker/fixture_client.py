"""Replays frozen JSON fixtures as `BrokerClient` -- no network calls.

Used by `eval/run_eval.py` and `tests/` so the eval harness and test suite are fully
reproducible and offline. Fixtures live under `eval/fixtures/`.
"""

from __future__ import annotations

from pathlib import Path

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.order_plan import Order


class FixtureBrokerClient:
    """`BrokerClient` that reads account state from a frozen JSON fixture on disk."""

    def __init__(self, fixture_path: Path) -> None:
        self._fixture_path = fixture_path

    def get_account_state(self) -> AccountState:
        raise NotImplementedError

    def submit_order(self, order: Order) -> str:
        """Records the order as if submitted; never reaches a real broker.

        Raises:
            NotImplementedError: business logic not yet implemented.
        """
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError
