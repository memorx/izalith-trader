"""
Tests for market adapters.
"""
import pytest
import asyncio
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.adapters.base import PriceData, OHLCV, OrderResult, Position
from core.adapters.crypto import CryptoAdapter
from core.adapters.forex import ForexAdapter
from core.adapters.stocks import StocksAdapter


class TestPriceData:
    """Test PriceData dataclass."""

    def test_price_data_creation(self):
        """Test creating PriceData."""
        price = PriceData(
            symbol="BTC/USDT",
            market="crypto",
            timestamp=datetime.now(timezone.utc),
            bid=67000.0,
            ask=67010.0,
            last=67005.0
        )

        assert price.symbol == "BTC/USDT"
        assert price.market == "crypto"
        assert price.spread == 10.0  # ask - bid
        assert price.spread_pct > 0

    def test_price_data_to_dict(self):
        """Test PriceData serialization."""
        price = PriceData(
            symbol="BTC/USDT",
            market="crypto",
            timestamp=datetime.now(timezone.utc),
            bid=67000.0,
            ask=67010.0,
            last=67005.0
        )

        data = price.to_dict()
        assert 'symbol' in data
        assert 'market' in data
        assert 'timestamp' in data


class TestOHLCV:
    """Test OHLCV dataclass."""

    def test_ohlcv_creation(self):
        """Test creating OHLCV."""
        candle = OHLCV(
            timestamp=datetime.now(timezone.utc),
            open=67000.0,
            high=67500.0,
            low=66800.0,
            close=67200.0,
            volume=1000000.0
        )

        assert candle.high >= candle.low
        assert candle.high >= candle.open
        assert candle.high >= candle.close


class TestOrderResult:
    """Test OrderResult dataclass."""

    def test_successful_order(self):
        """Test successful order result."""
        result = OrderResult(
            success=True,
            order_id="12345",
            symbol="BTC/USDT",
            side="buy",
            quantity=0.1,
            price=67000.0,
            filled_quantity=0.1,
            status="filled"
        )

        assert result.success
        assert result.error is None

    def test_failed_order(self):
        """Test failed order result."""
        result = OrderResult(
            success=False,
            error="Insufficient balance"
        )

        assert not result.success
        assert result.error == "Insufficient balance"


class TestCryptoAdapter:
    """Test CryptoAdapter."""

    @pytest.fixture
    def adapter(self):
        """Create a test adapter."""
        return CryptoAdapter(dry_run=True)

    @pytest.mark.asyncio
    async def test_connect(self, adapter):
        """Test adapter connection."""
        result = await adapter.connect()
        assert result is True
        assert adapter.connected is True
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_simulation_mode(self, adapter):
        """Test adapter runs in simulation mode without API keys."""
        await adapter.connect()

        # Should be in simulation mode
        assert adapter.is_simulating is True

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_price(self, adapter):
        """Test getting price in simulation mode."""
        await adapter.connect()

        price = await adapter.get_price("BTC/USDT")
        assert price is not None
        assert price.symbol == "BTC/USDT"
        assert price.last > 0

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_prices(self, adapter):
        """Test getting multiple prices."""
        await adapter.connect()

        symbols = ["BTC/USDT", "ETH/USDT"]
        prices = await adapter.get_prices(symbols)

        assert len(prices) == 2
        assert all(p.last > 0 for p in prices)

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_ohlcv(self, adapter):
        """Test getting OHLCV data."""
        await adapter.connect()

        ohlcv = await adapter.get_ohlcv("BTC/USDT", "1h", 50)

        assert len(ohlcv) == 50
        assert all(c.high >= c.low for c in ohlcv)

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_place_market_order_dry_run(self, adapter):
        """Test placing order in dry run mode."""
        await adapter.connect()

        result = await adapter.place_market_order("BTC/USDT", "buy", 0.01)

        assert result.success is True
        assert result.symbol == "BTC/USDT"
        assert result.side == "buy"
        assert "SIM" in result.order_id

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_balance(self, adapter):
        """Test getting balance."""
        await adapter.connect()

        balance = await adapter.get_balance()

        assert "USDT" in balance
        assert balance["USDT"] > 0

        await adapter.disconnect()

    def test_is_market_open(self, adapter):
        """Test crypto market is always open."""
        assert adapter.is_market_open() is True


class TestForexAdapter:
    """Test ForexAdapter."""

    @pytest.fixture
    def adapter(self):
        """Create a test adapter."""
        return ForexAdapter(dry_run=True)

    @pytest.mark.asyncio
    async def test_connect(self, adapter):
        """Test adapter connection."""
        result = await adapter.connect()
        assert result is True
        assert adapter.connected is True
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_price(self, adapter):
        """Test getting price."""
        await adapter.connect()

        price = await adapter.get_price("EUR_USD")
        assert price is not None
        assert price.last > 0

        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_symbol_normalization(self, adapter):
        """Test symbol format conversion."""
        assert adapter._normalize_symbol("EUR/USD") == "EUR_USD"
        assert adapter._denormalize_symbol("EUR_USD") == "EUR/USD"


class TestStocksAdapter:
    """Test StocksAdapter."""

    @pytest.fixture
    def adapter(self):
        """Create a test adapter."""
        return StocksAdapter(dry_run=True)

    @pytest.mark.asyncio
    async def test_connect(self, adapter):
        """Test adapter connection."""
        result = await adapter.connect()
        assert result is True
        assert adapter.connected is True
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_price(self, adapter):
        """Test getting price."""
        await adapter.connect()

        price = await adapter.get_price("VTI")
        assert price is not None
        assert price.last > 0

        await adapter.disconnect()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
