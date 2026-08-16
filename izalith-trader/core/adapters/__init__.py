"""
Core adapters module.
"""
from .base import BaseAdapter, PriceData, OHLCV, OrderResult
from .crypto import CryptoAdapter
from .forex import ForexAdapter
from .stocks import StocksAdapter

__all__ = [
    'BaseAdapter', 'PriceData', 'OHLCV', 'OrderResult',
    'CryptoAdapter', 'ForexAdapter', 'StocksAdapter'
]
