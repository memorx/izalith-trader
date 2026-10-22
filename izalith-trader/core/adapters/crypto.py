"""
Izalith Trading System - Crypto Adapter (ccxt: Binance, Kraken, Bitso, Bybit, OKX...)

El exchange se elige con CRYPTO_EXCHANGE (por defecto binance). Desde México lo natural es
Binance o Bitso; desde Canadá, Kraken (Binance, Bybit y OKX no operan ahí).

Usa dos clientes del mismo exchange:
- datos: público de producción, siempre (precios reales aunque se opere en testnet)
- operación: la cuenta con llaves (testnet o real), sólo si hay llaves

Los símbolos del bot son los del laboratorio (BTC/USDT). Si el exchange no tiene ese par,
se usa el equivalente en USD o USDC (Kraken: BTC/USD).

Si Binance no contesta, no se inventan precios: se regresa None o [] salvo que la
simulación se haya pedido o permitido (ver BaseAdapter).
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position, new_client_order_id
from core.utils.logger import get_logger

logger = get_logger("crypto")

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}


class CryptoAdapter(BaseAdapter):
    """Binance crypto adapter using ccxt."""

    market = "crypto"
    supports_short = False   # spot: no se puede vender lo que no se tiene
    sim_prices = {
        "BTC/USDT": 67500.0, "ETH/USDT": 3450.0, "SOL/USDT": 145.0,
        "DOGE/USDT": 0.12, "USDC/USDT": 1.0001,
    }
    sim_vol_per_hour = 0.005
    sim_fee = 0.001

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        testnet: bool = True,
        dry_run: bool = True,
        allow_simulation: bool = False,
        simulate: bool = False,
        exchange_id: str = "binance",
    ):
        super().__init__(dry_run, allow_simulation, simulate)
        self.api_key = api_key
        self.secret = secret
        self.testnet = testnet
        self.exchange_id = (exchange_id or "binance").lower()
        self.exchange_name = self.exchange_id.capitalize()
        self._data = None
        self._trade = None
        self._symbol_map: Dict[str, str] = {}

    @property
    def display_name(self) -> str:
        return f"Cripto ({self.exchange_name})"

    def resolve_symbol(self, symbol: str) -> str:
        """Par del bot -> par del exchange (BTC/USDT -> BTC/USD en Kraken)."""
        if symbol in self._symbol_map:
            return self._symbol_map[symbol]
        markets = getattr(self._data, "markets", None) or {}
        if not markets:
            return symbol
        resolved = symbol
        if symbol not in markets and "/" in symbol:
            base = symbol.split("/", 1)[0]
            for alt in ("USDT", "USD", "USDC"):
                if f"{base}/{alt}" in markets:
                    resolved = f"{base}/{alt}"
                    break
        self._symbol_map[symbol] = resolved
        return resolved

    # ── Conexión ──

    async def connect(self) -> bool:
        if self.force_simulation:
            return self._enter_simulation("simulación pedida")
        try:
            import ccxt.async_support as ccxt
        except ImportError:
            return self._enter_simulation("ccxt no está instalado")

        common = {"enableRateLimit": True, "timeout": 10000}
        exchange_cls = getattr(ccxt, self.exchange_id, None)
        if exchange_cls is None:
            return self._enter_simulation(f"ccxt no conoce el exchange '{self.exchange_id}'")
        try:
            self._data = exchange_cls(dict(common))
            await asyncio.wait_for(self._data.load_markets(), timeout=20.0)
        except Exception as e:
            await self._close_clients()
            return self._enter_simulation(f"{self.exchange_name} no contestó ({e.__class__.__name__}: {e})")

        if self.api_key and self.secret:
            try:
                self._trade = exchange_cls({**common, "apiKey": self.api_key, "secret": self.secret})
                if self.testnet:
                    self._trade.set_sandbox_mode(True)
                await asyncio.wait_for(self._trade.load_markets(), timeout=20.0)
            except Exception as e:
                logger.error(f"Cuenta de {self.exchange_name} no disponible: {e}. Sólo habrá datos, no operación real.")
                if self._trade:
                    try:
                        await self._trade.close()
                    except Exception:
                        pass
                self._trade = None

        self.connected = True
        self._simulation_mode = False
        mode = "testnet" if self.testnet else "REAL"
        auth = f"cuenta {mode}" if self._trade else "sólo datos públicos"
        logger.info(f"{self.exchange_name} conectado ({auth})")
        return True

    async def _close_clients(self):
        for client in (self._data, self._trade):
            if client:
                try:
                    await client.close()
                except Exception:
                    pass
        self._data = None
        self._trade = None

    async def disconnect(self) -> None:
        await self._close_clients()
        self.connected = False
        logger.info(f"{self.exchange_name} disconnected")

    # ── Datos ──

    def _price_from_ticker(self, symbol: str, ticker: Dict) -> PriceData:
        return PriceData(
            symbol=symbol,
            market=self.market,
            timestamp=datetime.now(timezone.utc),
            bid=ticker.get('bid') or 0,
            ask=ticker.get('ask') or 0,
            last=ticker.get('last') or 0,
            volume=ticker.get('quoteVolume') or 0,
            high_24h=ticker.get('high') or 0,
            low_24h=ticker.get('low') or 0,
            change_24h_pct=ticker.get('percentage') or 0
        )

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        if self._simulation_mode:
            return self._sim_price(symbol)
        if not self._data:
            return None
        try:
            ticker = await self._data.fetch_ticker(self.resolve_symbol(symbol))
            price = self._price_from_ticker(symbol, ticker)
            return price if price.last > 0 else None
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precio de {symbol}: {e}")
            return None

    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        if self._simulation_mode:
            return [self._sim_price(s) for s in symbols]
        if not self._data:
            return []
        if not self._data.has.get("fetchTickers"):
            # Bitso y otros no tienen consulta en bloque: uno por uno
            out = []
            for s in symbols:
                p = await self.get_price(s)
                if p:
                    out.append(p)
            return out
        mapped = {self.resolve_symbol(s): s for s in symbols}
        try:
            tickers = await self._data.fetch_tickers(list(mapped))
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precios de cripto: {e}")
            return []
        return [self._price_from_ticker(orig, tickers[ex]) for ex, orig in mapped.items() if ex in tickers]

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[OHLCV]:
        """Velas cerradas. Binance incluye la vela en formación al final: se descarta."""
        if self._simulation_mode:
            return self._sim_ohlcv(symbol, limit)
        if not self._data:
            return []
        try:
            raw = await self._data.fetch_ohlcv(self.resolve_symbol(symbol), timeframe, limit=limit + 1)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin velas de {symbol}: {e}")
            return []
        step_ms = TF_SECONDS.get(timeframe, 3600) * 1000
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        closed = [c for c in raw if c[0] + step_ms <= now_ms]
        return [
            OHLCV(
                timestamp=datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
                open=c[1], high=c[2], low=c[3], close=c[4], volume=c[5]
            )
            for c in closed[-limit:]
        ]

    # ── Reglas del mercado ──

    def _market_info(self, symbol: str) -> Optional[Dict]:
        client = self._trade or self._data
        if not client or not getattr(client, "markets", None):
            return None
        return client.markets.get(self.resolve_symbol(symbol))

    def amount_to_precision(self, symbol: str, quantity: float) -> float:
        client = self._trade or self._data
        if client and self._market_info(symbol):
            try:
                return float(client.amount_to_precision(self.resolve_symbol(symbol), quantity))
            except Exception:
                return 0.0
        return quantity

    def min_notional(self, symbol: str) -> float:
        info = self._market_info(symbol)
        if not info:
            return 10.0 if not self._simulation_mode else 0.0
        cost_min = ((info.get("limits") or {}).get("cost") or {}).get("min")
        return float(cost_min or 0.0)

    # ── Órdenes ──

    def _order_result(self, symbol: str, side: str, quantity: float, order: Dict, cid: str) -> OrderResult:
        filled = float(order.get('filled') or 0.0)
        avg = float(order.get('average') or order.get('price') or 0.0)
        base = symbol.split("/")[0]
        fee_quote, fee_base = 0.0, 0.0
        for f in (order.get('fees') or ([order['fee']] if order.get('fee') else [])):
            if not f or f.get('cost') is None:
                continue
            if f.get('currency') == base:
                fee_base += float(f['cost'])
                fee_quote += float(f['cost']) * avg
            else:
                fee_quote += float(f['cost'])
        # Si la comisión se cobró en el activo comprado, lo que queda en la cuenta es menos
        net_filled = filled - fee_base if side == "buy" else filled
        return OrderResult(
            success=True,
            order_id=str(order.get('id', '')),
            client_order_id=cid,
            symbol=symbol, side=side, quantity=quantity,
            price=avg,
            filled_quantity=net_filled,
            status=order.get('status') or 'unknown',
            fee=fee_quote,
        )

    async def _find_order(self, symbol: str, cid: str) -> Optional[Dict]:
        """Busca una orden por nuestro id: sirve para saber si una orden 'fallida' sí entró."""
        ex_symbol = self.resolve_symbol(symbol)
        if self.exchange_id == "binance":
            try:
                return await self._trade.fetch_order("", ex_symbol, params={"origClientOrderId": cid})
            except Exception:
                return None
        # Otros exchanges: buscar entre las órdenes recientes
        for fetch in ("fetch_open_orders", "fetch_closed_orders"):
            try:
                for o in await getattr(self._trade, fetch)(ex_symbol, None, 50):
                    if o.get("clientOrderId") == cid:
                        return o
            except Exception:
                continue
        return None

    async def _send(self, symbol: str, side: str, quantity: float, order_type: str,
                    price: Optional[float], cid: str) -> OrderResult:
        qty = self.amount_to_precision(symbol, quantity)
        if qty <= 0:
            return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity,
                               status="failed", error="Cantidad menor que el mínimo del mercado",
                               client_order_id=cid)
        ex_symbol = self.resolve_symbol(symbol)
        try:
            if order_type == "market":
                order = await self._trade.create_order(ex_symbol, "market", side, qty, None, {"clientOrderId": cid})
            else:
                order = await self._trade.create_order(ex_symbol, "limit", side, qty, price, {"clientOrderId": cid})
        except Exception as e:
            # El error pudo llegar después de que Binance aceptó la orden: preguntar antes de reportar falla
            found = await self._find_order(symbol, cid)
            if found:
                logger.warning(f"La orden {cid} sí entró pese al error ({e})")
                return self._order_result(symbol, side, qty, found, cid)
            logger.error(f"Error en orden {order_type} {side} {qty} {symbol}: {e}")
            return OrderResult(success=False, symbol=symbol, side=side, quantity=qty,
                               status="failed", error=str(e), client_order_id=cid)

        if order_type == "market" and not order.get('filled'):
            fetched = await self._find_order(symbol, cid)
            if fetched:
                order = fetched
        logger.info(f"Orden {order_type} {side} {qty} {symbol} - id {order.get('id')} ({cid})")
        return self._order_result(symbol, side, qty, order, cid)

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResult]:
        if self.dry_run or self._simulation_mode or not self._trade:
            return None
        order = await self._find_order(symbol, client_order_id)
        if not order:
            return None
        return self._order_result(symbol, order.get("side", ""), float(order.get("amount") or 0), order, client_order_id)

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                 client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        if self.dry_run or self._simulation_mode:
            price_data = await self.get_price(symbol)
            if not price_data:
                return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity,
                                   status="failed", error="Sin precio", client_order_id=cid, simulated=True)
            # Al comprar se paga el ask, al vender se recibe el bid
            px = (price_data.ask if side == "buy" else price_data.bid) or price_data.last
            qty = self.amount_to_precision(symbol, quantity)
            result = self._simulate_order(symbol, side, qty, px, cid)
            if result.success:
                logger.info(f"[PAPEL] Mercado {side} {qty:.6f} {symbol} @ {px:,.4f}")
            return result
        if not self._trade:
            return OrderResult(success=False, error=f"Sin cuenta de {self.exchange_name} conectada", client_order_id=cid)
        return await self._send(symbol, side, quantity, "market", None, cid)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                                client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        if self.dry_run or self._simulation_mode:
            result = self._simulate_order(symbol, side, self.amount_to_precision(symbol, quantity), price, cid)
            if result.success:
                logger.info(f"[PAPEL] Límite {side} {quantity:.6f} {symbol} @ {price:,.4f}")
            return result
        if not self._trade:
            return OrderResult(success=False, error=f"Sin cuenta de {self.exchange_name} conectada", client_order_id=cid)
        return await self._send(symbol, side, quantity, "limit", price, cid)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        if self.dry_run or self._simulation_mode:
            logger.info(f"[PAPEL] Orden cancelada {order_id}")
            return True
        if not self._trade:
            return False
        try:
            await self._trade.cancel_order(order_id, self.resolve_symbol(symbol))
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    # ── Cuenta ──

    async def get_balance(self) -> Dict[str, float]:
        if self.dry_run or self._simulation_mode:
            return dict(self._simulated_balance)
        if not self._trade:
            raise RuntimeError(f"Sin cuenta de {self.exchange_name} conectada")
        balance = await self._trade.fetch_balance()
        free = balance.get("free") or {}
        return {cur: float(v) for cur, v in free.items() if v and float(v) > 0}

    async def get_positions(self) -> List[Position]:
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())
        balance = await self.get_balance()
        positions = []
        for currency, amount in balance.items():
            if currency in ('USD', 'USDT', 'USDC', 'BUSD', 'FDUSD'):
                continue
            symbol = f"{currency}/USDT"
            price_data = await self.get_price(symbol)
            if price_data:
                positions.append(Position(symbol=symbol, side="long", quantity=amount,
                                          entry_price=0, current_price=price_data.last))
        return positions

    def is_market_open(self) -> bool:
        """Crypto markets are always open."""
        return True
