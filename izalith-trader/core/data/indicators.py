"""
Izalith Trading System - Technical Indicators
"""
import numpy as np
import pandas as pd
from typing import Dict, Union

Series = pd.Series


def sma(series: Series, period: int) -> Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=1).mean()


def ema(series: Series, period: int) -> Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def rsi(series: Series, period: int = 14) -> Series:
    """
    Relative Strength Index.

    RSI = 100 - (100 / (1 + RS))
    RS = Average Gain / Average Loss
    """
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    # Handle edge cases: when avg_loss is 0, RSI should be 100 (all gains)
    # When avg_gain is 0, RSI should be 0 (all losses)
    rsi_values = pd.Series(index=series.index, dtype=float)

    for i in range(len(series)):
        if pd.isna(avg_gain.iloc[i]) or pd.isna(avg_loss.iloc[i]):
            rsi_values.iloc[i] = 50.0
        elif avg_loss.iloc[i] == 0:
            rsi_values.iloc[i] = 100.0 if avg_gain.iloc[i] > 0 else 50.0
        elif avg_gain.iloc[i] == 0:
            rsi_values.iloc[i] = 0.0
        else:
            rs = avg_gain.iloc[i] / avg_loss.iloc[i]
            rsi_values.iloc[i] = 100 - (100 / (1 + rs))

    return rsi_values


def macd(
    series: Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Dict[str, Series]:
    """
    Moving Average Convergence Divergence.

    Returns dict with 'macd', 'signal', 'histogram'.
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)

    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return {
        'macd': macd_line,
        'signal': signal_line,
        'histogram': histogram
    }


def bollinger_bands(
    series: Series,
    period: int = 20,
    std: float = 2.0
) -> Dict[str, Series]:
    """
    Bollinger Bands.

    Returns dict with 'upper', 'middle', 'lower'.
    """
    middle = sma(series, period)
    std_dev = series.rolling(window=period, min_periods=1).std()

    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)

    return {
        'upper': upper,
        'middle': middle,
        'lower': lower
    }


def atr(
    high: Series,
    low: Series,
    close: Series,
    period: int = 14
) -> Series:
    """
    Average True Range.

    TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
    ATR = EMA(TR, period)
    """
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return ema(true_range, period)


def volume_sma(volume: Series, period: int = 20) -> Series:
    """Simple Moving Average of Volume."""
    return sma(volume, period)


def is_volume_spike(
    volume: Series,
    period: int = 20,
    threshold: float = 2.0
) -> Series:
    """
    Detect volume spikes.

    Returns True where volume > threshold * SMA(volume).
    """
    vol_sma = volume_sma(volume, period)
    return volume > (vol_sma * threshold)


def support_resistance(
    series: Series,
    window: int = 20
) -> Dict[str, float]:
    """
    Calculate support and resistance levels.

    Uses rolling min/max over the window period.
    """
    if len(series) < window:
        return {
            'support': series.min(),
            'resistance': series.max()
        }

    recent = series.tail(window)
    return {
        'support': recent.min(),
        'resistance': recent.max()
    }


def detect_crossover(
    fast_series: Series,
    slow_series: Series
) -> Dict[str, Series]:
    """
    Detect crossovers between two series.

    Returns dict with 'bullish' and 'bearish' boolean series.
    """
    # Previous positions
    prev_fast = fast_series.shift(1)
    prev_slow = slow_series.shift(1)

    # Bullish: fast crosses above slow
    bullish = (prev_fast <= prev_slow) & (fast_series > slow_series)

    # Bearish: fast crosses below slow
    bearish = (prev_fast >= prev_slow) & (fast_series < slow_series)

    return {
        'bullish': bullish.fillna(False),
        'bearish': bearish.fillna(False)
    }


def detect_divergence(
    price: Series,
    indicator: Series,
    window: int = 14
) -> Dict[str, bool]:
    """
    Detect price/indicator divergence.

    Returns dict with 'bullish_divergence' and 'bearish_divergence'.
    """
    if len(price) < window:
        return {'bullish_divergence': False, 'bearish_divergence': False}

    recent_price = price.tail(window)
    recent_indicator = indicator.tail(window)

    # Bullish divergence: price making lower lows, indicator making higher lows
    price_lower_low = recent_price.iloc[-1] < recent_price.iloc[0]
    indicator_higher_low = recent_indicator.iloc[-1] > recent_indicator.iloc[0]
    bullish_div = price_lower_low and indicator_higher_low

    # Bearish divergence: price making higher highs, indicator making lower highs
    price_higher_high = recent_price.iloc[-1] > recent_price.iloc[0]
    indicator_lower_high = recent_indicator.iloc[-1] < recent_indicator.iloc[0]
    bearish_div = price_higher_high and indicator_lower_high

    return {
        'bullish_divergence': bullish_div,
        'bearish_divergence': bearish_div
    }


def calculate_all_indicators(
    df: pd.DataFrame,
    close_col: str = 'close',
    high_col: str = 'high',
    low_col: str = 'low',
    volume_col: str = 'volume'
) -> pd.DataFrame:
    """
    Calculate all indicators and add them to the DataFrame.

    Expects DataFrame with OHLCV columns.
    """
    result = df.copy()

    close = result[close_col]
    high = result[high_col]
    low = result[low_col]
    volume = result[volume_col]

    # Moving averages
    result['sma_20'] = sma(close, 20)
    result['ema_5'] = ema(close, 5)
    result['ema_20'] = ema(close, 20)

    # RSI
    result['rsi'] = rsi(close, 14)

    # MACD
    macd_data = macd(close)
    result['macd'] = macd_data['macd']
    result['macd_signal'] = macd_data['signal']
    result['macd_histogram'] = macd_data['histogram']

    # Bollinger Bands
    bb = bollinger_bands(close, 20, 2)
    result['bb_upper'] = bb['upper']
    result['bb_middle'] = bb['middle']
    result['bb_lower'] = bb['lower']

    # ATR
    result['atr'] = atr(high, low, close, 14)

    # Volume
    result['volume_sma'] = volume_sma(volume, 20)
    result['volume_spike'] = is_volume_spike(volume, 20, 2.0)

    return result
