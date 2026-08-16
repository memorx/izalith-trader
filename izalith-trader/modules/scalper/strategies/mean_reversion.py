"""
Izalith Trading System - Mean Reversion Strategy
"""
from typing import Optional
import pandas as pd

from core.adapters.base import PriceData
from core.data.indicators import rsi, bollinger_bands
from .base import BaseStrategy, TradeSignal


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion trading strategy.

    Entry (BUY):
    - Price < Bollinger Lower Band
    - RSI < 35

    Entry (SELL/SHORT):
    - Price > Bollinger Upper Band
    - RSI > 65

    Exit:
    - Price crosses Bollinger Middle Band
    - Or stop loss -0.3%
    """

    name = "mean_reversion"

    def __init__(
        self,
        profit_target_pct: float = 0.5,
        stop_loss_pct: float = 0.3,
        rsi_oversold: float = 35,
        rsi_overbought: float = 65,
        bb_period: int = 20,
        bb_std: float = 2.0
    ):
        super().__init__(profit_target_pct, stop_loss_pct)
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_period = bb_period
        self.bb_std = bb_std

    async def analyze(
        self,
        symbol: str,
        price_data: PriceData,
        ohlcv_df: pd.DataFrame
    ) -> Optional[TradeSignal]:
        """Analyze for mean reversion entry signals."""
        if ohlcv_df.empty or len(ohlcv_df) < self.bb_period:
            return None

        close = ohlcv_df['close']
        current_price = price_data.last

        # Calculate indicators
        bb = bollinger_bands(close, self.bb_period, self.bb_std)
        rsi_values = rsi(close, 14)

        upper_band = bb['upper'].iloc[-1]
        lower_band = bb['lower'].iloc[-1]
        middle_band = bb['middle'].iloc[-1]
        current_rsi = rsi_values.iloc[-1]

        # Check BUY conditions (oversold)
        if current_price < lower_band and current_rsi < self.rsi_oversold:
            # Calculate confidence
            price_deviation = (lower_band - current_price) / lower_band
            rsi_deviation = (self.rsi_oversold - current_rsi) / self.rsi_oversold

            confidence = min((price_deviation * 10 + rsi_deviation) / 2, 1.0)

            stop_loss = current_price * (1 - self.stop_loss_pct / 100)
            take_profit = middle_band  # Target middle band

            return TradeSignal(
                symbol=symbol,
                action="buy",
                price=current_price,
                quantity=0,
                reason=f"Mean reversion BUY: Price below BB lower ({lower_band:.2f}), RSI={current_rsi:.1f}",
                confidence=confidence,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=self.name
            )

        # Check SELL conditions (overbought)
        if current_price > upper_band and current_rsi > self.rsi_overbought:
            # Calculate confidence
            price_deviation = (current_price - upper_band) / upper_band
            rsi_deviation = (current_rsi - self.rsi_overbought) / (100 - self.rsi_overbought)

            confidence = min((price_deviation * 10 + rsi_deviation) / 2, 1.0)

            stop_loss = current_price * (1 + self.stop_loss_pct / 100)
            take_profit = middle_band  # Target middle band

            return TradeSignal(
                symbol=symbol,
                action="sell",
                price=current_price,
                quantity=0,
                reason=f"Mean reversion SELL: Price above BB upper ({upper_band:.2f}), RSI={current_rsi:.1f}",
                confidence=confidence,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy=self.name
            )

        return None

    def should_exit(self, position, current_price: float, ohlcv_df=None) -> Optional[TradeSignal]:
        """Check for exit at middle band."""
        # Check basic SL/TP first
        basic_exit = super().should_exit(position, current_price, ohlcv_df)
        if basic_exit:
            return basic_exit

        # Check middle band exit
        if ohlcv_df is not None and not ohlcv_df.empty:
            close = ohlcv_df['close']
            bb = bollinger_bands(close, self.bb_period, self.bb_std)
            middle_band = bb['middle'].iloc[-1]

            if position.side == "long" and current_price >= middle_band:
                return TradeSignal(
                    symbol=position.symbol,
                    action="sell",
                    price=current_price,
                    quantity=position.quantity,
                    reason=f"Mean reversion exit: Price reached middle band ({middle_band:.2f})",
                    confidence=0.9,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

            if position.side == "short" and current_price <= middle_band:
                return TradeSignal(
                    symbol=position.symbol,
                    action="buy",
                    price=current_price,
                    quantity=position.quantity,
                    reason=f"Mean reversion exit: Price reached middle band ({middle_band:.2f})",
                    confidence=0.9,
                    stop_loss=0,
                    take_profit=0,
                    strategy=self.name
                )

        return None
