"""Live Alpaca paper-trading implementation of `BrokerClient`.

Reads `alpaca_endpoint` / `alpaca_api_key` / `alpaca_secret_key` from `Settings`
(`config.py`). Never used by `eval/` or `tests/` -- those use `fixture_client.py`
so that no test run makes a network call.

Alpaca's `Position` model has no "opened_at" field (OrderGuard's `Position.opened_at`
needs one for R2's same-day-close check), so it's approximated per symbol from the
most recently filled BUY order's `filled_at` -- exact for a single-lot position,
approximate for one built from multiple buys on different days (the approximation
biases toward the most recent purchase, i.e. toward flagging a day trade, which is
the safer direction to be wrong in for a risk gate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
)

from orderguard.config import Settings
from orderguard.schemas.account_state import AccountState, Activity, OpenOrder, Position
from orderguard.schemas.order_plan import Order, OrderType


class PaperTradingGuardError(Exception):
    """Raised when AlpacaClient cannot confirm every signal agrees this is a paper
    account. This is the only thing standing between a bug and a real order reaching
    a real account -- it refuses to construct rather than falling through to
    "probably fine" on a missing or disagreeing signal."""


def _assert_paper_trading(settings: Settings) -> None:
    """Requires THREE independent signals to all confirm paper trading before
    AlpacaClient is allowed to exist: the explicit ALPACA_PAPER flag, the endpoint URL
    shape, and ALPACA_ENVIRONMENT itself. Any one being wrong, or missing, raises --
    there is no majority vote and no "close enough"."""
    if not settings.alpaca_paper:
        raise PaperTradingGuardError(
            "ALPACA_PAPER is not true -- refusing to construct AlpacaClient. Set "
            "ALPACA_PAPER=true in .env if this really is a paper account."
        )
    if "paper-api" not in settings.alpaca_endpoint:
        raise PaperTradingGuardError(
            f"ALPACA_ENDPOINT does not look like a paper-trading endpoint (got "
            f"{settings.alpaca_endpoint!r}, expected it to contain 'paper-api'). "
            f"Refusing to construct AlpacaClient."
        )
    if settings.alpaca_environment != "paper":
        raise PaperTradingGuardError(
            f"ALPACA_ENVIRONMENT is {settings.alpaca_environment!r}, not 'paper'. "
            f"Refusing to construct AlpacaClient."
        )


class AlpacaClient:
    """`BrokerClient` backed by the live Alpaca paper-trading API.

    Construction always goes through `_assert_paper_trading` first -- see there for
    what's checked. `paper=True` below is hardcoded, not re-derived from settings,
    precisely so a bad `alpaca_environment` value can't independently flip it after
    the guard has already run.
    """

    def __init__(self, settings: Settings) -> None:
        _assert_paper_trading(settings)
        self._settings = settings
        self._client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=True,
        )

    def _opened_at(self, symbol: str) -> datetime:
        request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            symbols=[symbol],
            side=AlpacaOrderSide.BUY,
            direction="desc",
            limit=1,
        )
        orders = self._client.get_orders(filter=request)
        if orders and orders[0].filled_at is not None:
            return orders[0].filled_at
        return datetime.now(timezone.utc)  # no buy history found; treat as opened now (safest default for R2)

    def get_account_state(self) -> AccountState:
        account = self._client.get_account()
        positions = self._client.get_all_positions()
        open_orders = self._client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))

        return AccountState(
            account_id=str(account.id),
            as_of=datetime.now(timezone.utc),
            account_type="cash" if Decimal(str(account.multiplier)) <= 1 else "margin",
            equity=Decimal(str(account.equity)),
            cash=Decimal(str(account.cash)),
            unsettled_cash=Decimal(0),  # not directly exposed by Alpaca's account model
            buying_power=Decimal(str(account.buying_power)),
            pattern_day_trader=bool(account.pattern_day_trader),
            daytrade_count=int(account.daytrade_count) if account.daytrade_count is not None else 0,
            # Alpaca returns None (not 0) for an account with no day-trade history yet,
            # e.g. a freshly created paper account -- 0 is the correct read of that.
            positions=tuple(
                Position(
                    symbol=p.symbol,
                    qty=Decimal(str(p.qty)),
                    avg_entry_price=Decimal(str(p.avg_entry_price)),
                    current_price=Decimal(str(p.current_price)),
                    market_value=Decimal(str(p.market_value)),
                    cost_basis=Decimal(str(p.cost_basis)),
                    unrealized_pl=Decimal(str(p.unrealized_pl)),
                    asset_class=str(p.asset_class),
                    fractionable=True,  # Position doesn't expose this; assets endpoint would, out of scope here
                    shortable=True,
                    tradable=True,
                    opened_at=self._opened_at(p.symbol),
                )
                for p in positions
            ),
            open_orders=tuple(
                OpenOrder(
                    order_id=str(o.id),
                    symbol=o.symbol,
                    side=o.side.value,
                    qty=Decimal(str(o.qty)) if o.qty is not None else None,
                    notional=Decimal(str(o.notional)) if o.notional is not None else None,
                    order_type=o.order_type.value,
                    limit_price=Decimal(str(o.limit_price)) if o.limit_price is not None else None,
                    stop_price=Decimal(str(o.stop_price)) if o.stop_price is not None else None,
                    status=o.status.value,
                    submitted_at=o.submitted_at,
                )
                for o in open_orders
            ),
            recent_activity=self._recent_activity(),
        )

    def _recent_activity(self) -> tuple[Activity, ...]:
        request = GetOrdersRequest(status=QueryOrderStatus.CLOSED, side=AlpacaOrderSide.SELL, limit=50)
        closed_sells = self._client.get_orders(filter=request)
        return tuple(
            Activity(
                activity_id=str(o.id),
                activity_type="fill",
                symbol=o.symbol,
                qty=Decimal(str(o.filled_qty)) if o.filled_qty is not None else None,
                price=Decimal(str(o.filled_avg_price)) if o.filled_avg_price is not None else None,
                realized_pl=None,  # Alpaca's order model doesn't carry realized P&L directly
                transaction_time=o.filled_at,
            )
            for o in closed_sells
            if o.filled_at is not None
        )

    def submit_order(self, order: Order) -> str:
        side = AlpacaOrderSide.BUY if order.side == "buy" else AlpacaOrderSide.SELL
        tif = AlpacaTimeInForce(order.time_in_force.value)
        # Alpaca rejects a market order flagged extended_hours=True outright ("extended
        # hours order must be DAY or GTC limit orders") -- only a limit order can
        # actually run in the extended session. R6 (session.py) only checks is_open vs.
        # extended_hours, not this order_type/extended_hours combination, so a plan
        # that reaches here with an inconsistent pairing is forced to the safe default
        # (no extended-hours execution) rather than sent to the network to fail there.
        extended_hours = order.extended_hours and order.order_type == OrderType.LIMIT
        common = {
            "symbol": order.symbol,
            "side": side,
            "time_in_force": tif,
            "extended_hours": extended_hours,
        }
        if order.qty is not None:
            common["qty"] = float(order.qty)
        else:
            common["notional"] = float(order.notional)

        if order.order_type == OrderType.LIMIT:
            request = LimitOrderRequest(limit_price=float(order.limit_price), **common)
        else:
            request = MarketOrderRequest(**common)

        result = self._client.submit_order(order_data=request)
        return str(result.id)

    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)
