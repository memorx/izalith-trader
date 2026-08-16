"""
Core data module - indicators and unified feed.
"""
from .indicators import (
    sma, ema, rsi, macd, bollinger_bands, atr,
    volume_sma, is_volume_spike, support_resistance
)
from .feed import UnifiedFeed

__all__ = [
    'sma', 'ema', 'rsi', 'macd', 'bollinger_bands', 'atr',
    'volume_sma', 'is_volume_spike', 'support_resistance',
    'UnifiedFeed'
]
