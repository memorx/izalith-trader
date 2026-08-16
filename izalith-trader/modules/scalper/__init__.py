"""
Scalper module - Active trading with multiple strategies.
"""
from .strategies.base import BaseStrategy, TradeSignal
from .strategies.momentum import MomentumStrategy
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.breakout import BreakoutStrategy
from .risk_manager import RiskManager
from .executor import ScalperExecutor

__all__ = [
    'BaseStrategy', 'TradeSignal',
    'MomentumStrategy', 'MeanReversionStrategy', 'BreakoutStrategy',
    'RiskManager', 'ScalperExecutor'
]
