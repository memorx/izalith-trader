"""
Izalith Trading System - Portfolio Tracker

Las posiciones se llevan por (módulo, símbolo): DCA y el scalper pueden tener BTC a la
vez sin pisarse. La base de datos es la fuente de verdad; al arrancar, load() reconstruye
las posiciones abiertas y el efectivo de papel a partir de ella.

Efectivo de papel (DRY_RUN):
    efectivo = capital inicial + P&L realizado - costo de los largos abiertos - comisiones de entrada
Los cortos (perpetuos, forex) no consumen efectivo: sólo margen.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from core.utils.logger import get_logger
from core.utils.database import Database, normalize_side
from core.data.feed import UnifiedFeed

logger = get_logger("portfolio")


@dataclass
class Position:
    """Una posición abierta de un módulo."""
    symbol: str
    market: str
    side: str  # long, short
    quantity: float
    entry_price: float
    module: str = "manual"
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float = 0.0
    trade_id: Optional[int] = None
    fees: float = 0.0
    strategy: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    simulated: bool = False

    @property
    def key(self) -> Tuple[str, str]:
        return (self.module, self.symbol)

    @property
    def value(self) -> float:
        """Valor de mercado (para cortos, el nocional)."""
        return self.quantity * (self.current_price or self.entry_price)

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        price = self.current_price or self.entry_price
        sign = 1 if self.side == "long" else -1
        return sign * (price - self.entry_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.unrealized_pnl / self.cost_basis) * 100 if self.cost_basis else 0.0

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'market': self.market,
            'module': self.module,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'current_price': self.current_price,
            'value': self.value,
            'cost_basis': self.cost_basis,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_pnl_pct': self.unrealized_pnl_pct,
            'strategy': self.strategy,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'simulated': self.simulated,
        }


class PortfolioTracker:
    """Posiciones, efectivo de papel y P&L, respaldados en la base de datos."""

    def __init__(self, db: Database, feed: UnifiedFeed, initial_cash: float = 10000.0):
        self.db = db
        self.feed = feed
        self.initial_cash = initial_cash
        self.positions: Dict[Tuple[str, str], Position] = {}
        self.cash_usd: float = initial_cash
        self._last_snapshot_time: Optional[datetime] = None

    # ── Carga ──

    async def load(self) -> None:
        """Reconstruye posiciones abiertas y efectivo desde la base de datos."""
        self.positions = {}
        for t in await self.db.get_open_trades():
            ts = datetime.fromisoformat(t["timestamp"]).replace(tzinfo=timezone.utc)
            pos = Position(
                symbol=t["symbol"], market=t["market"], side=normalize_side(t["side"]),
                quantity=float(t["quantity"]), entry_price=float(t["entry_price"]),
                module=t["module"], entry_time=ts, current_price=float(t["entry_price"]),
                trade_id=t["id"], fees=float(t.get("fees") or 0), strategy=t.get("strategy"),
                stop_loss=t.get("stop_loss"), take_profit=t.get("take_profit"),
                simulated=bool(t.get("simulated")),
            )
            prev = self.positions.get(pos.key)
            if prev is not None:
                if prev.side == pos.side and prev.trade_id and pos.trade_id:
                    # Dos trades abiertos del mismo módulo y símbolo (datos viejos): se juntan en uno
                    await self.db.merge_open_trades(prev.trade_id, pos.trade_id)
                    qty = prev.quantity + pos.quantity
                    prev.entry_price = (prev.cost_basis + pos.cost_basis) / qty
                    prev.quantity = qty
                    prev.current_price = prev.entry_price
                    prev.fees += pos.fees
                    logger.warning(f"Trades abiertos duplicados para {pos.key}: se juntaron en el trade {prev.trade_id}")
                    continue
                logger.error(f"Trades abiertos en lados contrarios para {pos.key}; se usa el más reciente. Revísalo.")
            self.positions[pos.key] = pos

        realized = await self.db.get_realized_pnl_total()
        long_cost = sum(p.cost_basis for p in self.positions.values() if p.side == "long")
        open_fees = sum(p.fees for p in self.positions.values())
        self.cash_usd = self.initial_cash + realized - long_cost - open_fees
        if self.positions:
            logger.info(f"Portafolio cargado: {len(self.positions)} posiciones abiertas, "
                        f"efectivo de papel ${self.cash_usd:,.2f}")

    # ── Operaciones ──

    async def add_position(
        self,
        symbol: str,
        market: str,
        side: str,
        quantity: float,
        price: float,
        module: str = "manual",
        strategy: Optional[str] = None,
        fee: float = 0.0,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        simulated: bool = False,
        client_order_id: Optional[str] = None,
    ) -> Optional[Position]:
        """
        Registra una entrada. `side` puede ser buy/sell o long/short.

        Mismo lado que la posición abierta del módulo = promedia. Lado contrario = reduce
        (y si sobra, abre del otro lado).
        """
        pos_side = normalize_side(side)
        key = (module, symbol)
        existing = self.positions.get(key)

        if existing and existing.side != pos_side:
            closing = min(quantity, existing.quantity)
            await self.reduce_position(symbol, closing, price, module=module, fee=fee * closing / quantity)
            remaining = quantity - closing
            if remaining <= 1e-12:
                return None
            fee = fee * remaining / quantity
            quantity = remaining
            existing = None

        if existing:
            total_qty = existing.quantity + quantity
            existing.entry_price = (existing.entry_price * existing.quantity + price * quantity) / total_qty
            existing.quantity = total_qty
            existing.fees += fee
            if stop_loss is not None:
                existing.stop_loss = stop_loss
            if take_profit is not None:
                existing.take_profit = take_profit
            await self.db.update_open_trade(existing.trade_id, existing.entry_price, existing.quantity,
                                            existing.fees, stop_loss, take_profit)
            pos = existing
        else:
            trade_id = await self.db.insert_trade(
                module=module, symbol=symbol, market=market, side=pos_side, entry_price=price,
                quantity=quantity, strategy=strategy, fees=fee, stop_loss=stop_loss,
                take_profit=take_profit, simulated=simulated, client_order_id=client_order_id)
            pos = Position(symbol=symbol, market=market, side=pos_side, quantity=quantity, entry_price=price,
                           module=module, current_price=price, trade_id=trade_id, fees=fee, strategy=strategy,
                           stop_loss=stop_loss, take_profit=take_profit, simulated=simulated)
            self.positions[key] = pos

        if pos_side == "long":
            self.cash_usd -= quantity * price + fee
        else:
            self.cash_usd -= fee

        logger.info(f"[{module}] {pos_side.upper()} {quantity:g} {symbol} @ {price:,.4f}")
        return pos

    async def reduce_position(self, symbol: str, quantity: float, exit_price: float,
                              module: str = "manual", fee: float = 0.0) -> Optional[float]:
        """Cierra una parte (o todo) de la posición del módulo. Regresa el P&L realizado neto."""
        key = (module, symbol)
        pos = self.positions.get(key)
        if not pos:
            logger.warning(f"No hay posición de {module} en {symbol} para cerrar")
            return None

        qty = min(quantity, pos.quantity)
        share = qty / pos.quantity if pos.quantity else 1.0
        pnl = await self.db.close_trade(pos.trade_id, exit_price, qty, exit_fee=fee)
        if pnl is None:
            return None

        # La comisión de entrada ya se descontó del efectivo al abrir; aquí sólo entra la de salida
        if pos.side == "long":
            self.cash_usd += qty * exit_price - fee
        else:
            # El corto no consumió efectivo: al cerrarse entra la ganancia (o sale la pérdida)
            self.cash_usd += (pos.entry_price - exit_price) * qty - fee

        pos.quantity -= qty
        pos.fees -= pos.fees * share
        if pos.quantity <= max(1e-12, 1e-9 * qty):
            del self.positions[key]

        logger.info(f"[{module}] cierre {qty:g} {symbol} @ {exit_price:,.4f} → P&L ${pnl:+,.2f}")
        return pnl

    async def close_position(self, symbol: str, exit_price: float, module: str = "manual",
                             fee: float = 0.0) -> Optional[float]:
        pos = self.positions.get((module, symbol))
        if not pos:
            logger.warning(f"No hay posición de {module} en {symbol} para cerrar")
            return None
        return await self.reduce_position(symbol, pos.quantity, exit_price, module=module, fee=fee)

    # ── Consultas ──

    async def update_prices(self) -> None:
        if not self.positions:
            return
        symbols = sorted({p.symbol for p in self.positions.values()})
        prices = await self.feed.get_prices(symbols)
        for pos in self.positions.values():
            pd_ = prices.get(pos.symbol)
            if pd_:
                pos.current_price = pd_.last

    def get_position(self, symbol: str, module: Optional[str] = None) -> Optional[Position]:
        if module:
            return self.positions.get((module, symbol))
        for pos in self.positions.values():
            if pos.symbol == symbol:
                return pos
        return None

    def get_all_positions(self, module: Optional[str] = None) -> List[Position]:
        return [p for p in self.positions.values() if module is None or p.module == module]

    def get_positions_value(self) -> float:
        """Valor de los largos (los cortos se cuentan por su P&L, no por su nocional)."""
        return sum(p.value for p in self.positions.values() if p.side == "long")

    def get_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    def get_cash_usd(self) -> float:
        return self.cash_usd

    def held_quantity(self, symbol: str) -> float:
        """Cantidad en largo de un símbolo sumando todos los módulos (lo que hay en la cuenta)."""
        return sum(p.quantity for p in self.positions.values() if p.symbol == symbol and p.side == "long")

    def get_total_value_usd(self) -> float:
        shorts_pnl = sum(p.unrealized_pnl for p in self.positions.values() if p.side == "short")
        return self.cash_usd + self.get_positions_value() + shorts_pnl

    async def get_realized_pnl_today(self) -> float:
        return await self.db.get_daily_pnl()

    async def take_snapshot(self) -> None:
        await self.update_prices()
        await self.db.insert_portfolio_snapshot(
            total_value_usd=self.get_total_value_usd(),
            cash_usd=self.get_cash_usd(),
            positions_value_usd=self.get_positions_value(),
            unrealized_pnl=self.get_unrealized_pnl(),
            realized_pnl_today=await self.get_realized_pnl_today(),
        )
        self._last_snapshot_time = datetime.now(timezone.utc)

    async def get_summary(self) -> Dict:
        return {
            "total_value_usd": self.get_total_value_usd(),
            "initial_cash_usd": self.initial_cash,
            "cash_usd": self.get_cash_usd(),
            "positions_value_usd": self.get_positions_value(),
            "unrealized_pnl": self.get_unrealized_pnl(),
            "realized_pnl_today": await self.get_realized_pnl_today(),
            "realized_pnl_total": await self.db.get_realized_pnl_total(),
            "position_count": len(self.positions),
            "positions": [p.to_dict() for p in self.positions.values()],
        }
