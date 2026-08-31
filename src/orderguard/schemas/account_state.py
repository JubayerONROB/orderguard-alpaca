"""Snapshot of a broker account: positions, open orders, and recent activity.

`AccountState` is the read-only input to both the intent compiler (to resolve
"my energy names" into concrete symbols) and the rule engine (to evaluate
basket-level rules like buying power and concentration against real holdings).
It is a snapshot, never mutated in place -- every stage that needs an updated
view re-fetches or re-derives a new `AccountState`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ActivityType(str, Enum):
    """Kind of historical account activity relevant to rule evaluation."""

    FILL = "fill"
    DIVIDEND = "dividend"
    TRANSFER = "transfer"
    OTHER = "other"


class Position(BaseModel):
    """A single held position, as reported by the broker."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    qty: Decimal
    """Signed quantity: negative for a short position."""
    avg_entry_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    market_value: Decimal
    cost_basis: Decimal
    unrealized_pl: Decimal
    asset_class: str = Field(min_length=1)
    """Broker asset class, e.g. "us_equity"."""
    fractionable: bool
    shortable: bool
    tradable: bool
    opened_at: datetime
    """When this position (lot) was opened. Per-position, not per-lot -- OrderGuard does
    not model partial-lot cost basis. Used by rule R2 (pdt) to detect a same-day
    open-then-close that counts as a day trade."""

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class OpenOrder(BaseModel):
    """An existing unfilled order on the account, used by rule R4 (open_orders)."""

    model_config = ConfigDict(frozen=True)

    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str
    """Broker-reported side, e.g. "buy" / "sell"."""
    qty: Decimal | None = None
    notional: Decimal | None = None
    order_type: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    status: str
    submitted_at: datetime

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class Activity(BaseModel):
    """A historical account activity, e.g. a realized-loss fill used by rule R5 (wash_sale)."""

    model_config = ConfigDict(frozen=True)

    activity_id: str = Field(min_length=1)
    activity_type: ActivityType
    symbol: str = Field(min_length=1)
    qty: Decimal | None = None
    price: Decimal | None = None
    realized_pl: Decimal | None = None
    """Populated for closing fills; used to detect wash-sale-eligible losses."""
    transaction_time: datetime

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class AccountState(BaseModel):
    """Full point-in-time snapshot of a broker account."""

    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=1)
    as_of: datetime
    account_type: Literal["cash", "margin"]
    """Governs R1 (buying_power) settlement rules: a cash account's buying power
    excludes `unsettled_cash`; a margin account's does not."""
    equity: Decimal = Field(ge=0)
    cash: Decimal
    unsettled_cash: Decimal = Decimal(0)
    """Cash from a recent sale not yet settled (T+1). Already excluded from
    `buying_power` by the broker feed for a cash account; carried here so the rule
    engine's explanation can name it, not so the engine has to re-derive it."""
    buying_power: Decimal = Field(ge=0)
    pattern_day_trader: bool
    daytrade_count: int = Field(ge=0)
    positions: tuple[Position, ...] = ()
    open_orders: tuple[OpenOrder, ...] = ()
    recent_activity: tuple[Activity, ...] = ()
