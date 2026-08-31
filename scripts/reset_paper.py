"""Cancels every open order and closes every position on the paper account.

Run this between demo rehearsals to return to a clean baseline before re-seeding with
`scripts/seed_demo.py`. Refuses to run against anything but a paper account (reads
ALPACA_ENVIRONMENT from .env) -- this script is destructive by design and must never
touch a live account.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orderguard.broker.alpaca_client import AlpacaClient
from orderguard.config import get_settings


def main() -> None:
    settings = get_settings()
    if settings.alpaca_environment != "paper":
        print(
            f"refusing to run: ALPACA_ENVIRONMENT is {settings.alpaca_environment!r}, not 'paper'",
            file=sys.stderr,
        )
        sys.exit(1)

    client = AlpacaClient(settings)

    print("Cancelling all open orders...")
    client._client.cancel_orders()

    print("Closing all positions...")
    client._client.close_all_positions(cancel_orders=True)

    account = client.get_account_state()
    print(f"Done. positions={len(account.positions)} open_orders={len(account.open_orders)} equity={account.equity}")


if __name__ == "__main__":
    main()
