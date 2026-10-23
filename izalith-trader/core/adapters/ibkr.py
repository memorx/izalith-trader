"""
Izalith Trading System - Interactive Brokers Adapter (ib_async)

Necesita TWS o IB Gateway abierto en la computadora, con la API habilitada:
    IB Gateway: 4002 = cuenta de papel, 4001 = cuenta real
    TWS:        7497 = cuenta de papel, 7496 = cuenta real

Dos formas de practicar:
- DRY_RUN=true: el bot simula las órdenes con precios de IBKR (como con los demás adaptadores).
- DRY_RUN=false con el puerto de PAPEL (4002): las órdenes van al motor de papel de IBKR, que
  simula llenados, comisiones y rechazos reales. Es la mejor práctica antes del dinero real.

Montos chicos: con 100 dólares no alcanza para una acción de SPY. Las compras por monto usan
fracciones de acción (IBKR las permite en acciones y ETFs de EE.UU. si la cuenta tiene el
permiso de fracciones); si la orden fraccionada se rechaza, se intenta con acciones enteras.
Comisión IBKR Pro Fixed: 0.005 USD por acción, mínimo 1 USD por orden.
"""
import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytz

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position, new_client_order_id
from core.utils.logger import get_logger

logger = get_logger("ibkr")

BAR = {"1m": ("1 min", 60), "1h": ("1 hour", 3600), "1d": ("1 day", 86400), "1w": ("1 week", 604800)}
DONE = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and x > 0


class IBKRAdapter(BaseAdapter):
    market = "stock"
    supports_short = False
    sim_prices = {"VTI": 280.0, "VOO": 520.0, "SPY": 570.0, "QQQ": 480.0, "IEF": 95.0, "AGG": 98.0}
    sim_vol_per_hour = 0.003
    sim_fee = 0.00002
    sim_min_fee = 1.0

    def __init__(self, host: str = "127.0.0.1", port: int = 4002, client_id: int = 17,
                 dry_run: bool = True, allow_simulation: bool = False, simulate: bool = False, ib=None):
        super().__init__(dry_run, allow_simulation, simulate)
        self.host, self.port, self.client_id = host, int(port), int(client_id)
        self.ib = ib
        self._contracts: Dict[str, object] = {}
        self.account = ""

    @property
    def display_name(self) -> str:
        kind = "papel" if self.port in (4002, 7497) else "REAL"
        return f"Acciones (IBKR, cuenta de {kind})"

    # ── Conexión ──

    async def connect(self) -> bool:
        if self.force_simulation:
            return self._enter_simulation("simulación pedida")
        try:
            if self.ib is None:
                from ib_async import IB
                self.ib = IB()
            await asyncio.wait_for(self.ib.connectAsync(self.host, self.port, clientId=self.client_id), timeout=15)
        except ImportError:
            return self._enter_simulation("ib_async no está instalado")
        except Exception as e:
            return self._enter_simulation(f"IB Gateway/TWS no contestó en {self.host}:{self.port} ({e})")
        # 3 = datos con retraso si no hay suscripción en tiempo real (en vez de no recibir nada)
        try:
            self.ib.reqMarketDataType(3)
        except Exception:
            pass
        try:
            accounts = self.ib.managedAccounts()
            self.account = accounts[0] if accounts else ""
        except Exception:
            self.account = ""
        self.connected = True
        self._simulation_mode = False
        if self.account.startswith("DU") and self.port not in (4002, 7497):
            logger.warning(f"La cuenta {self.account} es de papel pero el puerto {self.port} es de cuenta real")
        logger.info(f"IBKR conectado ({self.display_name}, cuenta {self.account or '?'})")
        return True

    async def disconnect(self) -> None:
        if self.ib is not None:
            try:
                self.ib.disconnect()
            except Exception:
                pass
        self.connected = False
        logger.info("IBKR disconnected")

    async def _contract(self, symbol: str):
        if symbol not in self._contracts:
            from ib_async import Stock
            c = Stock(symbol, "SMART", "USD")
            try:
                q = await self.ib.qualifyContractsAsync(c)
                c = q[0] if q else c
            except Exception:
                pass
            self._contracts[symbol] = c
        return self._contracts[symbol]

    # ── Datos ──

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        if self._simulation_mode:
            return self._sim_price(symbol)
        if not self.connected:
            return None
        try:
            tickers = await self.ib.reqTickersAsync(await self._contract(symbol))
            t = tickers[0] if tickers else None
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precio de {symbol} en IBKR: {e}")
            return None
        if t is None:
            return None
        last = t.last if _ok(t.last) else (t.close if _ok(getattr(t, "close", None)) else None)
        bid = t.bid if _ok(t.bid) else last
        ask = t.ask if _ok(t.ask) else last
        if not _ok(last):
            if _ok(bid) and _ok(ask):
                last = (bid + ask) / 2
            else:
                return None
        return PriceData(symbol=symbol, market=self.market, timestamp=datetime.now(timezone.utc),
                         bid=bid, ask=ask, last=last)

    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        out = []
        for s in symbols:
            p = await self.get_price(s)
            if p:
                out.append(p)
        return out

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[OHLCV]:
        if self._simulation_mode:
            return self._sim_ohlcv(symbol, limit)
        if not self.connected:
            return []
        bar, seconds = BAR.get(timeframe, BAR["1h"])
        days = {"1m": 2, "1h": limit // 6 + 10, "1d": int(limit * 1.5) + 10, "1w": limit * 7 + 14}.get(timeframe, 30)
        duration = f"{days} D" if days <= 365 else f"{math.ceil(days / 365)} Y"
        try:
            bars = await self.ib.reqHistoricalDataAsync(await self._contract(symbol), endDateTime="",
                                                        durationStr=duration, barSizeSetting=bar,
                                                        whatToShow="TRADES", useRTH=True, formatDate=2)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin velas de {symbol} en IBKR: {e}")
            return []
        now = datetime.now(timezone.utc)
        out = []
        for b in bars or []:
            ts = b.date if isinstance(b.date, datetime) else datetime.combine(b.date, datetime.min.time())
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if timeframe != "1d" and ts + timedelta(seconds=seconds) > now:
                continue
            out.append(OHLCV(timestamp=ts, open=b.open, high=b.high, low=b.low, close=b.close, volume=float(b.volume)))
        return out[-limit:]

    # ── Órdenes ──

    def amount_to_precision(self, symbol: str, quantity: float) -> float:
        return math.floor(quantity * 1e4) / 1e4

    def min_notional(self, symbol: str) -> float:
        return 1.0

    def _result(self, symbol: str, side: str, quantity: float, trade, cid: str) -> OrderResult:
        st = trade.orderStatus
        status = st.status or ""
        filled = float(st.filled or 0)
        fee = 0.0
        for f in getattr(trade, "fills", []) or []:
            rep = getattr(f, "commissionReport", None)
            if rep is not None and _ok(getattr(rep, "commission", None)):
                fee += float(rep.commission)
        ok = status not in ("Cancelled", "ApiCancelled", "Inactive") or filled > 0
        return OrderResult(success=ok, order_id=str(trade.order.orderId), client_order_id=cid, symbol=symbol,
                           side=side, quantity=quantity, price=float(st.avgFillPrice or 0), filled_quantity=filled,
                           status=status.lower() or "unknown", fee=fee,
                           error=None if ok else f"IBKR: {status} {getattr(trade, 'log', [])[-1:]}")

    async def _place(self, symbol: str, side: str, quantity: float, cid: str, limit_price: Optional[float] = None,
                     wait_s: float = 30) -> OrderResult:
        from ib_async import LimitOrder, MarketOrder
        action = "BUY" if side == "buy" else "SELL"
        order = LimitOrder(action, quantity, round(limit_price, 2)) if limit_price else MarketOrder(action, quantity)
        order.orderRef = cid
        order.tif = "DAY"
        try:
            trade = self.ib.placeOrder(await self._contract(symbol), order)
        except Exception as e:
            return OrderResult(success=False, error=str(e), client_order_id=cid)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait_s
        while trade.orderStatus.status not in DONE and loop.time() < deadline and not limit_price:
            await asyncio.sleep(0.5)
        logger.info(f"IBKR {action} {quantity} {symbol}: {trade.orderStatus.status} ({cid})")
        return self._result(symbol, side, quantity, trade, cid)

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                 client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        qty = self.amount_to_precision(symbol, quantity)
        if self.dry_run or self._simulation_mode:
            p = await self.get_price(symbol)
            if not p:
                return OrderResult(success=False, error="Sin precio", client_order_id=cid, simulated=True)
            px = p.ask if side == "buy" else p.bid
            result = self._simulate_order(symbol, side, qty, px, cid)
            if result.success:
                logger.info(f"[PAPEL] Mercado {side} {qty:.4f} {symbol} @ {px:.2f}")
            return result
        if not self.connected:
            return OrderResult(success=False, error="IBKR no está conectado", client_order_id=cid)
        result = await self._place(symbol, side, qty, cid)
        whole = math.floor(qty)
        if not result.success and result.filled_quantity == 0 and whole >= 1 and whole != qty:
            # La cuenta puede no tener permiso de fracciones: se intenta con acciones enteras
            logger.warning(f"Orden fraccionada de {symbol} rechazada; se intenta con {whole} acciones enteras")
            result = await self._place(symbol, side, float(whole), f"{cid}-e")
            result.client_order_id = cid
        return result

    async def place_notional_order(self, symbol: str, side: str, amount: float,
                                   client_order_id: Optional[str] = None) -> OrderResult:
        p = await self.get_price(symbol)
        if not p:
            return OrderResult(success=False, error="Sin precio", client_order_id=client_order_id or "")
        ref = (p.ask if side == "buy" else p.bid) or p.last
        # Se deja aire para la comisión mínima de 1 dólar
        qty = self.amount_to_precision(symbol, max(0.0, amount - 1.0) / ref)
        if qty <= 0:
            return OrderResult(success=False, error=f"{amount:.2f} USD no alcanza para {symbol} @ {ref:.2f}",
                               client_order_id=client_order_id or "")
        return await self.place_market_order(symbol, side, qty, client_order_id)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                                client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        qty = self.amount_to_precision(symbol, quantity)
        if self.dry_run or self._simulation_mode:
            return self._simulate_order(symbol, side, qty, price, cid)
        if not self.connected:
            return OrderResult(success=False, error="IBKR no está conectado", client_order_id=cid)
        return await self._place(symbol, side, qty, cid, limit_price=price)

    async def cancel_order(self, order_id: str, symbol: str = "") -> bool:
        if self.dry_run or self._simulation_mode:
            return True
        for t in self.ib.trades():
            if str(t.order.orderId) == str(order_id):
                self.ib.cancelOrder(t.order)
                return True
        return False

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResult]:
        if self.dry_run or self._simulation_mode or not self.connected:
            return None
        refs = {client_order_id, f"{client_order_id}-e"}
        for t in self.ib.trades():
            if getattr(t.order, "orderRef", "") in refs:
                return self._result(symbol, t.order.action.lower(), float(t.order.totalQuantity), t, client_order_id)
        try:
            fills = await self.ib.reqExecutionsAsync()
        except Exception:
            fills = []
        mine = [f for f in fills if getattr(f.execution, "orderRef", "") in refs]
        if not mine:
            return None
        qty = sum(float(f.execution.shares) for f in mine)
        avg = sum(float(f.execution.shares) * float(f.execution.price) for f in mine) / qty
        side = "buy" if mine[0].execution.side.upper().startswith("B") else "sell"
        return OrderResult(success=True, order_id=str(mine[0].execution.orderId), client_order_id=client_order_id,
                           symbol=symbol, side=side, quantity=qty, price=avg, filled_quantity=qty, status="filled")

    # ── Cuenta ──

    async def get_balance(self) -> Dict[str, float]:
        if self.dry_run or self._simulation_mode:
            return dict(self._simulated_balance)
        if not self.connected:
            raise RuntimeError("IBKR no está conectado")
        rows = await self.ib.accountSummaryAsync()
        for tag in ("TotalCashValue", "AvailableFunds"):
            for r in rows:
                if r.tag == tag and r.currency in ("USD", "BASE"):
                    return {"USD": float(r.value)}
        raise RuntimeError("IBKR no regresó el efectivo en USD")

    async def get_positions(self) -> List[Position]:
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())
        if not self.connected:
            raise RuntimeError("IBKR no está conectado")
        out = []
        for p in self.ib.positions():
            qty = float(p.position)
            if qty == 0:
                continue
            out.append(Position(symbol=p.contract.symbol, side="long" if qty > 0 else "short",
                                quantity=abs(qty), entry_price=float(p.avgCost)))
        return out

    def is_market_open(self) -> bool:
        """NYSE de lunes a viernes 9:30-16:00 hora de Nueva York (no considera feriados)."""
        now = datetime.now(pytz.timezone("America/New_York"))
        if now.weekday() >= 5:
            return False
        return now.replace(hour=9, minute=30, second=0, microsecond=0) <= now <= now.replace(hour=16, minute=0, second=0, microsecond=0)
