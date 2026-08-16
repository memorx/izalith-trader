"""
Izalith Trading System - Scalper Executor
"""
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timezone

from core.adapters.base import BaseAdapter, OrderResult
from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade
from config.assets import ASSETS, get_asset, Market

from .strategies.base import BaseStrategy, TradeSignal, Position
from .strategies.momentum import MomentumStrategy
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.breakout import BreakoutStrategy
from .risk_manager import RiskManager

logger = get_logger("scalper")


class ScalperExecutor:
    """
    Executes scalper trades based on strategy signals.
    """

    STRATEGY_MAP = {
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "breakout": BreakoutStrategy
    }

    def __init__(
        self,
        adapters: Dict[str, BaseAdapter],
        feed: UnifiedFeed,
        portfolio: PortfolioTracker,
        db: Database,
        config: dict
    ):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.config = config

        self.risk_manager = RiskManager(portfolio, db, config)

        # Initialize strategies
        profit_target = config.get("scalper_profit_target_pct", 0.5)
        stop_loss = config.get("scalper_stop_loss_pct", 0.3)

        self.strategies: Dict[str, BaseStrategy] = {}
        enabled_strategies = config.get("scalper_strategies", ["momentum", "mean_reversion"])

        for name in enabled_strategies:
            if name in self.STRATEGY_MAP:
                self.strategies[name] = self.STRATEGY_MAP[name](profit_target, stop_loss)
                logger.info(f"Loaded strategy: {name}")

        # Track open positions with strategy info
        self._positions: Dict[str, Position] = {}
        self._running = False

    async def run(self) -> None:
        """Main executor loop."""
        self._running = True
        enabled_assets = self.config.get("scalper_enabled_assets", [])

        logger.info(f"Scalper started with {len(self.strategies)} strategies, {len(enabled_assets)} assets")

        while self._running:
            try:
                # Check exits first
                await self._check_exits()

                # Look for new entries
                await self._scan_for_entries(enabled_assets)

            except Exception as e:
                logger.error(f"Error in scalper loop: {e}")

            await asyncio.sleep(10)  # Check every 10 seconds

    async def stop(self) -> None:
        """Stop the executor."""
        self._running = False
        logger.info("Scalper stopped")

    async def _scan_for_entries(self, symbols: List[str]) -> None:
        """Scan symbols for entry signals."""
        for symbol in symbols:
            # Skip if already have position
            if symbol in self._positions:
                continue

            try:
                # Get market data
                price_data = await self.feed.get_price(symbol)
                if not price_data:
                    continue

                ohlcv_df = await self.feed.get_ohlcv_df(symbol, "1h", 100)
                if ohlcv_df.empty:
                    continue

                # Get adapter
                try:
                    asset = get_asset(symbol)
                    adapter = self.adapters.get(asset.market.value)
                except ValueError:
                    adapter = self.adapters.get("crypto")

                if not adapter:
                    continue

                # Check each strategy for signals
                for strategy_name, strategy in self.strategies.items():
                    signal = await strategy.analyze(symbol, price_data, ohlcv_df)

                    if signal and signal.action in ["buy", "sell"]:
                        await self._try_execute_entry(signal, adapter)
                        break  # Only one signal per symbol

            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")

    async def _try_execute_entry(
        self,
        signal: TradeSignal,
        adapter: BaseAdapter
    ) -> Optional[OrderResult]:
        """Try to execute an entry based on signal."""
        # Risk check
        can_trade, reason = await self.risk_manager.can_open_position(
            signal.symbol,
            signal,
            adapter.is_market_open()
        )

        if not can_trade:
            logger.debug(f"Trade rejected for {signal.symbol}: {reason}")
            return None

        # Calculate position size
        quantity = self.risk_manager.calculate_position_size(
            signal.symbol,
            signal,
            signal.price
        )

        if quantity <= 0:
            logger.debug(f"Position size too small for {signal.symbol}")
            return None

        signal.quantity = quantity

        # Execute order
        result = await adapter.place_market_order(
            signal.symbol,
            signal.action,
            quantity
        )

        if result.success:
            # Track position
            self._positions[signal.symbol] = Position(
                symbol=signal.symbol,
                side="long" if signal.action == "buy" else "short",
                entry_price=result.price or signal.price,
                quantity=quantity,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                entry_time=datetime.now(timezone.utc),
                strategy=signal.strategy
            )

            log_trade("scalper", signal.symbol, signal.action, result.price or signal.price, quantity)

            # Add to portfolio
            try:
                asset = get_asset(signal.symbol)
                market = asset.market.value
            except ValueError:
                market = "crypto"

            await self.portfolio.add_position(
                symbol=signal.symbol,
                market=market,
                side=signal.action,
                quantity=quantity,
                price=result.price or signal.price,
                module="scalper",
                strategy=signal.strategy
            )

            logger.info(
                f"Entry executed: {signal.action.upper()} {quantity:.6f} {signal.symbol} "
                f"@ ${result.price or signal.price:,.2f} ({signal.strategy}) - {signal.reason}"
            )

        else:
            logger.error(f"Entry failed for {signal.symbol}: {result.error}")

        return result

    async def _check_exits(self) -> None:
        """Check all open positions for exit signals."""
        for symbol, position in list(self._positions.items()):
            try:
                # Get current price
                price_data = await self.feed.get_price(symbol)
                if not price_data:
                    continue

                current_price = price_data.last

                # Get OHLCV for strategy-specific exits
                ohlcv_df = await self.feed.get_ohlcv_df(symbol, "1h", 50)

                # Get the strategy that opened this position
                strategy = self.strategies.get(position.strategy)
                if not strategy:
                    continue

                # Check for exit
                exit_signal = strategy.should_exit(position, current_price, ohlcv_df)

                if exit_signal:
                    await self._execute_exit(exit_signal, position)

            except Exception as e:
                logger.error(f"Error checking exit for {symbol}: {e}")

    async def _execute_exit(
        self,
        signal: TradeSignal,
        position: Position
    ) -> Optional[OrderResult]:
        """Execute an exit trade."""
        # Get adapter
        try:
            asset = get_asset(signal.symbol)
            adapter = self.adapters.get(asset.market.value)
        except ValueError:
            adapter = self.adapters.get("crypto")

        if not adapter:
            return None

        # Execute order
        result = await adapter.place_market_order(
            signal.symbol,
            signal.action,
            position.quantity
        )

        if result.success:
            # Calculate P/L
            exit_price = result.price or signal.price
            if position.side == "long":
                pnl = (exit_price - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - exit_price) * position.quantity

            pnl_pct = (pnl / (position.entry_price * position.quantity)) * 100

            # Close portfolio position
            await self.portfolio.close_position(signal.symbol, exit_price)

            # Remove from tracking
            del self._positions[signal.symbol]

            log_trade("scalper", signal.symbol, signal.action, exit_price, position.quantity, pnl)

            logger.info(
                f"Exit executed: {signal.action.upper()} {position.quantity:.6f} {signal.symbol} "
                f"@ ${exit_price:,.2f} | P/L: ${pnl:+,.2f} ({pnl_pct:+.2f}%) - {signal.reason}"
            )

        else:
            logger.error(f"Exit failed for {signal.symbol}: {result.error}")

        return result

    def get_status(self) -> Dict:
        """Get current scalper status."""
        return {
            "running": self._running,
            "strategies": list(self.strategies.keys()),
            "open_positions": len(self._positions),
            "positions": {
                symbol: {
                    "side": pos.side,
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "stop_loss": pos.stop_loss,
                    "take_profit": pos.take_profit,
                    "strategy": pos.strategy,
                    "entry_time": pos.entry_time.isoformat()
                }
                for symbol, pos in self._positions.items()
            },
            "risk": self.risk_manager.get_status()
        }
