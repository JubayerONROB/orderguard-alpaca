"""Places the two demo seed orders for the live segment (see docs/DEMO_RUNBOOK.md):

1. A starter AAPL position (market buy, notional) -- enables R3_CONCENTRATION once it
   fills, which only happens once the market is open.
2. A standing AAPL limit order priced well below the last trade, GTC -- enables
   R4_OPEN_ORDERS immediately, regardless of market hours.

Run `scripts/reset_paper.py` first if the account already has state from a prior
rehearsal. Refuses to run against anything but a paper account.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

from orderguard.broker.alpaca_client import AlpacaClient
from orderguard.config import get_settings
from orderguard.schemas.order_plan import Order

SEED_SYMBOL = "AAPL"
STARTER_POSITION_NOTIONAL = Decimal("10000.00")
STANDING_ORDER_QTY = Decimal(5)
STANDING_ORDER_DISCOUNT = Decimal("0.20")  # limit price set 20% below the last trade


def main() -> None:
    settings = get_settings()
    if settings.alpaca_environment != "paper":
        print(
            f"refusing to run: ALPACA_ENVIRONMENT is {settings.alpaca_environment!r}, not 'paper'",
            file=sys.stderr,
        )
        sys.exit(1)

    client = AlpacaClient(settings)

    data_client = StockHistoricalDataClient(api_key=settings.alpaca_api_key, secret_key=settings.alpaca_secret_key)
    trade = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=[SEED_SYMBOL]))
    last_price = Decimal(str(trade[SEED_SYMBOL].price))
    limit_price = (last_price * (1 - STANDING_ORDER_DISCOUNT)).quantize(Decimal("0.01"))
    print(f"{SEED_SYMBOL} last trade: {last_price}")

    starter = Order(
        symbol=SEED_SYMBOL,
        side="buy",
        order_type="market",
        notional=STARTER_POSITION_NOTIONAL,
        time_in_force="day",
    )
    starter_id = client.submit_order(starter)
    print(f"Starter position order submitted (market buy ${STARTER_POSITION_NOTIONAL} {SEED_SYMBOL}): {starter_id}")

    standing = Order(
        symbol=SEED_SYMBOL,
        side="buy",
        order_type="limit",
        qty=STANDING_ORDER_QTY,
        limit_price=limit_price,
        time_in_force="gtc",
    )
    standing_id = client.submit_order(standing)
    print(f"Standing order submitted (limit buy {STANDING_ORDER_QTY} {SEED_SYMBOL} @ ${limit_price} GTC): {standing_id}")

    print("Done. The starter position only fills once the market is open -- R4_OPEN_ORDERS")
    print("is live immediately; R3_CONCENTRATION needs the starter position filled first.")


if __name__ == "__main__":
    main()
