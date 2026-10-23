"""
Izalith Trading System - Breakout Strategy
"""
from typing import Optional
import pandas as pd

from core.adapters.base import PriceData
from core.data.indicators import volume_sma, atr
from .base import BaseStrategy, TradeSignal


class BreakoutStrategy(BaseStrategy):
    """
    Breakout trading strategy.

    Entry (BUY):
    - Price breaks above 24h high
    - Volume > 2x average
    - Confirmation: closed above for 2 candles

    Exit:
    - Take profit: +1%
    - Stop loss: -0.5%
    - Trailing stop: -0.3% from high
    """

    name = "breakout"

    def __init__(
        self,
        profit_target_pct: float = 1.0,
        stop_loss_pct: float = 0.5,
        trailing_stop_pct: float = 0.3,
        volume_multiplier: float = 2.0,
        lookback_periods: int = 24
    ):
        super().__init__(profit_target_pct, stop_loss_pct)
        self.trailing_stop_pct = trailing_stop_pct
        self.volume_multiplier = volume_multiplier
        self.lookback_periods = lookback_periods
        self._high_since_entry = {}  # Track high for trailing stop

    async def analyze(
        self,
        symbol: str,
        price_data: PriceData,
        ohlcv_df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """Analyze for breakout entry signals."""
        if ohlcv_df.empty or len(ohlcv_df) < self.lookback_periods:
            return None

        high = ohlcv_df['high']
        low = ohlcv_df['low']
        close = ohlcv_df['close']
        volume = ohlcv_df['volume']
        current_price = price_data.last

        # Calculate levels from the candles BEFORE the two confirmation candles.
        # If the window included them, a close could never be above its own candle's high.
        lookback = min(self.lookback_periods, len(high) - 2)
        high_24h = high.iloc[-(lookback+2):-2].max()
        low_24h = low.iloc[-(lookback+2):-2].min()

        # Calculate volume
        vol_avg = volume_sma(volume, 20).iloc[-1]
        current_volume = volume.iloc[-1]
        volume_spike = current_volume > (vol_avg * self.volume_multiplier)

        # Check for confirmation (last 2 closes above high)
        last_two_closes = close.iloc[-2:]
        bullish_confirmation = all(c > high_24h for c in last_two_closes)
        bearish_confirmation = all(c < low_24h for c in last_two_closes)

        # Breakout above high
        if current_price > high_24h and volume_spike and bullish_confirmation:
            # Calculate ATR for dynamic stop loss
            atr_value = atr(high, low, close, 14).iloc[-1]

            confidence = 0.6
            if current_volume > vol_avg * 3:
                confidence += 0.2
            if current_price > high_24h * 1.005:
                confidence += 0.2

            stop_loss = current_price - (atr_value * 1.5)
            take_profit = current_price * (1 + self.profit_target_pct / 100)

            return TradeSignal(
                symbol=symbol,
                action="buy",
                price=current_price,
                quantity=0,
                reason=f"Bullish breakout: Price broke 24h high ({high_24h:.2f}) with volume",
                confidence=min(confidence, 1.0),
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=self.name
            )

        # Breakdown below low
        if current_price < low_24h and volume_spike and bearish_confirmation:
            atr_value = atr(high, low, close, 14).iloc[-1]

            confidence = 0.6
            if current_volume > vol_avg * 3:
                confidence += 0.2
            if current_price < low_24h * 0.995:
                confidence += 0.2

            stop_loss = current_price + (atr_value * 1.5)
            take_profit = current_price * (1 - self.profit_target_pct / 100)

            return TradeSignal(
                symbol=symbol,
                action="sell",
                price=current_price,
                quantity=0,
                reason=f"Bearish breakdown: Price broke 24h low ({low_24h:.2f}) with volume",
                confidence=min(confidence, 1.0),
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=self.name
            )

        return None

    def should_exit(self, position, current_price: float, ohlcv_df=None) -> Optional[TradeSignal]:
        """Check for exit with trailing stop."""
        # Check basic SL/TP first
        key = f"{position.symbol}:{position.entry_time}"
        basic_exit = super().should_exit(position, current_price, ohlcv_df)
        if basic_exit:
            self._high_since_entry.pop(key, None)
            return basic_exit

        # Trailing stop logic

        if position.side == "long":
            # Track high since entry
            if key not in self._high_since_entry:
                self._high_since_entry[key] = current_price
            else:
                self._high_since_entry[key] = max(self._high_since_entry[key], current_price)

            high_since = self._high_since_entry[key]
            trailing_stop = high_since * (1 - self.trailing_stop_pct / 100)

            if current_price <= trailing_stop and current_price > position.entry_price:
                del self._high_since_entry[key]
                return TradeSignal(
                    symbol=position.symbol,
                    action="sell",
                    price=current_price,
                    quantity=position.quantity,
                    reason=f"Trailing stop triggered: {self.trailing_stop_pct}% from high ({high_since:.2f})",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

        else:  # short
            if key not in self._high_since_entry:
                self._high_since_entry[key] = current_price
            else:
                self._high_since_entry[key] = min(self._high_since_entry[key], current_price)

            low_since = self._high_since_entry[key]
            trailing_stop = low_since * (1 + self.trailing_stop_pct / 100)

            if current_price >= trailing_stop and current_price < position.entry_price:
                del self._high_since_entry[key]
                return TradeSignal(
                    symbol=position.symbol,
                    action="buy",
                    price=current_price,
                    quantity=position.quantity,
                    reason=f"Trailing stop triggered: {self.trailing_stop_pct}% from low ({low_since:.2f})",
                    confidence=1.0,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

        return None
