"""Pure helper functions shared across rule modules: price/position lookups and order
valuation. No I/O, no clock reads -- every value comes from the arguments passed in.
"""

from __future__ import annotations

from decimal import Decimal

from orderguard.schemas.account_state import AccountState, Position
from orderguard.schemas.market_snapshot import MarketSnapshot
from orderguard.schemas.order_plan import Order, OrderSide


class MissingQuoteError(Exception):
    """Raised when a rule needs a price for a symbol the MarketSnapshot doesn't quote."""


def get_price(market: MarketSnapshot, symbol: str) -> Decimal:
    """Returns the last-known price for `symbol`.

    Raises:
        MissingQuoteError: no quote for `symbol` in `market.quotes`.
    """
    for quote in market.quotes:
        if quote.symbol == symbol:
            return quote.last_price
    raise MissingQuoteError(f"no quote for {symbol} in market snapshot")


def get_position(state: AccountState, symbol: str) -> Position | None:
    """Returns the held position for `symbol`, or None if not held."""
    for position in state.positions:
        if position.symbol == symbol:
            return position
    return None


def order_reference_price(order: Order, market: MarketSnapshot) -> Decimal:
    """The price to value `order` at: its limit price if set, else the last quote."""
    if order.limit_price is not None:
        return order.limit_price
    return get_price(market, order.symbol)


def order_notional_value(order: Order, market: MarketSnapshot) -> Decimal:
    """The dollar value of `order`: `notional` directly, or `qty * reference price`."""
    if order.notional is not None:
        return order.notional
    price = order_reference_price(order, market)
    return order.qty * price  # type: ignore[operator]  # qty is guaranteed set when notional isn't


def floor_shares(dollar_amount: Decimal, price: Decimal) -> Decimal:
    """The largest whole-share quantity costing no more than `dollar_amount` at `price`.

    Share counts round DOWN, never up -- overshooting a cap by rounding up would
    defeat the purpose of the cap.
    """
    if dollar_amount <= 0 or price <= 0:
        return Decimal(0)
    return (dollar_amount / price).to_integral_value(rounding="ROUND_FLOOR")


def is_buy(order: Order) -> bool:
    return order.side == OrderSide.BUY


def is_sell(order: Order) -> bool:
    return order.side == OrderSide.SELL
