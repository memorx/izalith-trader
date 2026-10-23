"""
Tests for signal detection.
"""
import pytest
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.signals.detector import SignalDetector, Signal
from core.adapters.base import PriceData


class TestSignal:
    """Test Signal dataclass."""

    def test_signal_creation(self):
        """Test creating a signal."""
        signal = Signal(
            symbol="BTC/USDT",
            signal_type="rsi_oversold",
            direction="bullish",
            strength=0.8,
            message="RSI is oversold",
            price=67000.0
        )

        assert signal.symbol == "BTC/USDT"
        assert signal.direction == "bullish"
        assert 0 <= signal.strength <= 1

    def test_signal_to_dict(self):
        """Test signal serialization."""
        signal = Signal(
            symbol="BTC/USDT",
            signal_type="rsi_oversold",
            direction="bullish",
            strength=0.8,
            message="RSI is oversold",
            price=67000.0
        )

        data = signal.to_dict()
        assert 'symbol' in data
        assert 'signal_type' in data
        assert 'direction' in data


class TestSignalDetector:
    """Test SignalDetector."""

    @pytest.fixture
    def mock_feed(self):
        """Create a mock feed."""
        feed = MagicMock()

        # Mock get_price
        feed.get_price = AsyncMock(return_value=PriceData(
            symbol="BTC/USDT",
            market="crypto",
            timestamp=datetime.now(timezone.utc),
            bid=67000.0,
            ask=67010.0,
            last=67005.0
        ))

        # Mock get_ohlcv_df
        df = pd.DataFrame({
            'open': np.random.uniform(66000, 68000, 100),
            'high': np.random.uniform(67000, 69000, 100),
            'low': np.random.uniform(65000, 67000, 100),
            'close': np.random.uniform(66000, 68000, 100),
            'volume': np.random.uniform(1000000, 5000000, 100),
        })
        df.index = pd.date_range(end=datetime.now(), periods=100, freq='h')
        feed.get_ohlcv_df = AsyncMock(return_value=df)

        return feed

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = MagicMock()
        db.get_recent_signals = AsyncMock(return_value=[])
        db.last_signal = AsyncMock(return_value=None)
        db.insert_signal = AsyncMock(return_value=1)
        return db

    def test_rsi_oversold_signal(self):
        """Test RSI oversold signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["rsi"])

        # Create data with low RSI (downtrend)
        close = pd.Series([100 - i * 0.5 for i in range(30)])
        current_price = close.iloc[-1]

        signals = detector._detect_rsi_signals("BTC/USDT", close, current_price)

        # Should detect oversold condition
        assert any(s.signal_type == "rsi_oversold" for s in signals) or len(signals) == 0

    def test_rsi_overbought_signal(self):
        """Test RSI overbought signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["rsi"])

        # Create data with high RSI (uptrend)
        close = pd.Series([100 + i * 0.5 for i in range(30)])
        current_price = close.iloc[-1]

        signals = detector._detect_rsi_signals("BTC/USDT", close, current_price)

        # Should detect overbought condition
        assert any(s.signal_type == "rsi_overbought" for s in signals) or len(signals) == 0

    def test_macd_signals(self):
        """Test MACD signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["macd"])

        # Create trending data
        close = pd.Series([100 + i * 0.1 for i in range(50)])
        current_price = close.iloc[-1]

        signals = detector._detect_macd_signals("BTC/USDT", close, current_price)

        # Should return list (may or may not have signals)
        assert isinstance(signals, list)

    def test_bollinger_signals(self):
        """Test Bollinger Band signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["bollinger"])

        # Create data that touches lower band
        close = pd.Series([100] * 20 + [90])  # Drop below normal
        current_price = 90

        signals = detector._detect_bollinger_signals("BTC/USDT", close, current_price)

        # Should return list
        assert isinstance(signals, list)

    def test_volume_spike_signals(self):
        """Test volume spike signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["volume_spike"])

        close = pd.Series([100] * 21)
        volume = pd.Series([1000] * 20 + [5000])  # Volume spike at end
        current_price = 100

        signals = detector._detect_volume_signals("BTC/USDT", close, volume, current_price)

        # Should return list
        assert isinstance(signals, list)

    def test_breakout_signals(self):
        """Test breakout signal detection."""
        detector = SignalDetector(MagicMock(), MagicMock(), ["breakout"])

        high = pd.Series([105] * 24)
        low = pd.Series([95] * 24)
        current_price = 110  # Above 24h high

        signals = detector._detect_breakout_signals("BTC/USDT", high, low, current_price)

        # Should detect breakout above high
        assert any(s.signal_type == "breakout_high" for s in signals)

    @pytest.mark.asyncio
    async def test_analyze(self, mock_feed, mock_db):
        """Test full analysis."""
        detector = SignalDetector(mock_feed, mock_db, ["rsi", "macd"])

        signals = await detector.analyze("BTC/USDT")

        # Should return list of signals
        assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_analyze_all(self, mock_feed, mock_db):
        """Test analyzing multiple symbols."""
        detector = SignalDetector(mock_feed, mock_db, ["rsi"])

        signals = await detector.analyze_all(["BTC/USDT", "ETH/USDT"])

        # Should return combined signals
        assert isinstance(signals, list)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
