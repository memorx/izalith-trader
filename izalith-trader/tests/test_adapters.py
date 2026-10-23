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
    """CryptoAdapter en simulación pedida (sin red)."""

    @pytest.fixture
    def adapter(self):
        return CryptoAdapter(dry_run=True, simulate=True)

    @pytest.mark.asyncio
    async def test_connect_simulated_is_not_real(self, adapter):
        result = await adapter.connect()
        # connect() sólo regresa True si quedó conectado a la fuente REAL
        assert result is False
        assert adapter.is_simulating is True
        assert adapter.source == "simulado"
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_simulated_data_is_tagged(self, adapter):
        await adapter.connect()
        price = await adapter.get_price("BTC/USDT")
        candles = await adapter.get_ohlcv("BTC/USDT", "1h", 50)
        assert price.simulated is True
        assert all(c.simulated for c in candles)

    @pytest.mark.asyncio
    async def test_simulated_history_is_stable(self, adapter):
        """Antes cada consulta inventaba 100 velas nuevas y el precio saltaba ±5%."""
        await adapter.connect()
        a = await adapter.get_ohlcv("BTC/USDT", "1h", 50)
        b = await adapter.get_ohlcv("BTC/USDT", "1h", 50)
        assert [c.close for c in a] == [c.close for c in b]

    @pytest.mark.asyncio
    async def test_get_prices(self, adapter):
        await adapter.connect()
        prices = await adapter.get_prices(["BTC/USDT", "ETH/USDT"])
        assert len(prices) == 2
        assert all(p.last > 0 for p in prices)

    @pytest.mark.asyncio
    async def test_get_ohlcv(self, adapter):
        await adapter.connect()
        ohlcv = await adapter.get_ohlcv("BTC/USDT", "1h", 50)
        assert len(ohlcv) == 50
        assert all(c.high >= c.low for c in ohlcv)

    @pytest.mark.asyncio
    async def test_place_market_order_dry_run(self, adapter):
        await adapter.connect()
        result = await adapter.place_market_order("BTC/USDT", "buy", 0.01)
        assert result.success is True
        assert result.side == "buy"
        assert "SIM" in result.order_id
        assert result.fee > 0

    @pytest.mark.asyncio
    async def test_cannot_sell_what_you_dont_have_on_spot(self, adapter):
        await adapter.connect()
        result = await adapter.place_market_order("BTC/USDT", "sell", 0.01)
        assert result.success is False
        assert "insuficiente" in result.error.lower()

    @pytest.mark.asyncio
    async def test_paper_balance_moves_with_orders(self, adapter):
        await adapter.connect()
        before = (await adapter.get_balance())["USDT"]
        await adapter.place_market_order("BTC/USDT", "buy", 0.01)
        after = (await adapter.get_balance())["USDT"]
        assert after < before

    @pytest.mark.asyncio
    async def test_no_silent_fallback_when_exchange_fails(self, monkeypatch):
        """Si Binance no contesta y la simulación está apagada: sin datos, no precios inventados."""
        import ccxt.async_support as ccxt_async

        class Down:
            def __init__(self, *a, **k):
                pass
            async def load_markets(self):
                raise ConnectionError("sin red")
            async def close(self):
                pass

        monkeypatch.setattr(ccxt_async, "binance", Down)
        adapter = CryptoAdapter(dry_run=True, allow_simulation=False)
        assert await adapter.connect() is False
        assert adapter.source == "sin conexión"
        assert await adapter.get_price("BTC/USDT") is None
        assert await adapter.get_ohlcv("BTC/USDT") == []

    def test_is_market_open(self, adapter):
        assert adapter.is_market_open() is True


class TestForexAdapter:

    @pytest.mark.asyncio
    async def test_without_keys_and_without_simulation_has_no_data(self):
        adapter = ForexAdapter(dry_run=True)
        assert await adapter.connect() is False
        assert adapter.source == "sin conexión"
        assert await adapter.get_price("EUR_USD") is None

    @pytest.mark.asyncio
    async def test_simulated_price(self):
        adapter = ForexAdapter(dry_run=True, simulate=True)
        await adapter.connect()
        price = await adapter.get_price("EUR_USD")
        assert price is not None and price.last > 0 and price.simulated

    @pytest.mark.asyncio
    async def test_forex_can_short_in_paper(self):
        adapter = ForexAdapter(dry_run=True, simulate=True)
        await adapter.connect()
        result = await adapter.place_market_order("EUR_USD", "sell", 1000)
        assert result.success is True

    def test_symbol_normalization(self):
        assert ForexAdapter._normalize_symbol("EUR/USD") == "EUR_USD"


class TestStocksAdapter:

    @pytest.mark.asyncio
    async def test_live_without_keys_has_no_data(self):
        # Sin llaves sólo hay precios de Yahoo, y únicamente para papel; con dinero real no hay nada
        adapter = StocksAdapter(dry_run=False)
        assert await adapter.connect() is False
        assert await adapter.get_price("VTI") is None

    @pytest.mark.asyncio
    async def test_simulated_price(self):
        adapter = StocksAdapter(dry_run=True, simulate=True)
        await adapter.connect()
        price = await adapter.get_price("VTI")
        assert price is not None and price.last > 0

    @pytest.mark.asyncio
    async def test_notional_order_in_paper(self):
        adapter = StocksAdapter(dry_run=True, simulate=True)
        await adapter.connect()
        result = await adapter.place_notional_order("VOO", "buy", 50)
        assert result.success is True
        assert result.filled_quantity * result.price == pytest.approx(50, rel=0.01)

    def test_fractional_precision(self):
        adapter = StocksAdapter(dry_run=True)
        assert adapter.amount_to_precision("VOO", 50 / 520) == pytest.approx(0.096153846, abs=1e-9)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
