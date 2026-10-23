"""
Izalith Trading System - Crypto Adapter (ccxt: Binance, Kraken, Bitso, Bybit, OKX...)

El exchange se elige con CRYPTO_EXCHANGE (por defecto binance). Desde México lo natural es
Binance o Bitso; desde Canadá, Kraken (Binance, Bybit y OKX no operan ahí).

Usa dos clientes del mismo exchange:
- datos: público de producción, siempre (precios reales aunque se opere en testnet)
- operación: la cuenta con llaves (testnet o real), sólo si hay llaves

Los símbolos del bot son los del laboratorio (BTC/USDT). En el exchange se opera contra la
moneda de cotización configurada (CRYPTO_QUOTE; en Kraken, USD por defecto: BTC/USDT -> BTC/USD),
porque el dinero que se deposita es USD o CAD, no USDT.

Si Binance no contesta, no se inventan precios: se regresa None o [] salvo que la
simulación se haya pedido o permitido (ver BaseAdapter).
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .base import (BaseAdapter, PriceData, OHLCV, OrderResult, Position, new_client_order_id, sub_client_id,
                   SUB_SUFFIXES, OPEN_STATES, OrderLookupError)
from core.utils.logger import get_logger

logger = get_logger("crypto")

# (maker, taker) del nivel de entrada, verificadas el 22 oct 2026. El papel cobra lo mismo que la realidad.
EXCHANGE_FEES = {
    "binance": (0.001, 0.001),
    "kraken": (0.004, 0.008),     # Kraken Pro, nivel de $0 a $2.5k de volumen mensual
    "bitso": (0.003, 0.0036),     # pares en USD/USDT; los pares en MXN cuestan 0.60% / 0.78%
    "ndax": (0.002, 0.002),
    "bybit": (0.001, 0.001),
    "okx": (0.0008, 0.001),
}

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
        quote: str = "",
    ):
        super().__init__(dry_run, allow_simulation, simulate)
        self.api_key = api_key
        self.secret = secret
        self.testnet = testnet
        self.exchange_id = (exchange_id or "binance").lower()
        self.exchange_name = self.exchange_id.capitalize()
        self.maker_fee, self.taker_fee = EXCHANGE_FEES.get(self.exchange_id, (0.001, 0.001))
        self.sim_fee = self.taker_fee
        self._data = None
        self._trade = None
        self._symbol_map: Dict[str, str] = {}
        # Moneda contra la que se opera: en Kraken se deposita USD/CAD, no USDT
        self.quote = (quote or ("USD" if self.exchange_id == "kraken" else "")).upper()
        self._last_px: Dict[str, float] = {}

    @property
    def display_name(self) -> str:
        return f"Cripto ({self.exchange_name})"

    def resolve_symbol(self, symbol: str) -> str:
        """Par del bot -> par del exchange (BTC/USDT -> BTC/USD con CRYPTO_QUOTE=USD)."""
        if symbol in self._symbol_map:
            return self._symbol_map[symbol]
        markets = getattr(self._data, "markets", None) or {}
        if not markets or "/" not in symbol:
            return symbol
        base = symbol.split("/", 1)[0]
        candidates = ([f"{base}/{self.quote}"] if self.quote else []) + [symbol] + \
                     [f"{base}/{alt}" for alt in ("USDT", "USD", "USDC")]
        resolved = next((c for c in candidates if c in markets), symbol)
        self._symbol_map[symbol] = resolved
        return resolved

    def quote_currency(self, symbol: str) -> str:
        resolved = self.resolve_symbol(symbol)
        return resolved.split("/")[1] if "/" in resolved else "USD"

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
                    try:
                        self._trade.set_sandbox_mode(True)
                    except Exception:
                        if not self.dry_run:
                            # Pidió testnet y el exchange no tiene: no conectar la cuenta, para no operar dinero real sin querer
                            raise RuntimeError(f"{self.exchange_name} no tiene testnet y se pidió testnet con DRY_RUN=false; "
                                               f"pon CRYPTO_TESTNET=false si de verdad quieres operar en real")
                        logger.warning(f"{self.exchange_name} no tiene testnet: la cuenta se conecta sólo para leer "
                                       f"saldos y se opera en papel (DRY_RUN=true). Pon CRYPTO_TESTNET=false para quitar este aviso.")
                        self.testnet = False
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
            if price.last:
                self._last_px[symbol] = price.last
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
        out = [self._price_from_ticker(orig, tickers[ex]) for ex, orig in mapped.items() if ex in tickers]
        for p in out:
            if p.last:
                self._last_px[p.symbol] = p.last
        return out

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
        """
        Monto mínimo real por orden: el mayor entre el mínimo en dinero y el mínimo en cantidad.
        Kraken: 0.5 USD, pero también 0.00006 BTC (~5 USD) y 0.002 ETH (~5-8 USD).
        """
        info = self._market_info(symbol)
        if not info:
            return 10.0 if not self._simulation_mode else 0.0
        limits = info.get("limits") or {}
        cost_min = float((limits.get("cost") or {}).get("min") or 0.0)
        amount_min = float((limits.get("amount") or {}).get("min") or 0.0)
        px = self._last_px.get(symbol, 0.0)
        return max(cost_min, amount_min * px * 1.01)

    # ── Órdenes ──

    @staticmethod
    def _status(order: Dict) -> str:
        """
        Estado de una orden de ccxt. Vacío se trata como VIVA: Kraken contesta AddOrder sólo con
        el txid, así que ccxt regresa status=None aunque la orden esté en el libro.
        """
        return (order.get("status") or "open").lower()

    def _order_result(self, symbol: str, side: str, quantity: float, order: Dict, cid: str) -> OrderResult:
        filled = float(order.get('filled') or 0.0)
        avg = float(order.get('average') or order.get('price') or 0.0)
        base = symbol.split("/")[0]
        fee_quote, fee_base = 0.0, 0.0
        quote = self.quote_currency(symbol)
        for f in (order.get('fees') or ([order['fee']] if order.get('fee') else [])):
            if not f or f.get('cost') is None:
                continue
            cur = f.get('currency')
            if cur == base:
                fee_base += float(f['cost'])
                fee_quote += float(f['cost']) * avg
            elif cur in (None, quote, "USD", "USDT", "USDC"):
                fee_quote += float(f['cost'])
            else:
                # Comisión en otra moneda (BNB en Binance): se convierte con su último precio; si no se
                # conoce, se estima como la comisión de taker sobre el monto (sumar BNB como dólares no sirve)
                px = self._last_px.get(f"{cur}/USDT") or self._last_px.get(f"{cur}/{quote}")
                if px:
                    fee_quote += float(f['cost']) * px
                else:
                    logger.warning(f"Comisión en {cur} sin precio para convertir; se estima con {self.taker_fee:.2%}")
                    fee_quote += filled * avg * self.taker_fee
        # Si la comisión se cobró en el activo comprado, lo que queda en la cuenta es menos
        net_filled = filled - fee_base if side == "buy" else filled
        return OrderResult(
            success=True,
            order_id=str(order.get('id', '')),
            client_order_id=cid,
            symbol=symbol, side=side, quantity=quantity,
            price=avg,
            filled_quantity=net_filled,
            status=order.get('status') or 'unknown',   # 'unknown' = en vuelo para execute_order
            fee=fee_quote,
        )

    async def _find_order(self, symbol: str, cid: str, strict: bool = False) -> Optional[Dict]:
        """
        Busca una orden por nuestro id: sirve para saber si una orden 'fallida' sí entró.
        Con strict=True, si no se pudo preguntar lanza OrderLookupError en vez de decir "no existe".
        """
        ex_symbol = self.resolve_symbol(symbol)
        if self.exchange_id == "binance":
            try:
                return await self._trade.fetch_order("", ex_symbol, params={"origClientOrderId": cid})
            except Exception as e:
                if type(e).__name__ == "OrderNotFound" or not strict:
                    return None
                raise OrderLookupError(str(e))
        # Kraken filtra por cl_ord_id del lado del servidor; los demás, entre las órdenes recientes
        params = {"clientOrderId": cid} if self.exchange_id == "kraken" else {}
        failed = None
        for fetch in ("fetch_open_orders", "fetch_closed_orders"):
            try:
                for o in await getattr(self._trade, fetch)(ex_symbol, None, 50, params):
                    if o.get("clientOrderId") == cid:
                        return o
            except Exception as e:
                failed = e
        if strict and failed is not None:
            raise OrderLookupError(str(failed))
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
            # El error pudo llegar después de que el exchange aceptó la orden: preguntar antes de reportar falla
            try:
                found = await self._find_order(symbol, cid, strict=True)
            except OrderLookupError as e2:
                # No se sabe si entró: "unknown" la deja en vuelo para que se revise antes de repetirla
                logger.error(f"Orden {order_type} {side} {qty} {symbol}: falló ({e}) y no se pudo confirmar si entró ({e2})")
                return OrderResult(success=True, symbol=symbol, side=side, quantity=qty, status="unknown",
                                   error=f"No se sabe si entró: {e}", client_order_id=cid)
            if found:
                logger.warning(f"La orden {cid} sí entró pese al error ({e})")
                return self._order_result(symbol, side, qty, found, cid)
            logger.error(f"Error en orden {order_type} {side} {qty} {symbol}: {e}")
            return OrderResult(success=False, symbol=symbol, side=side, quantity=qty,
                               status="failed", error=str(e), client_order_id=cid)

        if order.get("status") is None or order.get("filled") is None:
            # Kraken sólo regresa el txid al crear: el estado y lo llenado hay que pedirlos
            for _ in range(5 if order_type == "market" else 1):
                try:
                    order = {**order, **await self._trade.fetch_order(order["id"], ex_symbol)}
                except Exception:
                    fetched = await self._find_order(symbol, cid)
                    order = fetched or order
                if order_type != "market" or self._status(order) not in OPEN_STATES:
                    break
                await asyncio.sleep(1)
        logger.info(f"Orden {order_type} {side} {qty} {symbol} - id {order.get('id')} ({cid})")
        return self._order_result(symbol, side, qty, order, cid)

    def _all_ids(self, client_order_id: str):
        return [client_order_id] + [sub_client_id(client_order_id, sfx) for sfx in SUB_SUFFIXES]

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResult]:
        """Incluye las sub-órdenes de una orden pasiva. Si alguna sigue viva, el status lo dice."""
        if self.dry_run or self._simulation_mode or not self._trade:
            return None
        found = []
        for cid in self._all_ids(client_order_id):
            order = await self._find_order(symbol, cid, strict=True)
            if order:
                found.append(self._order_result(symbol, order.get("side", ""), float(order.get("amount") or 0), order, cid))
        if not found:
            return None
        live = any((r.status or "").lower() in OPEN_STATES or (r.status or "").lower() in ("", "unknown")
                   for r in found)
        filled = [r for r in found if r.filled_quantity > 0]
        if not filled:
            r = found[0]
            r.status = "open" if live else r.status
            return r
        total = sum(r.filled_quantity for r in filled)
        avg = sum(r.filled_quantity * r.price for r in filled) / total
        return OrderResult(success=True, order_id=filled[0].order_id, client_order_id=client_order_id, symbol=symbol,
                           side=filled[0].side, quantity=total, price=avg, filled_quantity=total,
                           status="open" if live else "closed", fee=sum(r.fee for r in filled))

    async def _cancel_confirmed(self, order: Dict, ex_symbol: str) -> Optional[Dict]:
        """Cancela y confirma con el exchange. Regresa la orden ya muerta, o None si no se pudo confirmar."""
        for i in range(3):
            try:
                await self._trade.cancel_order(order["id"], ex_symbol)
            except Exception as e:
                logger.warning(f"Cancelar la orden {order.get('id')} falló (intento {i + 1}): {e}")
            try:
                o = await self._trade.fetch_order(order["id"], ex_symbol)
                if self._status(o) not in OPEN_STATES:
                    return o
            except Exception:
                pass
            await asyncio.sleep(2 * (i + 1))
        return None

    async def cancel_open_by_client_id(self, symbol: str, client_order_id: str) -> bool:
        if self.dry_run or self._simulation_mode or not self._trade:
            return True
        ok = True
        for cid in self._all_ids(client_order_id):
            try:
                order = await self._find_order(symbol, cid, strict=True)
            except OrderLookupError:
                ok = False
                continue
            if order and self._status(order) in OPEN_STATES:
                ok = ok and (await self._cancel_confirmed(order, self.resolve_symbol(symbol))) is not None
        return ok

    async def place_passive_order(self, symbol: str, side: str, quantity: float,
                                  client_order_id: Optional[str] = None, max_wait_s: float = 900,
                                  requotes: int = 3) -> OrderResult:
        """
        Orden límite que espera en el libro (paga la comisión de maker, la mitad en Kraken).

        Se pone en el mejor bid (compra) o ask (venta) con postOnly; si en max_wait_s/requotes
        no se llenó, se cancela (y se confirma que quedó cancelada) y se vuelve a poner al
        precio nuevo. Lo que quede al final se termina a mercado.

        Seguridad: si una sub-orden no se puede confirmar como cancelada, NO se manda nada más
        (ni otro intento ni el resto a mercado) y el resultado queda "en_vuelo" para que la
        reconciliación lo resuelva. Así una orden viva nunca se duplica.
        """
        cid = client_order_id or new_client_order_id()
        qty = self.amount_to_precision(symbol, quantity)
        if self.dry_run or self._simulation_mode:
            p = await self.get_price(symbol)
            if not p:
                return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity, status="failed",
                                   error="Sin precio", client_order_id=cid, simulated=True)
            px = (p.bid if side == "buy" else p.ask) or p.last
            result = self._simulate_order(symbol, side, qty, px, cid, fee_rate=self.maker_fee)
            if result.success:
                logger.info(f"[PAPEL] Límite pasiva {side} {qty:.6f} {symbol} @ {px:,.4f} (maker {self.maker_fee:.2%})")
            return result
        if not self._trade:
            return OrderResult(success=False, error=f"Sin cuenta de {self.exchange_name} conectada", client_order_id=cid)

        ex_symbol = self.resolve_symbol(symbol)
        ref = await self.get_price(symbol)
        ref_px = ref.last if ref else 0.0
        remaining = qty
        fills = []
        unconfirmed = False
        wait = max(30.0, max_wait_s / max(1, requotes))
        loop = asyncio.get_event_loop()
        for attempt in range(1, requotes + 1):
            if remaining <= 0 or remaining * ref_px < self.min_notional(symbol):
                break
            sub = sub_client_id(cid, str(attempt))
            try:
                ticker = await self._data.fetch_ticker(ex_symbol)
                px = ticker["bid"] if side == "buy" else ticker["ask"]
                order = await self._trade.create_order(
                    ex_symbol, "limit", side, self.amount_to_precision(symbol, remaining), px,
                    {"postOnly": True, "clientOrderId": sub})
            except Exception as e:
                # El error pudo llegar después de que el exchange aceptó la orden: preguntar antes de seguir
                try:
                    order = await self._find_order(symbol, sub, strict=True)
                except OrderLookupError as e2:
                    logger.error(f"Orden pasiva {attempt} de {symbol}: falló ({e}) y no se pudo confirmar si entró "
                                 f"({e2}); no se manda nada más hasta reconciliar")
                    unconfirmed = True
                    break
                if not order:
                    logger.warning(f"Orden pasiva {attempt} de {symbol} rechazada: {e}")
                    continue
                logger.warning(f"La orden pasiva {attempt} de {symbol} sí entró pese al error ({e})")
            if order.get("status") is None or order.get("filled") is None:
                # Kraken sólo regresa el txid al crear: el estado y lo llenado hay que pedirlos
                try:
                    order = {**order, **await self._trade.fetch_order(order["id"], ex_symbol)}
                except Exception:
                    pass
            deadline = loop.time() + wait
            status = self._status(order)
            while status in OPEN_STATES and loop.time() < deadline:
                await asyncio.sleep(10)
                try:
                    order = await self._trade.fetch_order(order["id"], ex_symbol)
                    status = self._status(order)
                except Exception:
                    pass
            if status in OPEN_STATES:
                final = await self._cancel_confirmed(order, ex_symbol)
                if final is None:
                    logger.error(f"No se pudo confirmar la cancelación de la orden pasiva {order.get('id')} de {symbol}: "
                                 f"no se manda nada más hasta reconciliar")
                    unconfirmed = True
                else:
                    order = final
            gross = float(order.get("filled") or 0)
            if gross > 0:
                fills.append(self._order_result(symbol, side, gross, order, sub))
                remaining = self.amount_to_precision(symbol, remaining - gross)
            if unconfirmed:
                break

        if remaining > 0 and not unconfirmed:
            p = await self.get_price(symbol)
            if p and remaining * p.last >= self.min_notional(symbol):
                logger.info(f"Orden pasiva de {symbol} no se llenó completa; el resto ({remaining}) va a mercado")
                rest = await self._send(symbol, side, remaining, "market", None, sub_client_id(cid, "m"))
                if rest.success and (rest.status or "").lower() in set(OPEN_STATES) | {"", "unknown"}:
                    unconfirmed = True          # la de mercado no terminó o no se sabe: no se da por llena
                if rest.success and rest.filled_quantity > 0:
                    fills.append(rest)

        total = sum(f.filled_quantity for f in fills)
        status = "en_vuelo" if unconfirmed else ("closed" if fills else "failed")
        if not fills:
            return OrderResult(success=unconfirmed, symbol=symbol, side=side, quantity=qty, status=status,
                               error=None if unconfirmed else "La orden pasiva no se llenó", client_order_id=cid)
        avg = sum(f.filled_quantity * f.price for f in fills) / total
        return OrderResult(success=True, order_id=fills[0].order_id, client_order_id=cid, symbol=symbol, side=side,
                           quantity=qty, price=avg, filled_quantity=total, status=status,
                           fee=sum(f.fee for f in fills))

    async def place_passive_notional(self, symbol: str, side: str, amount: float,
                                     client_order_id: Optional[str] = None, max_wait_s: float = 900) -> OrderResult:
        price = await self.get_price(symbol)
        if not price or price.last <= 0:
            return OrderResult(success=False, symbol=symbol, side=side, status="failed", error="Sin precio",
                               client_order_id=client_order_id or "")
        ref = (price.bid if side == "buy" else price.ask) or price.last
        # Se deja aire para la comisión: si el saldo no alcanza, la orden falla
        # 0.2% de aire por si el precio se mueve entre este cálculo y la orden
        qty = self.amount_to_precision(symbol, amount / ref / (1 + self.maker_fee + 0.002))
        return await self.place_passive_order(symbol, side, qty, client_order_id, max_wait_s)

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
            return self._paper_balance()
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
