"""
Izalith Trading System - Allocator

Corre en vivo las estrategias de portafolio del laboratorio (lab/catalog.py) con EL MISMO
código que se evaluó en el backtest: se llama a la estrategia con datos frescos, se toma
su última decisión (pesos objetivo) y se rebalancea la "bolsa" de esa estrategia.

Cada estrategia tiene su propia bolsa de capital (ALLOCATOR_CAPITAL_USD) y sus posiciones
se registran con módulo "alloc:<clave>", así se puede medir cada una por separado y
compararla contra su backtest.

Cuándo opera:
- cuando la estrategia toma una decisión nueva (cambia la fecha de su última decisión), o
- cuando la bolsa se desvió más de 10 puntos de lo que la estrategia quiere.
Las órdenes de venta van primero, para liberar efectivo.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter
from core.data.feed import UnifiedFeed
from core.execution import execute_order
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger

logger = get_logger("allocator")

DRIFT_LIMIT = 0.10        # rebalancear si un activo se desvía más de 10% del capital de la bolsa
MIN_TRADE_FRACTION = 0.02  # no mover menos del 2% de la bolsa


class AllocatorBot:
    def __init__(self, adapters: Dict[str, BaseAdapter], feed: UnifiedFeed, portfolio: PortfolioTracker,
                 db: Database, config: dict):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.keys: List[str] = list(config.get("allocator_strategies", []))
        self.capital = float(config.get("allocator_capital_usd", 1000.0))
        self.check_seconds = max(60, int(config.get("allocator_check_minutes", 30)) * 60)
        self.dry_run = config.get("dry_run", True)
        # En cripto, órdenes límite que esperan: en Kraken la comisión baja de 0.80% a 0.40%
        self.passive = bool(config.get("allocator_passive_orders", True))
        self.state: Dict[str, Dict] = {}
        self._running = False

    @staticmethod
    def module_name(key: str) -> str:
        return f"alloc:{key}"

    def _validate(self) -> List[str]:
        from lab.catalog import get_spec
        valid = []
        for key in self.keys:
            try:
                spec = get_spec(key)
            except KeyError:
                logger.error(f"Allocator: no existe la estrategia '{key}' en el catálogo")
                continue
            if spec.tipo != "weights" or spec.run is None:
                logger.error(f"Allocator: '{key}' no es una estrategia de portafolio; no se puede correr aquí")
                continue
            valid.append(key)
            self.state[key] = {"nombre": spec.nombre, "nivel": spec.nivel, "capital": self.capital,
                               "status": "esperando primera revisión"}
        return valid

    async def run(self) -> None:
        self.keys = self._validate()
        self._running = True
        logger.info(f"Allocator: {len(self.keys)} estrategias con ${self.capital:,.0f} cada una "
                    f"({'PAPEL' if self.dry_run else 'REAL'})")
        while self._running:
            for key in self.keys:
                try:
                    await self.step(key)
                except Exception as e:
                    self.state[key].update(status="error", error=str(e))
                    logger.error(f"Allocator {key}: {e}")
            await asyncio.sleep(self.check_seconds)

    async def stop(self) -> None:
        self._running = False

    async def _decision(self, key: str) -> Dict:
        """Corre la estrategia del catálogo con datos frescos (en un hilo: pandas y red bloquean)."""
        from lab.catalog import Ctx, get_spec
        spec = get_spec(key)

        def compute():
            out = spec.run(Ctx(refresh=True), **spec.params)
            return out.extra
        return await asyncio.to_thread(compute)

    def _adapter_for(self, symbol: str) -> Optional[BaseAdapter]:
        return self.feed._get_adapter_for_symbol(symbol)

    async def sleeve(self, key: str) -> Dict:
        """Capital, efectivo y posiciones de la bolsa de una estrategia."""
        module = self.module_name(key)
        positions = self.portfolio.get_all_positions(module=module)
        await self.portfolio.update_prices()
        realized = sum(t["pnl"] or 0 for t in await self.db.get_closed_trades(module=module))
        cost = sum(p.cost_basis + p.fees for p in positions)
        cash = self.capital + realized - cost
        value = sum(p.value for p in positions)
        return {"cash": cash, "equity": cash + value, "realized": realized,
                "positions": {p.symbol: p for p in positions}}

    async def step(self, key: str) -> None:
        module = self.module_name(key)
        extra = await self._decision(key)
        if extra.get("perp_cols"):
            self.state[key].update(status="no soportada", error="Necesita perpetuos; el adaptador es sólo spot")
            return
        target: Dict[str, float] = extra.get("target") or {}
        tdate = extra.get("target_date")
        sl = await self.sleeve(key)
        equity = sl["equity"]

        prices = await self.feed.get_prices(sorted(set(target) | set(sl["positions"])))
        current = {s: p.quantity * (prices[s].last if s in prices else p.current_price)
                   for s, p in sl["positions"].items()}
        desired = {s: w * equity for s, w in target.items()}
        symbols = sorted(set(current) | set(desired))
        deltas = {s: desired.get(s, 0.0) - current.get(s, 0.0) for s in symbols}
        max_drift = max((abs(d) / equity for d in deltas.values()), default=0.0) if equity > 0 else 0.0

        applied = await self.db.kv_get(f"{module}:applied")
        new_decision = tdate != applied
        self.state[key].update(
            status="al día", error=None, target=target, target_date=tdate, equity=equity, cash=sl["cash"],
            realized=sl["realized"], drift=max_drift, checked_at=datetime.now(timezone.utc).isoformat(),
            holdings={s: round(v, 2) for s, v in current.items()})

        if not new_decision and max_drift < DRIFT_LIMIT:
            return

        tag = tdate if new_decision else f"{tdate}-drift-{datetime.now(timezone.utc):%Y%m%d%H}"
        min_trade = MIN_TRADE_FRACTION * equity
        pending = False
        # Primero ventas, luego compras
        for s in sorted(symbols, key=lambda x: deltas[x]):
            delta = deltas[s]
            adapter = self._adapter_for(s)
            if not adapter or abs(delta) < max(min_trade, adapter.min_notional(s)):
                continue
            if not adapter.is_market_open():
                pending = True
                continue
            price = prices.get(s)
            if not price or (not self.dry_run and price.simulated):
                pending = True
                continue
            if delta < 0:
                pos = sl["positions"].get(s)
                if not pos:
                    continue
                qty = min(pos.quantity, -delta / price.last)
                if desired.get(s, 0.0) <= 0:
                    qty = pos.quantity   # salida completa: no dejar migajas
                res = await execute_order(self.db, adapter, module, s, "sell", quantity=qty, close_side="long",
                                          purpose=f"{module}:{tag}:{s}:sell",
                                          passive=self.passive and adapter.market == "crypto")
                if res.success and res.filled_quantity > 0:
                    await self.portfolio.reduce_position(s, res.filled_quantity, res.price, module=module, fee=res.fee)
                elif res.status != "duplicate":
                    pending = True
            else:
                cash_now = (await self.sleeve(key))["cash"]
                amount = min(delta, cash_now * 0.995)
                if amount < max(min_trade, adapter.min_notional(s)):
                    continue
                res = await execute_order(self.db, adapter, module, s, "buy", amount=amount,
                                          purpose=f"{module}:{tag}:{s}:buy",
                                          passive=self.passive and adapter.market == "crypto")
                if res.success and res.filled_quantity > 0:
                    await self.portfolio.add_position(symbol=s, market=adapter.market, side="buy",
                                                      quantity=res.filled_quantity, price=res.price, module=module,
                                                      strategy=key, fee=res.fee, simulated=res.simulated,
                                                      client_order_id=res.client_order_id)
                elif res.status != "duplicate":
                    pending = True

        if pending:
            self.state[key]["status"] = "rebalanceo pendiente (mercado cerrado o sin precio)"
        else:
            await self.db.kv_set(f"{module}:applied", tdate)
            self.state[key]["status"] = "rebalanceada"
            logger.info(f"Allocator {key}: rebalanceada a {target} (decisión {tdate})")

    def get_status(self) -> Dict:
        return {"running": self._running, "capital_per_strategy": self.capital, "strategies": self.state}
