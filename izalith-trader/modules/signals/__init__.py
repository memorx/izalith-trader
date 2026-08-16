"""
Signals module - Detects and sends trading signals.
"""
from .detector import SignalDetector, Signal
from .telegram import TelegramNotifier
from .webhook import WebhookNotifier

__all__ = ['SignalDetector', 'Signal', 'TelegramNotifier', 'WebhookNotifier']
