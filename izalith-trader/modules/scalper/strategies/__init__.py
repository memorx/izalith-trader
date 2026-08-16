"""
Scalper strategies module.
"""
from .base import BaseStrategy, TradeSignal
from .momentum import MomentumStrategy
from .mean_reversion import MeanReversionStrategy
from .breakout import BreakoutStrategy

__all__ = [
    'BaseStrategy', 'TradeSignal',
    'MomentumStrategy', 'MeanReversionStrategy', 'BreakoutStrategy'
]
