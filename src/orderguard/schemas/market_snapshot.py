"""Point-in-time market data: quotes, tradable-asset metadata, and the session clock.

`MarketSnapshot` is the second input to the intent compiler (alongside `AccountState`)
and is what rule R6 (session) and R7 (asset_eligibility) check baskets against. Like
`AccountState` it is a frozen snapshot, not a live feed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Quote(BaseModel):
    """Last-known pricing for one symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    last_price: Decimal = Field(gt=0)
    bid: Decimal | None = Field(default=None, gt=0)
    ask: Decimal | None = Field(default=None, gt=0)
    as_of: datetime

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class AssetMeta(BaseModel):
    """Broker-reported tradability metadata for one symbol, independent of whether it's held.

    Needed for symbols a basket would newly buy into (e.g. AMD in a rotation instruction)
    where no `Position` exists yet to carry these flags.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    tradable: bool
    fractionable: bool
    shortable: bool
    asset_class: str = Field(min_length=1)

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        return v.upper()


class MarketClock(BaseModel):
    """Current market session state, used by rule R6 (session)."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    is_open: bool
    next_open: datetime
    next_close: datetime


class MarketSnapshot(BaseModel):
    """Full point-in-time market data snapshot backing one compile/validate run."""

    model_config = ConfigDict(frozen=True)

    as_of: datetime
    quotes: tuple[Quote, ...] = ()
    assets: tuple[AssetMeta, ...] = ()
    clock: MarketClock
