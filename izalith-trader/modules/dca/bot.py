"""
Izalith Trading System - DCA Bot

Compra un monto fijo cada periodo. Reglas:
- La configuración (.env) manda: si cambias monto o frecuencia, se aplica al reiniciar.
- La hora se interpreta en TRADING_TIMEZONE (por defecto, Ciudad de México).
- Cada turno de compra tiene un propósito único ("dca:SPY:2026-10-27T09:00"): si el bot se
  reinicia a media compra, no vuelve a comprar el mismo turno.
- Lo que se registra es lo que se llenó (cantidad neta de comisión y precio real), no el cálculo.
- Las acciones se compran por monto (fracciones), así $50 de VOO no falla por decimales.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from core.adapters.base import BaseAdapter
from core.data.feed import UnifiedFeed
from core.execution import execute_order
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade
from config.assets import get_asset

logger = get_logger("dca")

MODULE = "dca"
WEEKDAY_MAP = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
FREQUENCIES = ("hourly", "daily", "weekly", "biweekly", "monthly")


def next_run(frequency: str, hour: int, day_of_week: int, tz: ZoneInfo,
             after: Optional[datetime] = None) -> datetime:
    """Siguiente turno estrictamente después de `after`, en UTC."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"DCA_FREQUENCY desconocida: '{frequency}'. Opciones: {', '.join(FREQUENCIES)}")
    now = (after or datetime.now(timezone.utc)).astimezone(tz)

    if frequency == "hourly":
        nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif frequency == "daily":
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    elif frequency in ("weekly", "biweekly"):
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        nxt += timedelta(days=(day_of_week - now.weekday()) % 7)
        if nxt <= now:
            nxt += timedelta(days=7)
        if frequency == "biweekly" and after is not None:
            nxt += timedelta(days=7)
    else:  # monthly: primer día del mes siguiente (o de este mes si aún no pasa)
        nxt = now.replace(day=1, hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            year, month = (nxt.year + 1, 1) if nxt.month == 12 else (nxt.year, nxt.month + 1)
            nxt = nxt.replace(year=year, month=month)
    return nxt.astimezone(timezone.utc)


@dataclass
class DCAOrder:
    symbol: str
    amount_usd: float
    frequency: str
    next_execution: datetime
    total_invested: float = 0.0
    total_quantity: float = 0.0
    last_execution: Optional[datetime] = None

    @property
    def avg_price(self) -> float:
        return self.total_invested / self.total_quantity if self.total_quantity else 0.0


class DCABot:
    """Dollar Cost Averaging bot."""

    def __init__(self, adapters: Dict[str, BaseAdapter], feed: UnifiedFeed, portfolio: PortfolioTracker,
                 db: Database, config: dict):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.config = config
        self.orders: Dict[str, DCAOrder] = {}
        self.tz = ZoneInfo(config.get("trading_timezone", "America/Mexico_City"))
        self.frequency = str(config.get("dca_frequency", "weekly")).strip().lower()
        self.hour = int(config.get("dca_hour", 9))
        day = str(config.get("dca_day_of_week", "monday")).strip().lower()
        if day not in WEEKDAY_MAP:
            raise ValueError(f"DCA_DAY_OF_WEEK desconocido: '{day}'")
        self.day_of_week = WEEKDAY_MAP[day]
        next_run(self.frequency, self.hour, self.day_of_week, self.tz)  # valida la frecuencia al arrancar
        self._running = False

    def _next(self, after: Optional[datetime] = None) -> datetime:
        # Si el bot estuvo apagado varios turnos no se compran todos de golpe: se sigue desde ahora
        if after is not None:
            after = max(after, datetime.now(timezone.utc))
        return next_run(self.frequency, self.hour, self.day_of_week, self.tz, after)

    async def initialize(self) -> None:
        amount = float(self.config.get("dca_amount_usd", 50.0))
        db_orders = {o["symbol"]: o for o in await self.db.get_dca_orders()}

        for symbol in self.config.get("dca_enabled_assets", []):
            row = db_orders.get(symbol)
            next_exec = None
            if row and row.get("next_execution"):
                next_exec = datetime.fromisoformat(row["next_execution"])
                if next_exec.tzinfo is None:
                    next_exec = next_exec.replace(tzinfo=timezone.utc)
            changed = bool(row) and (row["frequency"] != self.frequency or abs(row["amount_usd"] - amount) > 1e-9)
            if next_exec is None or changed:
                next_exec = self._next()
            # La configuración actual manda sobre lo guardado
            await self.db.upsert_dca_order(symbol, amount, self.frequency, next_exec.replace(tzinfo=None).isoformat())
            self.orders[symbol] = DCAOrder(
                symbol=symbol, amount_usd=amount, frequency=self.frequency, next_execution=next_exec,
                total_invested=float(row.get("total_invested") or 0) if row else 0.0,
                total_quantity=float(row.get("total_quantity") or 0) if row else 0.0)
            local = next_exec.astimezone(self.tz).strftime('%Y-%m-%d %H:%M')
            logger.info(f"DCA {symbol}: ${amount:g} {self.frequency}, siguiente compra {local} ({self.tz.key})")

    async def run(self) -> None:
        await self.initialize()
        self._running = True
        logger.info(f"DCA bot started with {len(self.orders)} orders")
        while self._running:
            try:
                await self.execute_scheduled_buys()
            except Exception as e:
                logger.error(f"Error in DCA loop: {e}")
            await asyncio.sleep(60)

    async def stop(self) -> None:
        self._running = False
        logger.info("DCA bot stopped")

    async def execute_scheduled_buys(self) -> None:
        now = datetime.now(timezone.utc)
        for order in self.orders.values():
            if now >= order.next_execution:
                try:
                    await self._execute_buy(order)
                except Exception as e:
                    logger.error(f"Failed to execute DCA buy for {order.symbol}: {e}")

    def _adapter_for(self, symbol: str) -> Optional[BaseAdapter]:
        try:
            return self.adapters.get(get_asset(symbol).market.value)
        except ValueError:
            return self.adapters.get("crypto")

    async def _execute_buy(self, order: DCAOrder) -> None:
        symbol = order.symbol
        adapter = self._adapter_for(symbol)
        if not adapter:
            logger.error(f"No adapter found for {symbol}")
            return
        if not adapter.is_market_open():
            return  # se reintenta cada minuto hasta que abra el mercado

        slot = order.next_execution.replace(tzinfo=None).isoformat(timespec="minutes")
        result = await execute_order(self.db, adapter, MODULE, symbol, "buy", amount=order.amount_usd,
                                     purpose=f"dca:{symbol}:{slot}")

        if result.status == "duplicate":
            # Este turno ya se mandó antes de un reinicio: se pasa al siguiente sin volver a comprar
            order.next_execution = self._next(order.next_execution)
            await self.db.set_dca_next_execution(symbol, order.next_execution.replace(tzinfo=None).isoformat())
            return
        if not result.success or result.filled_quantity <= 0:
            logger.error(f"DCA buy failed for {symbol}: {result.error or result.status}")
            return

        qty = result.filled_quantity
        price = result.price
        cost = qty * price + result.fee
        order.total_invested += cost
        order.total_quantity += qty
        order.last_execution = datetime.now(timezone.utc)
        order.next_execution = self._next(order.next_execution)
        await self.db.update_dca_execution(symbol, qty, cost, order.next_execution.replace(tzinfo=None).isoformat())

        market = adapter.market
        await self.portfolio.add_position(symbol=symbol, market=market, side="buy", quantity=qty, price=price,
                                          module=MODULE, fee=result.fee, simulated=result.simulated,
                                          client_order_id=result.client_order_id)
        log_trade(MODULE, symbol, "buy", price, qty)
        logger.info(f"DCA {symbol}: compró {qty:.6f} @ {price:,.2f} (total ${order.total_invested:,.2f}, "
                    f"promedio ${order.avg_price:,.2f})")

    def get_status(self) -> Dict:
        return {
            "running": self._running,
            "timezone": self.tz.key,
            "orders": {
                symbol: {
                    "amount_usd": o.amount_usd,
                    "frequency": o.frequency,
                    "next_execution": o.next_execution.isoformat(),
                    "total_invested": o.total_invested,
                    "total_quantity": o.total_quantity,
                    "avg_price": o.avg_price,
                }
                for symbol, o in self.orders.items()
            },
        }
