"""Deterministic, indicator-based market regime classification. Zero LLM calls -- this
module only ever computes fixed formulas over real historical bars.

Bar-fetching (`fetch_daily_bars`) is isolated from the indicator math (`compute_regime`)
specifically so tests can feed synthetic bar sequences into `compute_regime` without any
network access; only `get_market_regimes` (the orchestrator) touches Alpaca.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from orderguard.research.schemas import MarketRegime

DEFAULT_WATCHLIST: tuple[str, ...] = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "AMZN")
LOOKBACK_DAYS = 252
"""~1 trading year of daily bars, fetched per symbol -- enough for a 70/30 chronological
split (backtest_engine.py) to leave a meaningful out-of-sample window."""

SMA_WINDOW = 20
TREND_BUFFER_PCT = Decimal("1.0")
"""Price must be at least this many percent above/below the SMA to count as a trend,
rather than NEUTRAL -- avoids flapping between BULLISH/BEARISH on noise right at the SMA."""

VOL_LOW_THRESHOLD = Decimal("0.20")
VOL_HIGH_THRESHOLD = Decimal("0.40")
"""Annualized realized volatility buckets: <20% LOW, 20-40% NORMAL, >40% HIGH. Typical
large-cap equity vol ranges, not fitted to any particular symbol."""

VOLUME_LOW_MULTIPLE = Decimal("0.8")
VOLUME_HIGH_MULTIPLE = Decimal("1.2")

TRADING_DAYS_PER_YEAR = 252


class Bar(Protocol):
    """The minimal shape `compute_regime` needs from a bar -- duck-typed so tests can
    pass plain objects (or alpaca-py's real `Bar` model) without a live client."""

    close: float | Decimal
    volume: int
    timestamp: datetime


class InsufficientBarsError(Exception):
    """Raised when fewer than `SMA_WINDOW + 1` bars are available for a symbol --
    not enough history to compute a trailing-20-day indicator at all."""

    def __init__(self, symbol: str, bars_available: int) -> None:
        self.symbol = symbol
        self.bars_available = bars_available
        super().__init__(f"{symbol}: only {bars_available} bars available, need at least {SMA_WINDOW + 1}")


def fetch_daily_bars(
    data_client: StockHistoricalDataClient, symbol: str, lookback_days: int = LOOKBACK_DAYS
) -> list[Bar]:
    """Fetches up to `lookback_days` of daily bars for `symbol`, oldest first."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_days * 1.6))  # calendar days, to cover weekends/holidays
    request = StockBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Day, start=start, end=end)
    bar_set = data_client.get_stock_bars(request)
    bars = list(bar_set[symbol])
    return bars[-lookback_days:] if len(bars) > lookback_days else bars


def compute_regime(symbol: str, bars: Sequence[Bar], as_of: datetime | None = None) -> MarketRegime:
    """Computes a `MarketRegime` from a chronologically-ordered (oldest first) bar
    sequence. Pure function -- no I/O, no `datetime.now()` unless `as_of` is omitted.

    Raises:
        InsufficientBarsError: fewer than `SMA_WINDOW + 1` bars given.
    """
    if len(bars) < SMA_WINDOW + 1:
        raise InsufficientBarsError(symbol=symbol, bars_available=len(bars))

    closes = [Decimal(str(b.close)) for b in bars]
    volumes = [int(b.volume) for b in bars]

    current_price = closes[-1]
    current_volume = volumes[-1]
    window_closes = closes[-(SMA_WINDOW + 1) : -1]  # the 20 sessions BEFORE today
    window_volumes = volumes[-(SMA_WINDOW + 1) : -1]

    sma_20 = sum(window_closes) / SMA_WINDOW
    avg_volume_20d = sum(window_volumes) // SMA_WINDOW

    daily_returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(len(closes) - SMA_WINDOW, len(closes)) if i > 0
    ]
    daily_returns_float = [float(r) for r in daily_returns]
    daily_stdev = Decimal(str(statistics.pstdev(daily_returns_float))) if len(daily_returns_float) >= 2 else Decimal(0)
    realized_vol_20d = daily_stdev * Decimal(TRADING_DAYS_PER_YEAR).sqrt()

    trend_upper = sma_20 * (1 + TREND_BUFFER_PCT / 100)
    trend_lower = sma_20 * (1 - TREND_BUFFER_PCT / 100)
    if current_price > trend_upper:
        trend = "BULLISH"
    elif current_price < trend_lower:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    if realized_vol_20d < VOL_LOW_THRESHOLD:
        volatility_regime = "LOW"
    elif realized_vol_20d > VOL_HIGH_THRESHOLD:
        volatility_regime = "HIGH"
    else:
        volatility_regime = "NORMAL"

    if avg_volume_20d == 0:
        volume_state = "AVERAGE"
    else:
        ratio = Decimal(current_volume) / Decimal(avg_volume_20d)
        if ratio < VOLUME_LOW_MULTIPLE:
            volume_state = "BELOW_AVERAGE"
        elif ratio > VOLUME_HIGH_MULTIPLE:
            volume_state = "ABOVE_AVERAGE"
        else:
            volume_state = "AVERAGE"

    return MarketRegime(
        symbol=symbol,
        trend=trend,
        volatility_regime=volatility_regime,
        volume_state=volume_state,
        current_price=current_price,
        sma_20=sma_20,
        realized_vol_20d=realized_vol_20d,
        avg_volume_20d=avg_volume_20d,
        current_volume=current_volume,
        as_of=as_of if as_of is not None else bars[-1].timestamp,
    )


def get_market_regimes(
    data_client: StockHistoricalDataClient, symbols: Sequence[str] = DEFAULT_WATCHLIST
) -> tuple[MarketRegime, ...]:
    """Fetches bars and computes a regime for every symbol in `symbols`. A symbol with
    insufficient history is skipped (not fabricated) -- callers see fewer regimes than
    symbols requested rather than a fake one."""
    regimes = []
    for symbol in symbols:
        bars = fetch_daily_bars(data_client, symbol)
        try:
            regimes.append(compute_regime(symbol, bars))
        except InsufficientBarsError:
            continue
    return tuple(regimes)
