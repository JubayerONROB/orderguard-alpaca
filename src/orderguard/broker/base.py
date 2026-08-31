"""The `BrokerClient` protocol both `alpaca_client.py` and `fixture_client.py` implement.

Keeping this surface narrow is what lets `fixture_client.py` stand in for a real broker
in `eval/` and `tests/` with zero network calls, while `alpaca_client.py` is the only
module that ever talks to Alpaca.
"""

from __future__ import annotations

from typing import Protocol

from orderguard.schemas.account_state import AccountState
from orderguard.schemas.order_plan import Order


class BrokerClient(Protocol):
    """Read access to account state plus (human-approved) order submission."""

    def get_account_state(self) -> AccountState:
        """Fetches the current account snapshot: equity, positions, open orders, activity."""
        ...

    def submit_order(self, order: Order) -> str:
        """Submits one already-approved order to the broker. Returns the broker order id.

        Callers must only invoke this after explicit human approval of the containing
        `OrderPlan` -- this protocol does not enforce that; the caller does.
        """
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancels one existing open order by broker order id."""
        ...
