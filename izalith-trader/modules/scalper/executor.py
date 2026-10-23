"""
Izalith Trading System - Scalper Executor

Las posiciones del scalper viven en el PortfolioTracker (y en la base de datos), con su
stop y su take profit: un reinicio no las deja huérfanas.

Aviso: en el laboratorio (python -m lab.review) las tres estrategias del scalper salen
en rojo con datos reales de Binance: las comisiones se comen el margen. Este módulo queda
para pruebas en papel, no para dinero real.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter, OrderResult
from core.data.feed import UnifiedFeed
from core.execution import execute_order
from core.portfolio.tracker import PortfolioTracker, Position as TrackedPosition
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade
from config.assets import get_asset

from .strategies.base import BaseStrategy, TradeSignal, Position
from .strategies.momentum import MomentumStrategy
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.breakout import BreakoutStrategy
from .risk_manager import RiskManager, MODULE

logger = get_logger("scalper")


class _StopsOnly(BaseStrategy):
    """Salida por stop y take profit cuando la estrategia que abrió la posición ya no está cargada."""
    name = "stops"

    async def analyze(self, symbol, price_data, ohlcv_df):
        return None


class ScalperExecutor:
    """Executes scalper trades based on strategy signals."""

    STRATEGY_MAP = {
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "breakout": BreakoutStrategy
    }

    def __init__(self, adapters: Dict[str, BaseAdapter], feed: UnifiedFeed, portfolio: PortfolioTracker,
                 db: Database, config: dict):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.config = config
        self.dry_run = config.get("dry_run", True)
        self.risk_manager = RiskManager(portfolio, db, config)

        profit_target = config.get("scalper_profit_target_pct", 0.5)
        stop_loss = config.get("scalper_stop_loss_pct", 0.3)

        self.strategies: Dict[str, BaseStrategy] = {}
        for name in config.get("scalper_strategies", ["momentum", "mean_reversion"]):
            if name in self.STRATEGY_MAP:
                self.strategies[name] = self.STRATEGY_MAP[name](profit_target, stop_loss)
                logger.info(f"Loaded strategy: {name}")
        self._fallback = _StopsOnly(profit_target, stop_loss)
        self._running = False

    def _adapter_for(self, symbol: str) -> Optional[BaseAdapter]:
        try:
            return self.adapters.get(get_asset(symbol).market.value)
        except ValueError:
            return self.adapters.get("crypto")

    @staticmethod
    def _market_of(symbol: str) -> str:
        try:
            return get_asset(symbol).market.value
        except ValueError:
            return "crypto"

    async def run(self) -> None:
        self._running = True
        enabled_assets = self.config.get("scalper_enabled_assets", [])
        open_now = self.portfolio.get_all_positions(module=MODULE)
        logger.info(f"Scalper started with {len(self.strategies)} strategies, {len(enabled_assets)} assets, "
                    f"{len(open_now)} posiciones abiertas recuperadas")
        while self._running:
            try:
                await self._check_exits()
                await self._scan_for_entries(enabled_assets)
            except Exception as e:
                logger.error(f"Error in scalper loop: {e}")
            await asyncio.sleep(10)

    async def stop(self) -> None:
        self._running = False
        logger.info("Scalper stopped")

    def _usable(self, price_data, ohlcv_df) -> bool:
        """Con dinero real nunca se opera sobre datos inventados."""
        if self.dry_run:
            return True
        return not (price_data.simulated or ohlcv_df.attrs.get("simulated"))

    async def _scan_for_entries(self, symbols: List[str]) -> None:
        held = {p.symbol for p in self.portfolio.get_all_positions(module=MODULE)}
        for symbol in symbols:
            if symbol in held:
                continue
            try:
                price_data = await self.feed.get_price(symbol)
                if not price_data:
                    continue
                ohlcv_df = await self.feed.get_ohlcv_df(symbol, "1h", 100)
                if ohlcv_df.empty or not self._usable(price_data, ohlcv_df):
                    continue
                adapter = self._adapter_for(symbol)
                if not adapter:
                    continue
                for strategy in self.strategies.values():
                    signal = await strategy.analyze(symbol, price_data, ohlcv_df)
                    if not signal or signal.action not in ("buy", "sell"):
                        continue
                    if signal.action == "sell" and not adapter.supports_short:
                        logger.debug(f"{symbol}: señal de corto ignorada, {adapter.market} no permite cortos")
                        continue
                    candle = ohlcv_df.index[-1].isoformat()
                    await self._try_execute_entry(signal, adapter, candle)
                    break
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")

    async def _try_execute_entry(self, signal: TradeSignal, adapter: BaseAdapter, candle: str) -> Optional[OrderResult]:
        market = self._market_of(signal.symbol)
        can_trade, reason = await self.risk_manager.can_open_position(
            signal.symbol, signal, adapter.is_market_open(), market)
        if not can_trade:
            logger.debug(f"Trade rejected for {signal.symbol}: {reason}")
            return None

        quantity = adapter.amount_to_precision(
            signal.symbol, await self.risk_manager.calculate_position_size(signal.symbol, signal, signal.price))
        if quantity <= 0 or quantity * signal.price < adapter.min_notional(signal.symbol):
            logger.debug(f"Position size too small for {signal.symbol}")
            return None

        result = await execute_order(self.db, adapter, MODULE, signal.symbol, signal.action, quantity=quantity,
                                     purpose=f"scalper:entry:{signal.symbol}:{candle}")
        if not result.success or result.filled_quantity <= 0:
            if result.status not in ("duplicate", "freno", "en_vuelo"):
                logger.error(f"Entry failed for {signal.symbol}: {result.error}")
            return result

        fill = result.price or signal.price
        # Se mantienen las distancias de la señal, medidas desde el precio real de entrada
        sl = fill * (signal.stop_loss / signal.price) if signal.stop_loss else None
        tp = fill * (signal.take_profit / signal.price) if signal.take_profit else None
        await self.portfolio.add_position(
            symbol=signal.symbol, market=market, side=signal.action, quantity=result.filled_quantity,
            price=fill, module=MODULE, strategy=signal.strategy, fee=result.fee, stop_loss=sl, take_profit=tp,
            simulated=result.simulated, client_order_id=result.client_order_id)
        log_trade(MODULE, signal.symbol, signal.action, fill, result.filled_quantity)
        logger.info(f"Entry: {signal.action.upper()} {result.filled_quantity:.6f} {signal.symbol} @ {fill:,.4f} "
                    f"({signal.strategy}) - {signal.reason}")
        return result

    async def _check_exits(self) -> None:
        for tracked in list(self.portfolio.get_all_positions(module=MODULE)):
            try:
                price_data = await self.feed.get_price(tracked.symbol)
                if not price_data:
                    continue
                ohlcv_df = await self.feed.get_ohlcv_df(tracked.symbol, "1h", 50)
                if not self.dry_run and price_data.simulated:
                    continue
                strategy = self.strategies.get(tracked.strategy or "", self._fallback)
                pos = Position(
                    symbol=tracked.symbol, side=tracked.side, entry_price=tracked.entry_price,
                    quantity=tracked.quantity,
                    stop_loss=tracked.stop_loss or self._fallback.calculate_stop_loss(tracked.entry_price, tracked.side),
                    take_profit=tracked.take_profit or self._fallback.calculate_take_profit(tracked.entry_price, tracked.side),
                    entry_time=tracked.entry_time, strategy=tracked.strategy or "")
                exit_signal = strategy.should_exit(pos, price_data.last, ohlcv_df)
                if exit_signal:
                    await self._execute_exit(exit_signal, tracked)
            except Exception as e:
                logger.error(f"Error checking exit for {tracked.symbol}: {e}")

    async def _execute_exit(self, signal: TradeSignal, tracked: TrackedPosition) -> Optional[OrderResult]:
        adapter = self._adapter_for(tracked.symbol)
        if not adapter:
            return None
        result = await execute_order(self.db, adapter, MODULE, tracked.symbol, signal.action,
                                     quantity=tracked.quantity, close_side=tracked.side,
                                     purpose=f"scalper:exit:{tracked.trade_id}")
        if not result.success or result.filled_quantity <= 0:
            if result.status not in ("duplicate", "en_vuelo"):
                logger.error(f"Exit failed for {tracked.symbol}: {result.error}")
            return result
        exit_price = result.price or signal.price
        pnl = await self.portfolio.reduce_position(tracked.symbol, result.filled_quantity, exit_price,
                                                   module=MODULE, fee=result.fee)
        log_trade(MODULE, tracked.symbol, signal.action, exit_price, result.filled_quantity, pnl)
        logger.info(f"Exit: {tracked.symbol} @ {exit_price:,.4f} | P/L: ${pnl or 0:+,.2f} - {signal.reason}")
        return result

    def get_status(self) -> Dict:
        positions = self.portfolio.get_all_positions(module=MODULE)
        return {
            "running": self._running,
            "strategies": list(self.strategies.keys()),
            "open_positions": len(positions),
            "positions": {p.symbol: p.to_dict() for p in positions},
            "risk": self.risk_manager.get_status(),
        }
