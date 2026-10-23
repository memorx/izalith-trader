"""
Izalith Trading System - Signal Detector
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict
import pandas as pd

from core.data.feed import UnifiedFeed
from core.data.indicators import (
    rsi, macd, bollinger_bands, is_volume_spike,
    support_resistance, detect_crossover
)
from core.utils.logger import get_logger, log_signal
from core.utils.database import Database

logger = get_logger("signals")


def fmt_price(p: float) -> str:
    """Precio con los decimales que importan (BTC 2, EUR_USD 5, DOGE 6)."""
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.5f}"
    return f"{p:.6f}"


@dataclass
class Signal:
    """Represents a detected trading signal."""
    symbol: str
    signal_type: str  # rsi_oversold, rsi_overbought, macd_cross, breakout, volume_spike, bollinger
    direction: str    # bullish, bearish
    strength: float   # 0-1
    message: str      # Human readable message
    price: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    indicator_value: Optional[float] = None
    db_id: Optional[int] = None
    candle_time: Optional[str] = None   # vela cerrada que generó la señal
    simulated: bool = False             # True si los datos eran inventados: no se manda a nadie

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'signal_type': self.signal_type,
            'direction': self.direction,
            'strength': self.strength,
            'message': self.message,
            'price': self.price,
            'timestamp': self.timestamp.isoformat(),
            'indicator_value': self.indicator_value,
            'candle_time': self.candle_time,
            'simulated': self.simulated
        }


class SignalDetector:
    """Detects trading signals from market data."""

    # Signal thresholds
    RSI_OVERSOLD = 30
    RSI_OVERBOUGHT = 70
    VOLUME_SPIKE_THRESHOLD = 2.0
    # Con velas de 1 hora, una condición dura varias velas: no se repite la alerta en 4 horas
    DUPLICATE_WINDOW_MINUTES = 240

    def __init__(
        self,
        feed: UnifiedFeed,
        db: Database,
        enabled_signals: List[str] = None
    ):
        self.feed = feed
        self.db = db
        self.enabled_signals = enabled_signals or ["rsi", "macd", "breakout", "volume_spike"]
        self._recent_signals: Dict[str, datetime] = {}

    async def analyze(self, symbol: str) -> List[Signal]:
        """Analyze a symbol and return detected signals."""
        signals = []

        try:
            # Get price data
            price_data = await self.feed.get_price(symbol)
            if not price_data:
                return signals

            current_price = price_data.last

            # Get OHLCV data
            df = await self.feed.get_ohlcv_df(symbol, "1h", 100)
            if df.empty or len(df) < 20:
                return signals

            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume']

            # RSI Signals
            if "rsi" in self.enabled_signals:
                rsi_signals = self._detect_rsi_signals(symbol, close, current_price)
                signals.extend(rsi_signals)

            # MACD Signals
            if "macd" in self.enabled_signals:
                macd_signals = self._detect_macd_signals(symbol, close, current_price)
                signals.extend(macd_signals)

            # Bollinger Band Signals
            if "bollinger" in self.enabled_signals:
                bb_signals = self._detect_bollinger_signals(symbol, close, current_price)
                signals.extend(bb_signals)

            # Volume Spike Signals
            if "volume_spike" in self.enabled_signals:
                vol_signals = self._detect_volume_signals(symbol, close, volume, current_price)
                signals.extend(vol_signals)

            # Breakout Signals
            if "breakout" in self.enabled_signals:
                breakout_signals = self._detect_breakout_signals(symbol, high, low, current_price)
                signals.extend(breakout_signals)

            simulated = bool(price_data.simulated or df.attrs.get("simulated"))
            candle_time = df.index[-1].isoformat()
            for sig in signals:
                sig.candle_time = candle_time
                sig.simulated = simulated

            # Filter duplicates
            signals = await self._filter_duplicates(signals)

            # Log and store signals
            for signal in signals:
                log_signal(
                    signal.symbol,
                    signal.signal_type,
                    signal.direction,
                    signal.strength,
                    signal.price
                )

                # Store in database
                signal.db_id = await self.db.insert_signal(
                    symbol=signal.symbol,
                    signal_type=signal.signal_type,
                    direction=signal.direction,
                    strength=signal.strength,
                    price=signal.price,
                    candle_time=signal.candle_time,
                    simulated=signal.simulated
                )

        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")

        return signals

    def _detect_rsi_signals(
        self,
        symbol: str,
        close: pd.Series,
        current_price: float
    ) -> List[Signal]:
        """Detect RSI-based signals."""
        signals = []

        rsi_values = rsi(close, 14)
        current_rsi = rsi_values.iloc[-1]

        if current_rsi < self.RSI_OVERSOLD:
            strength = (self.RSI_OVERSOLD - current_rsi) / self.RSI_OVERSOLD
            signals.append(Signal(
                symbol=symbol,
                signal_type="rsi_oversold",
                direction="bullish",
                strength=min(strength, 1.0),
                message=f"RSI({current_rsi:.1f}) is oversold - potential buying opportunity",
                price=current_price,
                indicator_value=current_rsi
            ))

        elif current_rsi > self.RSI_OVERBOUGHT:
            strength = (current_rsi - self.RSI_OVERBOUGHT) / (100 - self.RSI_OVERBOUGHT)
            signals.append(Signal(
                symbol=symbol,
                signal_type="rsi_overbought",
                direction="bearish",
                strength=min(strength, 1.0),
                message=f"RSI({current_rsi:.1f}) is overbought - potential selling opportunity",
                price=current_price,
                indicator_value=current_rsi
            ))

        return signals

    def _detect_macd_signals(
        self,
        symbol: str,
        close: pd.Series,
        current_price: float
    ) -> List[Signal]:
        """Detect MACD crossover signals."""
        signals = []

        macd_data = macd(close)
        macd_line = macd_data['macd']
        signal_line = macd_data['signal']
        histogram = macd_data['histogram']

        crossovers = detect_crossover(macd_line, signal_line)

        # Check for recent crossover (last 2 bars)
        if crossovers['bullish'].iloc[-1] or crossovers['bullish'].iloc[-2]:
            strength = min(abs(histogram.iloc[-1]) / 0.01, 1.0)  # Normalize
            signals.append(Signal(
                symbol=symbol,
                signal_type="macd_bullish_cross",
                direction="bullish",
                strength=strength,
                message="MACD crossed above signal line - bullish momentum",
                price=current_price,
                indicator_value=histogram.iloc[-1]
            ))

        elif crossovers['bearish'].iloc[-1] or crossovers['bearish'].iloc[-2]:
            strength = min(abs(histogram.iloc[-1]) / 0.01, 1.0)
            signals.append(Signal(
                symbol=symbol,
                signal_type="macd_bearish_cross",
                direction="bearish",
                strength=strength,
                message="MACD crossed below signal line - bearish momentum",
                price=current_price,
                indicator_value=histogram.iloc[-1]
            ))

        return signals

    def _detect_bollinger_signals(
        self,
        symbol: str,
        close: pd.Series,
        current_price: float
    ) -> List[Signal]:
        """Detect Bollinger Band signals."""
        signals = []

        bb = bollinger_bands(close, 20, 2)
        upper = bb['upper'].iloc[-1]
        lower = bb['lower'].iloc[-1]
        middle = bb['middle'].iloc[-1]

        # Price touching lower band
        if current_price <= lower:
            strength = (lower - current_price) / (middle - lower) if middle != lower else 0.5
            signals.append(Signal(
                symbol=symbol,
                signal_type="bollinger_lower",
                direction="bullish",
                strength=min(abs(strength) + 0.5, 1.0),
                message=f"Price touched lower Bollinger Band ({fmt_price(lower)}) - potential bounce",
                price=current_price,
                indicator_value=lower
            ))

        # Price touching upper band
        elif current_price >= upper:
            strength = (current_price - upper) / (upper - middle) if upper != middle else 0.5
            signals.append(Signal(
                symbol=symbol,
                signal_type="bollinger_upper",
                direction="bearish",
                strength=min(abs(strength) + 0.5, 1.0),
                message=f"Price touched upper Bollinger Band ({fmt_price(upper)}) - potential reversal",
                price=current_price,
                indicator_value=upper
            ))

        return signals

    def _detect_volume_signals(
        self,
        symbol: str,
        close: pd.Series,
        volume: pd.Series,
        current_price: float
    ) -> List[Signal]:
        """Detect volume spike signals."""
        signals = []

        spikes = is_volume_spike(volume, 20, self.VOLUME_SPIKE_THRESHOLD)

        if spikes.iloc[-1]:
            # Determine direction based on price movement
            price_change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] if len(close) > 1 else 0

            if price_change > 0:
                signals.append(Signal(
                    symbol=symbol,
                    signal_type="volume_spike_bullish",
                    direction="bullish",
                    strength=0.7,
                    message=f"Volume spike detected with bullish price action (+{price_change*100:.2f}%)",
                    price=current_price,
                    indicator_value=volume.iloc[-1]
                ))
            elif price_change < 0:
                signals.append(Signal(
                    symbol=symbol,
                    signal_type="volume_spike_bearish",
                    direction="bearish",
                    strength=0.7,
                    message=f"Volume spike detected with bearish price action ({price_change*100:.2f}%)",
                    price=current_price,
                    indicator_value=volume.iloc[-1]
                ))

        return signals

    def _detect_breakout_signals(
        self,
        symbol: str,
        high: pd.Series,
        low: pd.Series,
        current_price: float
    ) -> List[Signal]:
        """Detect price breakout signals."""
        signals = []

        # Get 24h high/low (assuming hourly data)
        period = min(24, len(high))
        high_24h = high.tail(period).max()
        low_24h = low.tail(period).min()

        # Breakout above 24h high
        if current_price > high_24h:
            strength = min((current_price - high_24h) / high_24h * 100, 1.0)
            signals.append(Signal(
                symbol=symbol,
                signal_type="breakout_high",
                direction="bullish",
                strength=max(strength, 0.6),
                message=f"Price broke above 24h high ({fmt_price(high_24h)}) - bullish breakout",
                price=current_price,
                indicator_value=high_24h
            ))

        # Breakout below 24h low
        elif current_price < low_24h:
            strength = min((low_24h - current_price) / low_24h * 100, 1.0)
            signals.append(Signal(
                symbol=symbol,
                signal_type="breakout_low",
                direction="bearish",
                strength=max(strength, 0.6),
                message=f"Price broke below 24h low ({fmt_price(low_24h)}) - bearish breakdown",
                price=current_price,
                indicator_value=low_24h
            ))

        return signals

    async def _filter_duplicates(self, signals: List[Signal]) -> List[Signal]:
        """Filter out duplicate signals sent recently."""
        filtered = []

        for signal in signals:
            key = f"{signal.symbol}:{signal.signal_type}"

            last = await self.db.last_signal(signal.symbol, signal.signal_type)
            if last and signal.candle_time and last.get("candle_time") == signal.candle_time:
                continue  # misma vela, misma señal
            recent = await self.db.get_recent_signals(
                signal.symbol,
                signal.signal_type,
                self.DUPLICATE_WINDOW_MINUTES
            )

            if not recent:
                filtered.append(signal)
                self._recent_signals[key] = signal.timestamp

        return filtered

    async def analyze_all(self, symbols: List[str]) -> List[Signal]:
        """Analyze multiple symbols and return all detected signals."""
        all_signals = []

        tasks = [self.analyze(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_signals.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Analysis error: {result}")

        return all_signals
