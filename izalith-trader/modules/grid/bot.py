"""
Izalith Trading System - Grid Trading Bot

Rejilla de compras escalonadas en un rango:
- Sólo se colocan COMPRAS al crear la rejilla. Cada compra llenada crea su venta un
  escalón arriba, guardando el precio de compra: la venta nunca usa inventario que el bot
  no compró, y el P&L de cada vuelta es (venta - compra) * cantidad - comisiones.
- El escalón tiene que ser mayor que dos comisiones más el spread; si no, cada vuelta pierde
  y el bot se niega a arrancar.
- Los niveles se revisan contra el precio cada 5 segundos y se ejecutan a mercado (en papel
  o en real), con registro de orden para no duplicar.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter
from core.data.feed import UnifiedFeed
from core.execution import execute_order
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade
from config.assets import get_asset

logger = get_logger("grid")

MODULE = "grid"


@dataclass
class GridLevel:
    id: Optional[int]
    price: float
    side: str  # buy, sell
    quantity: float
    status: str = "pending"  # pending, filled
    filled_at: Optional[datetime] = None
    buy_price: Optional[float] = None   # en las ventas: a cuánto se compró lo que se vende

    def to_dict(self) -> Dict:
        return {
            "id": self.id, "price": self.price, "side": self.side, "quantity": self.quantity,
            "status": self.status, "buy_price": self.buy_price,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
        }


class GridBot:
    """Grid trading bot for range-bound markets."""

    def __init__(self, adapters: Dict[str, BaseAdapter], feed: UnifiedFeed, portfolio: PortfolioTracker,
                 db: Database, config: dict):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.config = config
        self.grids: Dict[str, List[GridLevel]] = {}
        self.step: Dict[str, float] = {}
        self._running = False
        self._failures: Dict[str, int] = {}

    def _adapter_for(self, symbol: str) -> Optional[BaseAdapter]:
        try:
            return self.adapters.get(get_asset(symbol).market.value)
        except ValueError:
            return self.adapters.get("crypto")

    def _validate(self, low: float, high: float, levels: int, fee: float, spread_pct: float) -> float:
        if levels < 2 or high <= low:
            raise ValueError("La rejilla necesita al menos 2 niveles y GRID_RANGE_HIGH > GRID_RANGE_LOW")
        step = (high - low) / (levels - 1)
        step_pct = step / low
        min_step = 2 * fee + spread_pct
        if step_pct <= min_step:
            raise ValueError(
                f"Cada escalón ({step_pct:.4%}) es menor que dos comisiones más el spread ({min_step:.4%}): "
                f"cada vuelta perdería dinero. Usa menos niveles o un rango más ancho.")
        return step

    async def initialize(self) -> None:
        total = float(self.config.get("grid_total_investment", 1000.0))
        num_levels = int(self.config.get("grid_levels", 10))
        low = float(self.config.get("grid_range_low", 0.995))
        high = float(self.config.get("grid_range_high", 1.005))
        fee = float(self.config.get("grid_fee_pct", 0.1)) / 100

        for symbol in self.config.get("grid_enabled_assets", ["USDC/USDT"]):
            price = await self.feed.get_price(symbol)
            spread_pct = (price.spread_pct / 100) if price else 0.0
            self.step[symbol] = self._validate(low, high, num_levels, fee, spread_pct)

            existing = await self.db.get_grid_levels(symbol, status="pending")
            if existing:
                self.grids[symbol] = [
                    GridLevel(id=l["id"], price=l["level_price"], side=l["side"], quantity=l["quantity"],
                              status=l["status"], buy_price=l.get("buy_price"))
                    for l in existing
                ]
                logger.info(f"Grid {symbol}: {len(existing)} niveles pendientes recuperados")
            else:
                await self._create_grid(symbol, low, high, num_levels, total)
            await self.db.purge_filled_grid_levels(symbol)

    async def _create_grid(self, symbol: str, low: float, high: float, num_levels: int, total: float) -> None:
        price_data = await self.feed.get_price(symbol)
        if not price_data:
            logger.error(f"Could not get price for {symbol}")
            return
        current = price_data.last
        step = (high - low) / (num_levels - 1)
        per_level = total / num_levels
        levels = []
        for i in range(num_levels):
            level_price = low + i * step
            if level_price >= current:
                continue   # arriba del precio no hay inventario que vender todavía
            qty = per_level / level_price
            level_id = await self.db.insert_grid_level(symbol, level_price, "buy", qty)
            levels.append(GridLevel(id=level_id, price=level_price, side="buy", quantity=qty))
        self.grids[symbol] = levels
        logger.info(f"Grid {symbol}: {len(levels)} compras de {low:.4f} a {current:.4f} "
                    f"(escalón {step:.4f}, ${per_level:,.2f} por nivel)")

    async def run(self) -> None:
        await self.initialize()
        self._running = True
        logger.info(f"Grid bot started with {len(self.grids)} grids")
        while self._running:
            try:
                await self.check_and_execute()
            except Exception as e:
                logger.error(f"Error in grid loop: {e}")
            await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        logger.info("Grid bot stopped")

    async def check_and_execute(self) -> None:
        for symbol, levels in self.grids.items():
            # Después de varios errores seguidos se espera más entre intentos
            fails = self._failures.get(symbol, 0)
            if fails and datetime.now(timezone.utc).second % min(60, 5 * 2 ** min(fails, 4)) >= 5:
                continue
            price_data = await self.feed.get_price(symbol)
            adapter = self._adapter_for(symbol)
            if not price_data or not adapter:
                continue
            if not adapter.is_paper and price_data.simulated:
                continue
            for level in [l for l in levels if l.status == "pending"]:
                if (level.side == "buy" and price_data.ask <= level.price) or \
                   (level.side == "sell" and price_data.bid >= level.price):
                    await self._execute_level(symbol, level, adapter)

    async def _execute_level(self, symbol: str, level: GridLevel, adapter: BaseAdapter) -> None:
        if level.side == "buy":
            quote = adapter.quote_currency(symbol)
            try:
                free = (await adapter.get_balance()).get(quote, 0.0)
            except Exception as e:
                logger.error(f"Grid {symbol}: no se pudo leer el saldo ({e})")
                return
            if free < level.quantity * level.price:
                logger.warning(f"Grid {symbol}: saldo de {quote} insuficiente para el nivel {level.price:.4f}")
                self._failures[symbol] = self._failures.get(symbol, 0) + 1
                return

        result = await execute_order(self.db, adapter, MODULE, symbol, level.side, quantity=level.quantity,
                                     purpose=f"grid:{symbol}:{level.id}",
                                     close_side="long" if level.side == "sell" else None)
        if not result.success or result.filled_quantity <= 0:
            if result.status not in ("duplicate", "en_vuelo"):
                self._failures[symbol] = self._failures.get(symbol, 0) + 1
                logger.error(f"Grid order failed: {result.error}")
            return
        self._failures[symbol] = 0

        level.status = "filled"
        level.filled_at = datetime.now(timezone.utc)
        if level.id:
            await self.db.update_grid_level(level.id, "filled")
        fill = result.price or level.price
        step = self.step.get(symbol, level.price * 0.002)

        if level.side == "buy":
            await self.portfolio.add_position(symbol=symbol, market=adapter.market, side="buy",
                                              quantity=result.filled_quantity, price=fill, module=MODULE,
                                              fee=result.fee, simulated=result.simulated,
                                              client_order_id=result.client_order_id)
            sell_price = level.price + step
            new_id = await self.db.insert_grid_level(symbol, sell_price, "sell", result.filled_quantity, buy_price=fill)
            self.grids[symbol].append(GridLevel(id=new_id, price=sell_price, side="sell",
                                                quantity=result.filled_quantity, buy_price=fill))
            logger.info(f"Grid compra {symbol} @ {fill:.4f} → venta puesta en {sell_price:.4f}")
        else:
            pnl = await self.portfolio.reduce_position(symbol, result.filled_quantity, fill, module=MODULE,
                                                       fee=result.fee)
            buy_price = level.price - step
            new_id = await self.db.insert_grid_level(symbol, buy_price, "buy", level.quantity)
            self.grids[symbol].append(GridLevel(id=new_id, price=buy_price, side="buy", quantity=level.quantity))
            logger.info(f"Grid venta {symbol} @ {fill:.4f} (compra {level.buy_price or 0:.4f}) "
                        f"P&L ${pnl or 0:+.4f} → compra puesta en {buy_price:.4f}")
        log_trade(MODULE, symbol, level.side, fill, result.filled_quantity)
        self.grids[symbol] = [l for l in self.grids[symbol] if l.status == "pending"]

    async def reset_grid(self, symbol: str) -> None:
        await self.db.clear_grid_levels(symbol)
        self.grids.pop(symbol, None)
        await self._create_grid(
            symbol,
            float(self.config.get("grid_range_low", 0.995)), float(self.config.get("grid_range_high", 1.005)),
            int(self.config.get("grid_levels", 10)), float(self.config.get("grid_total_investment", 1000.0)))
        logger.info(f"Grid reset for {symbol}")

    async def get_status(self) -> Dict:
        status = {"running": self._running, "grids": {}}
        for symbol, levels in self.grids.items():
            closed = await self.db.get_closed_trades(module=MODULE)
            status["grids"][symbol] = {
                "pending_buys": len([l for l in levels if l.side == "buy"]),
                "pending_sells": len([l for l in levels if l.side == "sell"]),
                "realized_pnl": sum(t["pnl"] or 0 for t in closed if t["symbol"] == symbol),
                "levels": [l.to_dict() for l in levels],
            }
        return status
