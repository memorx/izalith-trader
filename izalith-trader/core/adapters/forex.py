"""
Izalith Trading System - Forex Adapter (OANDA API v3)

Los símbolos del bot usan el formato de OANDA (EUR_USD) de punta a punta.
Para cerrar posiciones se usa el endpoint de cierre de OANDA: una orden contraria
en una cuenta con hedging abriría un corto nuevo en vez de cerrar el largo.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp
import pytz

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position, new_client_order_id
from core.utils.logger import get_logger

logger = get_logger("forex")

PRACTICE_URL = "https://api-fxpractice.oanda.com/v3"
LIVE_URL = "https://api-fxtrade.oanda.com/v3"

TF_MAP = {
    "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
    "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
    "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
    "H1": "H1", "H4": "H4", "D": "D", "W": "W",
}


def _parse_time(value: str) -> datetime:
    """OANDA manda nanosegundos ('2024-01-02T15:00:00.000000000Z'); Python acepta hasta microsegundos."""
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        value = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


class ForexAdapter(BaseAdapter):
    """OANDA forex adapter."""

    market = "forex"
    supports_short = True
    sim_prices = {"EUR_USD": 1.0850, "GBP_USD": 1.2650, "USD_JPY": 154.50, "USD_MXN": 18.65, "USD_CAD": 1.38}
    sim_vol_per_hour = 0.001
    sim_fee = 0.0001

    def __init__(
        self,
        account_id: str = "",
        api_key: str = "",
        practice: bool = True,
        dry_run: bool = True,
        allow_simulation: bool = False,
        simulate: bool = False,
    ):
        super().__init__(dry_run, allow_simulation, simulate)
        self.account_id = account_id
        self.api_key = api_key
        self.practice = practice
        self.base_url = PRACTICE_URL if practice else LIVE_URL
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def display_name(self) -> str:
        return "Forex (OANDA)"

    @property
    def _headers(self) -> Dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Formato de OANDA (EUR_USD)."""
        return symbol.replace("/", "_")

    async def connect(self) -> bool:
        if self.force_simulation:
            return self._enter_simulation("simulación pedida")
        if not (self.api_key and self.account_id):
            return self._enter_simulation("sin llaves de OANDA")

        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        try:
            async with self._session.get(f"{self.base_url}/accounts/{self.account_id}",
                                         headers=self._headers) as resp:
                if resp.status != 200:
                    await self._close_session()
                    return self._enter_simulation(f"OANDA rechazó las llaves (HTTP {resp.status})")
        except Exception as e:
            await self._close_session()
            return self._enter_simulation(f"OANDA no contestó ({e})")

        self.connected = True
        self._simulation_mode = False
        logger.info(f"OANDA conectado ({'práctica' if self.practice else 'REAL'})")
        return True

    async def _close_session(self):
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
        self._session = None

    async def disconnect(self) -> None:
        await self._close_session()
        self.connected = False
        logger.info("OANDA disconnected")

    # ── Datos ──

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        if self._simulation_mode:
            return self._sim_price(self._normalize_symbol(symbol))
        if not self._session:
            return None
        inst = self._normalize_symbol(symbol)
        try:
            url = f"{self.base_url}/accounts/{self.account_id}/pricing"
            async with self._session.get(url, headers=self._headers, params={"instruments": inst}) as resp:
                if resp.status != 200:
                    logger.warning(f"Sin precio de {inst}: HTTP {resp.status}")
                    return None
                data = await resp.json()
            prices = data.get("prices", [])
            if not prices:
                return None
            p = prices[0]
            bid = float(p.get("bids", [{}])[0].get("price", 0))
            ask = float(p.get("asks", [{}])[0].get("price", 0))
            if bid <= 0 or ask <= 0:
                return None
            return PriceData(symbol=symbol, market=self.market, timestamp=datetime.now(timezone.utc),
                             bid=bid, ask=ask, last=(bid + ask) / 2, volume=0)
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin precio de {inst}: {e}")
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
            return self._sim_ohlcv(self._normalize_symbol(symbol), limit)
        if not self._session:
            return []
        inst = self._normalize_symbol(symbol)
        try:
            url = f"{self.base_url}/instruments/{inst}/candles"
            params = {"granularity": TF_MAP.get(timeframe, "H1"), "count": limit + 1, "price": "M"}
            async with self._session.get(url, headers=self._headers, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"Sin velas de {inst}: HTTP {resp.status}")
                    return []
                data = await resp.json()
            candles = [c for c in data.get("candles", []) if c.get("complete", False)]
            return [
                OHLCV(
                    timestamp=_parse_time(c["time"]),
                    open=float(c["mid"]["o"]), high=float(c["mid"]["h"]),
                    low=float(c["mid"]["l"]), close=float(c["mid"]["c"]),
                    volume=float(c.get("volume", 0)),
                )
                for c in candles[-limit:]
            ]
        except Exception as e:
            self.last_error = str(e)
            logger.warning(f"Sin velas de {inst}: {e}")
            return []

    # ── Órdenes ──

    def amount_to_precision(self, symbol: str, quantity: float) -> float:
        return float(int(quantity))

    async def _post_order(self, symbol: str, side: str, quantity: float, order: Dict, cid: str) -> OrderResult:
        url = f"{self.base_url}/accounts/{self.account_id}/orders"
        try:
            async with self._session.post(url, headers=self._headers, json={"order": order}) as resp:
                data = await resp.json()
                status = resp.status
        except Exception as e:
            logger.error(f"Error en orden {side} {symbol}: {e}")
            return OrderResult(success=False, error=str(e), client_order_id=cid)

        if status == 201 and "orderFillTransaction" in data:
            fill = data["orderFillTransaction"]
            units = abs(float(fill["units"]))
            return OrderResult(success=True, order_id=fill["id"], client_order_id=cid, symbol=symbol,
                               side=side, quantity=units, price=float(fill["price"]),
                               filled_quantity=units, status="filled",
                               fee=abs(float(fill.get("financing", 0))) + abs(float(fill.get("commission", 0))))
        if status == 201:
            created = data.get("orderCreateTransaction", {})
            return OrderResult(success=True, order_id=created.get("id", ""), client_order_id=cid, symbol=symbol,
                               side=side, quantity=quantity, price=float(order.get("price", 0) or 0),
                               status="pending")
        return OrderResult(success=False, error=data.get("errorMessage", f"HTTP {status}"), client_order_id=cid)

    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                 client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        units = int(quantity)
        if self.dry_run or self._simulation_mode:
            p = await self.get_price(symbol)
            if not p:
                return OrderResult(success=False, error="Sin precio", client_order_id=cid, simulated=True)
            px = p.ask if side == "buy" else p.bid
            result = self._simulate_order(symbol, side, units, px, cid)
            logger.info(f"[PAPEL] Mercado {side} {units} {symbol} @ {px:.5f}")
            return result
        if not self._session:
            return OrderResult(success=False, error="Sin cuenta de OANDA conectada", client_order_id=cid)
        order = {
            "type": "MARKET", "instrument": self._normalize_symbol(symbol),
            "units": str(units if side == "buy" else -units), "timeInForce": "FOK",
            "clientExtensions": {"id": cid},
        }
        return await self._post_order(symbol, side, units, order, cid)

    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                                client_order_id: Optional[str] = None) -> OrderResult:
        cid = client_order_id or new_client_order_id()
        units = int(quantity)
        if self.dry_run or self._simulation_mode:
            result = self._simulate_order(symbol, side, units, price, cid)
            logger.info(f"[PAPEL] Límite {side} {units} {symbol} @ {price:.5f}")
            return result
        if not self._session:
            return OrderResult(success=False, error="Sin cuenta de OANDA conectada", client_order_id=cid)
        decimals = 3 if "JPY" in symbol else 5
        order = {
            "type": "LIMIT", "instrument": self._normalize_symbol(symbol),
            "units": str(units if side == "buy" else -units),
            "price": f"{price:.{decimals}f}", "timeInForce": "GTC",
            "clientExtensions": {"id": cid},
        }
        return await self._post_order(symbol, side, units, order, cid)

    async def close_position(self, symbol: str, side: str, quantity: float,
                             client_order_id: Optional[str] = None) -> OrderResult:
        """Cierra (total o parcialmente) un largo o un corto sin abrir el lado contrario."""
        exit_side = "sell" if side == "long" else "buy"
        if self.dry_run or self._simulation_mode or not self._session:
            return await self.place_market_order(symbol, exit_side, quantity, client_order_id)
        inst = self._normalize_symbol(symbol)
        units = str(int(quantity))
        body = {"longUnits": units} if side == "long" else {"shortUnits": units}
        url = f"{self.base_url}/accounts/{self.account_id}/positions/{inst}/close"
        try:
            async with self._session.put(url, headers=self._headers, json=body) as resp:
                data = await resp.json()
                status = resp.status
        except Exception as e:
            return OrderResult(success=False, error=str(e))
        key = "longOrderFillTransaction" if side == "long" else "shortOrderFillTransaction"
        if status == 200 and key in data:
            fill = data[key]
            u = abs(float(fill["units"]))
            return OrderResult(success=True, order_id=fill["id"], symbol=symbol, side=exit_side,
                               quantity=u, price=float(fill["price"]), filled_quantity=u, status="filled")
        return OrderResult(success=False, error=data.get("errorMessage", f"HTTP {status}"))

    # ── Cuenta ──

    async def get_balance(self) -> Dict[str, float]:
        if self.dry_run or self._simulation_mode:
            return dict(self._simulated_balance)
        if not self._session:
            raise RuntimeError("Sin cuenta de OANDA conectada")
        url = f"{self.base_url}/accounts/{self.account_id}/summary"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"OANDA no regresó el saldo (HTTP {resp.status})")
            account = (await resp.json()).get("account", {})
        return {account.get("currency", "USD"): float(account.get("balance", 0))}

    async def get_positions(self) -> List[Position]:
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())
        if not self._session:
            raise RuntimeError("Sin cuenta de OANDA conectada")
        url = f"{self.base_url}/accounts/{self.account_id}/openPositions"
        async with self._session.get(url, headers=self._headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"OANDA no regresó las posiciones (HTTP {resp.status})")
            data = await resp.json()
        positions = []
        for pos in data.get("positions", []):
            long_units = float(pos.get("long", {}).get("units", 0))
            short_units = float(pos.get("short", {}).get("units", 0))
            if long_units > 0:
                positions.append(Position(symbol=pos["instrument"], side="long", quantity=long_units,
                                          entry_price=float(pos["long"].get("averagePrice", 0)),
                                          unrealized_pnl=float(pos["long"].get("unrealizedPL", 0))))
            if short_units < 0:
                positions.append(Position(symbol=pos["instrument"], side="short", quantity=abs(short_units),
                                          entry_price=float(pos["short"].get("averagePrice", 0)),
                                          unrealized_pnl=float(pos["short"].get("unrealizedPL", 0))))
        return positions

    def is_market_open(self) -> bool:
        """Forex: de domingo 5pm a viernes 5pm hora de Nueva York (no considera feriados)."""
        now = datetime.now(pytz.timezone('America/New_York'))
        if now.weekday() == 5:
            return False
        if now.weekday() == 6 and now.hour < 17:
            return False
        if now.weekday() == 4 and now.hour >= 17:
            return False
        return True
