"""Unit tests for AlpacaClient against a mocked alpaca-py TradingClient.

No network access and no real credentials required: `TradingClient` itself is patched
out entirely, so nothing here ever reaches Alpaca.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import QueryOrderStatus

from orderguard.broker.alpaca_client import AlpacaClient, PaperTradingGuardError

NOW = datetime(2026, 8, 27, 14, 30, tzinfo=timezone.utc)


def _settings(**overrides) -> SimpleNamespace:
    """A duck-typed stand-in for `Settings` -- only the attributes AlpacaClient
    actually reads, no real pydantic validation or .env involved."""
    defaults = dict(
        alpaca_api_key="test-key-not-real",
        alpaca_secret_key="test-secret-not-real",
        alpaca_environment="paper",
        alpaca_endpoint="https://paper-api.alpaca.markets/v2",
        alpaca_paper=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_account(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="ab96xxxx-xxxx-xxxx-xxxx-xxxxxxxxf0d5",
        multiplier="4",
        equity="100000",
        cash="100000",
        buying_power="400000",
        pattern_day_trader=False,
        daytrade_count=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_position(**overrides) -> SimpleNamespace:
    defaults = dict(
        symbol="AAPL",
        qty="31.26",
        avg_entry_price="319.92",
        current_price="319.92",
        market_value="10000.00",
        cost_basis="10000.00",
        unrealized_pl="0.00",
        asset_class="us_equity",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_open_order(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="5cec26de-26fa-4d1c-a1d3-73d9ce982631",
        symbol="AAPL",
        side=SimpleNamespace(value="buy"),
        qty="5",
        notional=None,
        order_type=SimpleNamespace(value="limit"),
        limit_price="255.00",
        stop_price=None,
        status=SimpleNamespace(value="accepted"),
        submitted_at=NOW,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_closed_buy(**overrides) -> SimpleNamespace:
    defaults = dict(filled_at=NOW)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_closed_sell(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="sell-order-1",
        symbol="TSM",
        filled_qty="10",
        filled_avg_price="200.00",
        filled_at=NOW,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _patched_trading_client() -> MagicMock:
    """Builds a MagicMock standing in for `TradingClient`, wired so `get_orders`
    returns different fixtures depending on the filter passed -- mirroring the three
    distinct queries AlpacaClient.get_account_state() actually makes (open orders,
    per-symbol closed buys for opened_at, closed sells for recent activity)."""
    mock_client = MagicMock()
    mock_client.get_account.return_value = _mock_account()
    mock_client.get_all_positions.return_value = [_mock_position()]

    def get_orders_side_effect(filter):
        if filter.status == QueryOrderStatus.OPEN:
            return [_mock_open_order()]
        if filter.status == QueryOrderStatus.CLOSED and filter.side == AlpacaOrderSide.BUY:
            return [_mock_closed_buy()]
        if filter.status == QueryOrderStatus.CLOSED and filter.side == AlpacaOrderSide.SELL:
            return [_mock_closed_sell()]
        return []

    mock_client.get_orders.side_effect = get_orders_side_effect
    return mock_client


# Paper-trading guard ----------------------------------------------------------------


def test_paper_mode_constructs() -> None:
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_cls.return_value = _patched_trading_client()
        client = AlpacaClient(_settings())
        assert client is not None


def test_alpaca_paper_false_refuses_to_construct() -> None:
    with pytest.raises(PaperTradingGuardError, match="ALPACA_PAPER"):
        AlpacaClient(_settings(alpaca_paper=False))


def test_non_paper_endpoint_refuses_to_construct() -> None:
    with pytest.raises(PaperTradingGuardError, match="ALPACA_ENDPOINT"):
        AlpacaClient(_settings(alpaca_endpoint="https://api.alpaca.markets/v2"))


def test_non_paper_environment_refuses_to_construct() -> None:
    with pytest.raises(PaperTradingGuardError, match="ALPACA_ENVIRONMENT"):
        AlpacaClient(_settings(alpaca_environment="live"))


def test_guard_runs_before_trading_client_is_ever_constructed() -> None:
    """The guard must reject BEFORE touching the network layer at all -- confirmed by
    asserting TradingClient itself was never instantiated on a guard failure."""
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        with pytest.raises(PaperTradingGuardError):
            AlpacaClient(_settings(alpaca_paper=False))
        mock_cls.assert_not_called()


# Account / position / open-order conversion --------------------------------------


def test_get_account_state_converts_account_fields() -> None:
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_cls.return_value = _patched_trading_client()
        client = AlpacaClient(_settings())
        account = client.get_account_state()

    assert account.account_type == "margin"  # multiplier "4" > 1
    assert account.equity == Decimal(100000)
    assert account.cash == Decimal(100000)
    assert account.buying_power == Decimal(400000)
    assert account.pattern_day_trader is False
    assert account.daytrade_count == 0


def test_get_account_state_converts_positions() -> None:
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_cls.return_value = _patched_trading_client()
        client = AlpacaClient(_settings())
        account = client.get_account_state()

    assert len(account.positions) == 1
    position = account.positions[0]
    assert position.symbol == "AAPL"
    assert position.qty == Decimal("31.26")
    assert position.current_price == Decimal("319.92")
    assert position.opened_at == NOW  # from the mocked closed-buy lookup


def test_get_account_state_converts_open_orders() -> None:
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_cls.return_value = _patched_trading_client()
        client = AlpacaClient(_settings())
        account = client.get_account_state()

    assert len(account.open_orders) == 1
    open_order = account.open_orders[0]
    assert open_order.symbol == "AAPL"
    assert open_order.side == "buy"
    assert open_order.qty == Decimal(5)
    assert open_order.limit_price == Decimal("255.00")
    assert open_order.order_id == "5cec26de-26fa-4d1c-a1d3-73d9ce982631"


def test_daytrade_count_none_reads_as_zero() -> None:
    """Alpaca returns None (not 0) for an account with no day-trade history yet."""
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_client = _patched_trading_client()
        mock_client.get_account.return_value = _mock_account(daytrade_count=None)
        mock_cls.return_value = mock_client
        client = AlpacaClient(_settings())
        account = client.get_account_state()

    assert account.daytrade_count == 0


def test_cash_account_type_from_multiplier_one() -> None:
    with patch("orderguard.broker.alpaca_client.TradingClient") as mock_cls:
        mock_client = _patched_trading_client()
        mock_client.get_account.return_value = _mock_account(multiplier="1")
        mock_cls.return_value = mock_client
        client = AlpacaClient(_settings())
        account = client.get_account_state()

    assert account.account_type == "cash"
