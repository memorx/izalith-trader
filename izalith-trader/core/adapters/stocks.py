"""
Izalith Trading System - Stocks Adapter (Alpaca, alpaca-py)

alpaca-py es síncrono: cada llamada va en un hilo para no congelar el resto del bot.
Las velas se piden con ventana de fechas y se toma la cola (Alpaca regresa de la más
vieja a la más nueva y `limit` cortaría las recientes).
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pytz

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position, new_client_order_id
from core.utils.logger import get_logger

logger = get_logger("stocks")

TF_SECONDS = {"1m": 60, "1h": 3600, "1d": 86400, "1w": 604800}


class StocksAdapter(BaseAdapter):
    """Alpaca stocks adapter."""

    market = "stock"
    supports_short = False
    sim_prices = {"VTI": 280.0, "VOO": 520.0, "SPY": 570.0, "QQQ": 480.0, "IEF": 95.0, "AGG": 98.0}
    sim_vol_per_hour = 0.003
    sim_fee = 0.0

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        dry_run: bool = True,
        allow_simulation: bool = False,
        simulate: bool = False,
    ):
        super().__init__(dry_run, allow_simulation, simulate)
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self._api = None
        self._data_client = None
        self._yahoo = False   # sin llaves y en papel: precios reales de Yahoo

    @property
    def display_name(self) -> str:
        return "Acciones (Yahoo, sólo papel)" if self._yahoo else "Acciones (Alpaca)"

    async def connect(self) -> bool:
        if self.force_simulation:
            return self._enter_simulation("simulación pedida")
        if not (self.api_key and self.secret_key):
            if self.dry_run:
                try:
                    import yfinance  # noqa: F401
                except ImportError:
                    return self._enter_simulation("sin llaves de Alpaca y sin yfinance")
                self._yahoo = True
                self.connected = True
                self._simulation_mode = False
                self.last_error = "Sin llaves de Alpaca: precios de Yahoo (con unos minutos de retraso), sólo papel"
                logger.info("Acciones: precios de Yahoo para operar en papel")
                return True
            return self._enter_simulation("sin llaves de Alpaca")
        try:
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
        except ImportError:
            return self._enter_simulation("alpaca-py no está instalado")
        try:
            self._api = TradingClient(api_key=self.api_key, secret_key=self.secret_key, paper=self.paper)
            self._data_client = StockHistoricalDataClient(api_key=self.api_key, secret_key=self.secret_key)
            await asyncio.to_thread(self._api.get_account)
        except Exception as e:
            self._api = self._data_client = None
            return self._enter_simulation(f"Alpaca no contestó ({e})")
        self.connected = True
        self._simulation_mode = False
        logger.info(f"Alpaca conectado ({'papel' if self.paper else 'REAL'})")
        return True

    async def disconnect(self) -> None:
        self._api = None
        self._data_client = None
        self.connected = False
        logger.info("Yahoo disconnected" if self._yahoo else "Alpaca disconnected")

    # ── Datos ──

    @staticmethod
    def _feed():
        from alpaca.data.enums import DataFeed
        return DataFeed.IEX   # el feed gratuito; SIP pide suscripción

    async def _yahoo_price(self, symbol: str) -> Optional[PriceData]:
        import yfinance as yf

        def fetch():
            info = yf.Ticker(symbol).fast_info
            return float(info["last_price"]), float(info.get("day_high") or 0), float(info.get("day_low") or 0),                 float(info.get("previous_close") or 0)
        try:
            last, hi, lo, prev = await asyncio.to_thread(fetch)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precio de {symbol} en Yahoo: {e}")
            return None
        if not last or last <= 0:
            return None
        spread = last * 0.0002   # Yahoo no da bid/ask: se supone un spread chico de ETF líquido
        return PriceData(symbol=symbol, market=self.market, timestamp=datetime.now(timezone.utc),
                         bid=last - spread / 2, ask=last + spread / 2, last=last, high_24h=hi, low_24h=lo,
                         change_24h_pct=(last / prev - 1) * 100 if prev else 0.0)

    async def _yahoo_ohlcv(self, symbol: str, timeframe: str, limit: int) -> List[OHLCV]:
        import yfinance as yf
        interval = {"1m": "1m", "1h": "1h", "1d": "1d", "1w": "1wk"}.get(timeframe, "1h")
        period = {"1m": "5d", "1h": "60d", "1d": "2y", "1w": "10y"}.get(timeframe, "60d")
        try:
            df = await asyncio.to_thread(lambda: yf.Ticker(symbol).history(period=period, interval=interval))
        except Exception as e:
            self.last_error = str(e)
            return []
        if df is None or df.empty:
            return []
        step = timedelta(seconds=TF_SECONDS.get(timeframe, 3600))
        now = datetime.now(timezone.utc)
        out = []
        for ts, row in df.iterrows():
            t = ts.to_pydatetime().astimezone(timezone.utc)
            if timeframe != "1d" and t + step > now:
                continue
            out.append(OHLCV(timestamp=t, open=float(row["Open"]), high=float(row["High"]),
                             low=float(row["Low"]), close=float(row["Close"]), volume=float(row["Volume"])))
        return out[-limit:]

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        if self._simulation_mode:
            return self._sim_price(symbol)
        if self._yahoo:
            return await self._yahoo_price(symbol)
        if not self._data_client:
            return None
        try:
            from alpaca.data.requests import StockLatestQuoteRequest, StockLatestTradeRequest
            trades = await asyncio.to_thread(
                self._data_client.get_stock_latest_trade,
                StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._feed()))
            quotes = await asyncio.to_thread(
                self._data_client.get_stock_latest_quote,
                StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed()))
            trade, quote = trades.get(symbol), quotes.get(symbol)
            if not trade:
                return None
            bid = quote.bid_price if quote and quote.bid_price else trade.price
            ask = quote.ask_price if quote and quote.ask_price else trade.price
            ts = trade.timestamp if trade.timestamp.tzinfo else trade.timestamp.replace(tzinfo=timezone.utc)
            return PriceData(symbol=symbol, market=self.market, timestamp=ts,
                             bid=bid, ask=ask, last=trade.price, volume=trade.size)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precio de {symbol}: {e}")
            return None

    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        prices = []
        for symbol in symbols:
            price = await self.get_price(symbol)
            if price:
                prices.append(price)
        return prices

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[OHLCV]:
        if self._simulation_mode:
            return self._sim_ohlcv(symbol, limit)
        if self._yahoo:
            return await self._yahoo_ohlcv(symbol, timeframe, limit)
        if not self._data_client:
            return []
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            tf = {"1m": TimeFrame.Minute, "1h": TimeFrame.Hour, "1d": TimeFrame.Day,
                  "1w": TimeFrame.Week}.get(timeframe, TimeFrame.Hour)
            # Ventana de fechas holgada para `limit` velas (la bolsa abre ~6.5 h, 5 días por semana)
            days = {"1m": max(3, limit // 300 + 3), "1h": limit // 6 * 7 // 5 + 10,
                    "1d": limit * 7 // 5 + 15, "1w": limit * 7 + 14}.get(timeframe, 60)
            end = datetime.now(timezone.utc)
            request = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf,
                                       start=end - timedelta(days=days), end=end, feed=self._feed())
            bars = await asyncio.to_thread(self._data_client.get_stock_bars, request)
            rows = bars.data.get(symbol, []) if hasattr(bars, "data") else bars.get(symbol, [])
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin velas de {symbol}: {e}")
            return []
        step = timedelta(seconds=TF_SECONDS.get(timeframe, 3600))
        out = []
        for bar in rows:
            ts = bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc)
            if ts + step > end:
                continue   # vela todavía abierta
            out.append(OHLCV(timestamp=ts, open=bar.open, high=bar.high, low=bar.low,
                             close=bar.close, volume=bar.volume))
        return out[-limit:]

    # ── Órdenes ──

    def amount_to_precision(self, symbol: str, quantity: float) -> float:
        # Alpaca acepta fracciones de acción hasta 9 decimales; se trunca para no pasarse
        return int(quantity * 1e9) / 1e9

    async def _wait_fill(self, order) -> object:
        """Las órdenes a mercado regresan 'accepted'; se espera unos segundos a que se llenen."""
        for _ in range(10):
            status = getattr(order.status, "value", str(order.status))
            if status in ("filled", "canceled", "expired", "rejected"):
                break
            await asyncio.sleep(1)
            order = await asyncio.to_thread(self._api.get_order_by_id, order.id)
        return order

    def _result(self, symbol: str, side: str, quantity: float, order, cid: str) -> OrderResult:
        status = getattr(order.status, "value", str(order.status))
        filled = float(order.filled_qty or 0)
        return OrderResult(
            success=status not in ("canceled", "expired", "rejected"),
            order_id=str(order.id), client_order_id=cid, symbol=symbol, side=side,
            quantity=quantity, price=float(order.filled_avg_price or 0),
            filled_quantity=filled, status=status,
            error=None if status not in ("canceled", "expired", "rejected") else f"Orden {status}",
        )

    async def _submit(self, symbol: str, side: str, request, quantity: float, cid: str) -> OrderResult:
        try:
            order = await asyncio.to_thread(self._api.submit_order, request)
        except Exception as e:
            try:
                order = await asyncio.to_thread(self._api.get_order_by_client_id, cid)
                logger.warning(f"La orden {cid} sí entró pese al error ({e})")
            except Exception:
                logger.error(f"Error en orden {side} {symbol}: {e}")
                return OrderResult(success=False, error=str(e), client_order_id=cid)
        order = await self._wait_fill(order)
        logger.info(f"Orden {side} {symbol} - id {order.id} ({getattr(order.status, 'value', order.status)})")
        return self._result(symbol, side, quantity, order, cid)

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResult]:
        if self.dry_run or self._simulation_mode or not self._api:
            return None
        try:
            order = await asyncio.to_thread(self._api.get_order_by_client_id, client_order_id)
        except Exception:
            return None
        side = getattr(order.side, "value", str(order.side))
        return self._result(symbol, side, float(order.qty or 0), order, client_order_id)

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
        if not self._api:
            return OrderResult(success=False, error="Sin cuenta de Alpaca conectada", client_order_id=cid)
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        request = MarketOrderRequest(symbol=symbol, qty=qty, client_order_id=cid,
                                     side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                     time_in_force=TimeInForce.DAY)
        return await self._submit(symbol, side, request, qty, cid)

    async def place_notional_order(self, symbol: str, side: str, amount: float,
                                   client_order_id: Optional[str] = None) -> OrderResult:
        """Compra o vende por monto en dólares (fracciones de acción)."""
        cid = client_order_id or new_client_order_id()
        if self.dry_run or self._simulation_mode or not self._api:
            return await super().place_notional_order(symbol, side, amount, cid)
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        request = MarketOrderRequest(symbol=symbol, notional=round(amount, 2), client_order_id=cid,
                                     side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                     time_in_force=TimeInForce.DAY)
        return await self._submit(symbol, side, request, 0.0, cid)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                                client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        qty = self.amount_to_precision(symbol, quantity)
        if self.dry_run or self._simulation_mode:
            result = self._simulate_order(symbol, side, qty, price, cid)
            if result.success:
                logger.info(f"[PAPEL] Límite {side} {qty:.4f} {symbol} @ {price:.2f}")
            return result
        if not self._api:
            return OrderResult(success=False, error="Sin cuenta de Alpaca conectada", client_order_id=cid)
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        fractional = abs(qty - round(qty)) > 1e-9
        request = LimitOrderRequest(symbol=symbol, qty=qty, client_order_id=cid, limit_price=round(price, 2),
                                    side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                    time_in_force=TimeInForce.DAY if fractional else TimeInForce.GTC)
        try:
            order = await asyncio.to_thread(self._api.submit_order, request)
        except Exception as e:
            logger.error(f"Error en orden límite {side} {symbol}: {e}")
            return OrderResult(success=False, error=str(e), client_order_id=cid)
        return self._result(symbol, side, qty, order, cid)

    async def cancel_order(self, order_id: str, symbol: str = "") -> bool:
        if self.dry_run or self._simulation_mode:
            logger.info(f"[PAPEL] Orden cancelada {order_id}")
            return True
        if not self._api:
            return False
        try:
            await asyncio.to_thread(self._api.cancel_order_by_id, order_id)
            return True
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    # ── Cuenta ──

    async def get_balance(self) -> Dict[str, float]:
        if self.dry_run or self._simulation_mode:
            return dict(self._simulated_balance)
        if not self._api:
            raise RuntimeError("Sin cuenta de Alpaca conectada")
        account = await asyncio.to_thread(self._api.get_account)
        return {"USD": float(account.cash)}

    async def get_positions(self) -> List[Position]:
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())
        if not self._api:
            raise RuntimeError("Sin cuenta de Alpaca conectada")
        positions = await asyncio.to_thread(self._api.get_all_positions)
        return [
            Position(symbol=pos.symbol, side="long" if float(pos.qty) > 0 else "short",
                     quantity=abs(float(pos.qty)), entry_price=float(pos.avg_entry_price),
                     current_price=float(pos.current_price), unrealized_pnl=float(pos.unrealized_pl),
                     unrealized_pnl_pct=float(pos.unrealized_plpc) * 100)
            for pos in positions
        ]

    def is_market_open(self) -> bool:
        """NYSE de lunes a viernes 9:30-16:00 hora de Nueva York (no considera feriados)."""
        now = datetime.now(pytz.timezone('America/New_York'))
        if now.weekday() >= 5:
            return False
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now <= market_close
