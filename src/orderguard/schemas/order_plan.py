"""The proposed basket of orders: the intent compiler's output and the rule engine's input.

An `OrderPlan` is what the trader ultimately approves (or rejects). It is deliberately
broker-agnostic -- it does not know about Alpaca's request shape -- so that `broker/`
clients are the only place that translates it into a wire format.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class Order(BaseModel):
    """A single proposed order within a basket.

    Exactly one of `qty` or `notional` must be set, matching Alpaca's own constraint --
    an order is sized either in shares or in dollars, never both.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    qty: Decimal | None = Field(default=None, gt=0)
    notional: Decimal | None = Field(default=None, gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    extended_hours: bool = False

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _validate_sizing_and_prices(self) -> Order:
        if (self.qty is None) == (self.notional is None):
            raise ValueError("exactly one of `qty` or `notional` must be set")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type} order requires `limit_price`")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError(f"{self.order_type} order requires `stop_price`")
        return self


class OrderPlan(BaseModel):
    """A basket of proposed orders, produced by the intent compiler from one instruction."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    account_id: str = Field(min_length=1)
    source_instruction: str = Field(min_length=1)
    """The trader's original plain-English instruction, kept for the audit trail."""
    orders: tuple[Order, ...] = ()
    """May be empty: a legitimate outcome when the instruction requires no action, or
    for a trivial baseline that never proposes orders."""
    cancellations: tuple[str, ...] = ()
    """Broker order ids of existing open orders this basket cancels, e.g. to resolve
    an R4 (open_orders) stacking conflict via repair."""
