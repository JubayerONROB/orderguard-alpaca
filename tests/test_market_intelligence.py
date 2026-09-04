"""Unit tests for market_intelligence.py against synthetic bar data -- no network,
no Alpaca client involved. Only `compute_regime` (the pure indicator math) is tested
here; `fetch_daily_bars`/`get_market_regimes` are thin orchestration over a real client.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from orderguard.research.market_intelligence import (
    SMA_WINDOW,
    InsufficientBarsError,
    compute_regime,
)

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _bars(closes: list[float], volumes: list[int] | None = None) -> list[SimpleNamespace]:
    volumes = volumes if volumes is not None else [1_000_000] * len(closes)
    return [
        SimpleNamespace(close=c, volume=v, timestamp=NOW - timedelta(days=len(closes) - i))
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def test_insufficient_bars_raises() -> None:
    with pytest.raises(InsufficientBarsError):
        compute_regime("AAPL", _bars([100.0] * SMA_WINDOW))  # need SMA_WINDOW + 1


def test_rising_prices_are_bullish() -> None:
    closes = [100.0 + i for i in range(SMA_WINDOW + 1)]  # steadily rising, today is the peak
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.trend == "BULLISH"


def test_falling_prices_are_bearish() -> None:
    closes = [100.0 - i for i in range(SMA_WINDOW + 1)]  # steadily falling, today is the trough
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.trend == "BEARISH"


def test_flat_prices_are_neutral() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.trend == "NEUTRAL"
    assert regime.realized_vol_20d == 0


def test_flat_prices_are_low_volatility() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.volatility_regime == "LOW"


def test_alternating_large_swings_are_high_volatility() -> None:
    closes = [100.0 if i % 2 == 0 else 130.0 for i in range(SMA_WINDOW + 1)]
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.volatility_regime == "HIGH"


def test_above_average_volume_detected() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    volumes = [1_000_000] * SMA_WINDOW + [5_000_000]  # today's volume spikes
    regime = compute_regime("AAPL", _bars(closes, volumes))
    assert regime.volume_state == "ABOVE_AVERAGE"


def test_below_average_volume_detected() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    volumes = [1_000_000] * SMA_WINDOW + [200_000]  # today's volume is thin
    regime = compute_regime("AAPL", _bars(closes, volumes))
    assert regime.volume_state == "BELOW_AVERAGE"


def test_average_volume_detected() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    regime = compute_regime("AAPL", _bars(closes))  # uniform 1,000,000 throughout
    assert regime.volume_state == "AVERAGE"


def test_symbol_is_uppercased() -> None:
    closes = [100.0] * (SMA_WINDOW + 1)
    regime = compute_regime("aapl", _bars(closes))
    assert regime.symbol == "AAPL"


def test_sma_20_computed_from_the_20_sessions_before_today() -> None:
    closes = [100.0] * SMA_WINDOW + [110.0]  # 20 flat sessions at 100, then today at 110
    regime = compute_regime("AAPL", _bars(closes))
    assert regime.sma_20 == 100
    assert regime.current_price == 110
