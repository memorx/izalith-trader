"""
Tests for technical indicators.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data.indicators import (
    sma, ema, rsi, macd, bollinger_bands, atr,
    volume_sma, is_volume_spike, support_resistance,
    detect_crossover, calculate_all_indicators
)


class TestMovingAverages:
    """Test moving average calculations."""

    def test_sma_basic(self):
        """Test simple moving average."""
        data = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        result = sma(data, 3)

        # SMA of last 3 values: (8+9+10)/3 = 9
        assert result.iloc[-1] == 9.0

    def test_ema_basic(self):
        """Test exponential moving average."""
        data = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        result = ema(data, 3)

        # EMA should be calculated
        assert len(result) == len(data)
        assert result.iloc[-1] > 0

    def test_sma_period_larger_than_data(self):
        """Test SMA with period larger than data."""
        data = pd.Series([1, 2, 3])
        result = sma(data, 10)

        # Should still return values with min_periods=1
        assert len(result) == len(data)


class TestRSI:
    """Test RSI calculations."""

    def test_rsi_overbought(self):
        """Test RSI detects overbought conditions."""
        # Create uptrend data
        data = pd.Series([100 + i for i in range(20)])
        result = rsi(data, 14)

        # RSI should be high (overbought) for strong uptrend
        assert result.iloc[-1] > 50

    def test_rsi_oversold(self):
        """Test RSI detects oversold conditions."""
        # Create downtrend data
        data = pd.Series([100 - i for i in range(20)])
        result = rsi(data, 14)

        # RSI should be low (oversold) for strong downtrend
        assert result.iloc[-1] < 50

    def test_rsi_range(self):
        """Test RSI stays within 0-100 range."""
        data = pd.Series(np.random.random(100) * 100)
        result = rsi(data, 14)

        # Drop NaN values and check range
        valid_values = result.dropna()
        assert all(0 <= v <= 100 for v in valid_values)


class TestMACD:
    """Test MACD calculations."""

    def test_macd_components(self):
        """Test MACD returns all components."""
        data = pd.Series(np.random.random(50) * 100)
        result = macd(data)

        assert 'macd' in result
        assert 'signal' in result
        assert 'histogram' in result

    def test_macd_histogram_calculation(self):
        """Test MACD histogram = MACD - Signal."""
        data = pd.Series(np.random.random(50) * 100)
        result = macd(data)

        # Histogram should be MACD - Signal
        expected = result['macd'] - result['signal']
        pd.testing.assert_series_equal(result['histogram'], expected)


class TestBollingerBands:
    """Test Bollinger Bands calculations."""

    def test_bollinger_bands_components(self):
        """Test Bollinger Bands returns all components."""
        data = pd.Series(np.random.random(30) * 100)
        result = bollinger_bands(data, 20, 2)

        assert 'upper' in result
        assert 'middle' in result
        assert 'lower' in result

    def test_bollinger_bands_order(self):
        """Test upper > middle > lower."""
        data = pd.Series(np.random.random(30) * 100)
        result = bollinger_bands(data, 20, 2)

        # Upper should be >= middle >= lower (compare values at same index)
        for i in range(len(data)):
            if not pd.isna(result['upper'].iloc[i]):
                assert result['upper'].iloc[i] >= result['middle'].iloc[i]
                assert result['middle'].iloc[i] >= result['lower'].iloc[i]


class TestATR:
    """Test ATR calculations."""

    def test_atr_positive(self):
        """Test ATR is always positive."""
        high = pd.Series([105, 110, 108, 112, 115])
        low = pd.Series([95, 100, 98, 102, 105])
        close = pd.Series([100, 105, 103, 108, 110])

        result = atr(high, low, close, 3)

        assert all(result.dropna() >= 0)


class TestVolumeIndicators:
    """Test volume indicators."""

    def test_volume_sma(self):
        """Test volume SMA calculation."""
        volume = pd.Series([1000, 2000, 3000, 4000, 5000])
        result = volume_sma(volume, 3)

        assert len(result) == len(volume)
        assert result.iloc[-1] == 4000  # (3000+4000+5000)/3

    def test_volume_spike_detection(self):
        """Test volume spike detection."""
        volume = pd.Series([1000] * 20 + [5000])  # Normal then spike
        result = is_volume_spike(volume, 20, 2.0)

        # Last value should be a spike
        assert result.iloc[-1] == True


class TestSupportResistance:
    """Test support/resistance calculations."""

    def test_support_resistance(self):
        """Test support and resistance levels."""
        data = pd.Series([100, 105, 95, 110, 90, 115])
        result = support_resistance(data, 5)

        assert 'support' in result
        assert 'resistance' in result
        assert result['support'] < result['resistance']


class TestCrossover:
    """Test crossover detection."""

    def test_bullish_crossover(self):
        """Test bullish crossover detection."""
        fast = pd.Series([1, 2, 3, 4, 5])
        slow = pd.Series([2, 2, 2, 2, 2])

        result = detect_crossover(fast, slow)

        # Fast crosses above slow at index 2
        assert 'bullish' in result
        assert 'bearish' in result

    def test_bearish_crossover(self):
        """Test bearish crossover detection."""
        fast = pd.Series([5, 4, 3, 2, 1])
        slow = pd.Series([3, 3, 3, 3, 3])

        result = detect_crossover(fast, slow)

        # Fast crosses below slow
        assert 'bearish' in result


class TestCalculateAllIndicators:
    """Test the combined indicator calculation."""

    def test_calculate_all_indicators(self):
        """Test all indicators are added to DataFrame."""
        df = pd.DataFrame({
            'open': [100, 101, 102, 103, 104] * 10,
            'high': [105, 106, 107, 108, 109] * 10,
            'low': [95, 96, 97, 98, 99] * 10,
            'close': [102, 103, 104, 105, 106] * 10,
            'volume': [1000, 2000, 3000, 4000, 5000] * 10,
        })

        result = calculate_all_indicators(df)

        # Check all expected columns are present
        expected_columns = [
            'sma_20', 'ema_5', 'ema_20', 'rsi',
            'macd', 'macd_signal', 'macd_histogram',
            'bb_upper', 'bb_middle', 'bb_lower',
            'atr', 'volume_sma', 'volume_spike'
        ]

        for col in expected_columns:
            assert col in result.columns, f"Missing column: {col}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
