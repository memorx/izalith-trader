"""
Izalith Trading System - Momentum Strategy
"""
from typing import Optional
import pandas as pd

from core.adapters.base import PriceData
from core.data.indicators import ema, rsi, macd, volume_sma
from .base import BaseStrategy, TradeSignal


class MomentumStrategy(BaseStrategy):
    """
    Momentum-based trading strategy.

    Entry (BUY):
    - EMA(5) > EMA(20) (uptrend)
    - RSI between 40-65 (not overbought)
    - Volume > 1.5x average
    - MACD histogram positive and rising

    Exit:
    - Take profit: +0.5%
    - Stop loss: -0.3%
    - Or RSI > 75
    """

    name = "momentum"

    def __init__(
        self,
        profit_target_pct: float = 0.5,
        stop_loss_pct: float = 0.3,
        rsi_low: float = 40,
        rsi_high: float = 65,
        volume_multiplier: float = 1.5
    ):
        super().__init__(profit_target_pct, stop_loss_pct)
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.volume_multiplier = volume_multiplier

    async def analyze(
        self,
        symbol: str,
        price_data: PriceData,
        ohlcv_df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """Analyze for momentum entry signals."""
        if ohlcv_df.empty or len(ohlcv_df) < 26:
            return None

        close = ohlcv_df['close']
        volume = ohlcv_df['volume']
        current_price = price_data.last

        # Calculate indicators
        ema_5 = ema(close, 5)
        ema_20 = ema(close, 20)
        rsi_values = rsi(close, 14)
        macd_data = macd(close)
        vol_sma = volume_sma(volume, 20)

        current_ema_5 = ema_5.iloc[-1]
        current_ema_20 = ema_20.iloc[-1]
        current_rsi = rsi_values.iloc[-1]
        current_histogram = macd_data['histogram'].iloc[-1]
        prev_histogram = macd_data['histogram'].iloc[-2] if len(macd_data['histogram']) > 1 else 0
        current_volume = volume.iloc[-1]
        avg_volume = vol_sma.iloc[-1]

        # Check BUY conditions
        ema_uptrend = current_ema_5 > current_ema_20
        rsi_valid = self.rsi_low <= current_rsi <= self.rsi_high
        volume_spike = current_volume > (avg_volume * self.volume_multiplier)
        macd_positive = current_histogram > 0 and current_histogram > prev_histogram

        if ema_uptrend and rsi_valid and volume_spike and macd_positive:
            confidence = 0.0
            reasons = []

            # Calculate confidence based on strength of signals
            if current_ema_5 > current_ema_20 * 1.001:
                confidence += 0.25
                reasons.append("Strong EMA uptrend")

            if 45 <= current_rsi <= 60:
                confidence += 0.25
                reasons.append("Optimal RSI zone")

            if current_volume > avg_volume * 2:
                confidence += 0.25
                reasons.append("Strong volume")

            if current_histogram > prev_histogram * 1.1:
                confidence += 0.25
                reasons.append("Rising MACD momentum")

            stop_loss = self.calculate_stop_loss(current_price, "buy")
            take_profit = self.calculate_take_profit(current_price, "buy")

            return TradeSignal(
                symbol=symbol,
                action="buy",
                price=current_price,
                quantity=0,  # Will be set by risk manager
                reason=f"Momentum entry: {', '.join(reasons)}",
                confidence=min(confidence, 1.0),
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=self.name
            )

        return None

    def should_exit(self, position, current_price: float, ohlcv_df=None) -> Optional[TradeSignal]:
        """Check for exit conditions including RSI overbought."""
        # Check basic SL/TP first
        basic_exit = super().should_exit(position, current_price, ohlcv_df)
        if basic_exit:
            return basic_exit

        # Check RSI overbought exit
        if ohlcv_df is not None and not ohlcv_df.empty:
            close = ohlcv_df['close']
            rsi_values = rsi(close, 14)
            current_rsi = rsi_values.iloc[-1]

            if current_rsi > 75:
                return TradeSignal(
                    symbol=position.symbol,
                    action="sell",
                    price=current_price,
                    quantity=position.quantity,
                    reason=f"RSI overbought ({current_rsi:.1f})",
                    confidence=0.8,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

        return None
