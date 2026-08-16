"""
Izalith Trading System - Base Strategy
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List
import pandas as pd

from core.adapters.base import PriceData


@dataclass
class TradeSignal:
    """Represents a trade signal from a strategy."""
    symbol: str
    action: str  # buy, sell, hold
    price: float
    quantity: float
    reason: str
    confidence: float  # 0-1
    stop_loss: float
    take_profit: float
    strategy: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "price": self.price,
            "quantity": self.quantity,
            "reason": self.reason,
            "confidence": self.confidence,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "strategy": self.strategy,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class Position:
    """Represents an open position for exit checking."""
    symbol: str
    side: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    entry_time: datetime
    strategy: str


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""

    name: str = "base"

    def __init__(
        self,
        profit_target_pct: float = 0.5,
        stop_loss_pct: float = 0.3
    ):
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct

    @abstractmethod
    async def analyze(
        self,
        symbol: str,
        price_data: PriceData,
        ohlcv_df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """
        Analyze market data and generate a trade signal.

        Args:
            symbol: The trading symbol
            price_data: Current price data
            ohlcv_df: DataFrame with OHLCV data and calculated indicators

        Returns:
            TradeSignal if entry conditions are met, None otherwise
        """
        pass

    def should_exit(
        self,
        position: Position,
        current_price: float,
        ohlcv_df: Optional[pd.DataFrame] = None
    ) -> Optional[TradeSignal]:
        """
        Check if a position should be exited.

        Args:
            position: The current position
            current_price: Current market price
            ohlcv_df: Optional DataFrame with current indicators

        Returns:
            TradeSignal for exit if conditions are met, None otherwise
        """
        # Check stop loss
        if position.side == "long":
            if current_price <= position.stop_loss:
                return TradeSignal(
                    symbol=position.symbol,
                    action="sell",
                    price=current_price,
                    quantity=position.quantity,
                    reason="Stop loss triggered",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )
            elif current_price >= position.take_profit:
                return TradeSignal(
                    symbol=position.symbol,
                    action="sell",
                    price=current_price,
                    quantity=position.quantity,
                    reason="Take profit triggered",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )
        else:  # short
            if current_price >= position.stop_loss:
                return TradeSignal(
                    symbol=position.symbol,
                    action="buy",
                    price=current_price,
                    quantity=position.quantity,
                    reason="Stop loss triggered",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )
            elif current_price <= position.take_profit:
                return TradeSignal(
                    symbol=position.symbol,
                    action="buy",
                    price=current_price,
                    quantity=position.quantity,
                    reason="Take profit triggered",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

        return None

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """Calculate stop loss price."""
        if side == "long" or side == "buy":
            return entry_price * (1 - self.stop_loss_pct / 100)
        else:
            return entry_price * (1 + self.stop_loss_pct / 100)

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Calculate take profit price."""
        if side == "long" or side == "buy":
            return entry_price * (1 + self.profit_target_pct / 100)
        else:
            return entry_price * (1 - self.profit_target_pct / 100)
