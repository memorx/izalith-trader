"""
Izalith Trading System - Base Adapter

Reglas para todos los adaptadores:
- Un precio inventado nunca se hace pasar por real. La simulación sólo corre si se
  pidió (simulate=True) o si se permitió como respaldo (allow_simulation=True), y todo
  lo que sale de ella lleva simulated=True.
- Si la fuente real falla en una consulta, se regresa None o [] y se registra el error;
  no se rellena con datos aleatorios.
- En modo real, un saldo que no se pudo leer es un error, no 10,000 de mentira.
"""
import random
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from core.utils.logger import get_logger

logger = get_logger("adapters")


@dataclass
class PriceData:
    """Unified price data structure."""
    symbol: str
    market: str  # crypto, forex, stock
    timestamp: datetime
    bid: float
    ask: float
    last: float
    spread: float = 0.0
    spread_pct: float = 0.0
    volume: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    change_24h_pct: float = 0.0
    simulated: bool = False

    def __post_init__(self):
        if self.spread == 0 and self.bid > 0 and self.ask > 0:
            self.spread = self.ask - self.bid
        if self.spread_pct == 0 and self.last > 0:
            self.spread_pct = (self.spread / self.last) * 100

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'market': self.market,
            'timestamp': self.timestamp.isoformat(),
            'bid': self.bid,
            'ask': self.ask,
            'last': self.last,
            'spread': self.spread,
            'spread_pct': self.spread_pct,
            'volume': self.volume,
            'high_24h': self.high_24h,
            'low_24h': self.low_24h,
            'change_24h_pct': self.change_24h_pct,
            'simulated': self.simulated
        }


@dataclass
class OHLCV:
    """OHLCV candle data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    simulated: bool = False

    def to_dict(self) -> Dict:
        return {
            'timestamp': self.timestamp.isoformat(),
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume
        }


@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: str = ""
    symbol: str = ""
    side: str = ""  # buy, sell
    quantity: float = 0.0
    price: float = 0.0
    filled_quantity: float = 0.0
    status: str = ""  # pending, filled, partial, cancelled, failed
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    client_order_id: str = ""
    fee: float = 0.0              # comisión cobrada, en moneda de cotización
    simulated: bool = False

    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'order_id': self.order_id,
            'client_order_id': self.client_order_id,
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'price': self.price,
            'filled_quantity': self.filled_quantity,
            'status': self.status,
            'error': self.error,
            'fee': self.fee,
            'simulated': self.simulated,
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class Position:
    """Current position in an asset."""
    symbol: str
    side: str  # long, short
    quantity: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0

    def update_price(self, price: float):
        self.current_price = price
        if self.side == "long":
            self.unrealized_pnl = (price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - price) * self.quantity
        if self.entry_price > 0:
            self.unrealized_pnl_pct = (self.unrealized_pnl / (self.entry_price * self.quantity)) * 100


def new_client_order_id(prefix: str = "iz") -> str:
    """Id propio de la orden: si la respuesta se pierde, se puede preguntar por él antes de reintentar."""
    return f"{prefix}{uuid.uuid4().hex[:20]}"


class BaseAdapter(ABC):
    """Abstract base class for market adapters."""

    market: str = ""
    supports_short: bool = False          # ¿se puede abrir un corto en esta cuenta?
    sim_prices: Dict[str, float] = {}     # precio inicial de la simulación por símbolo
    sim_vol_per_hour: float = 0.005       # volatilidad por vela de 1h en la simulación
    sim_fee: float = 0.001                # comisión que cobra la simulación
    sim_min_fee: float = 0.0              # comisión mínima por orden en papel (IBKR: 1 dólar)

    def __init__(self, dry_run: bool = True, allow_simulation: bool = False, simulate: bool = False):
        self.dry_run = dry_run
        self.allow_simulation = allow_simulation or simulate
        self.force_simulation = simulate
        self.connected = False
        self.last_error: Optional[str] = None
        self._simulation_mode = False
        self._simulated_positions: Dict[str, Position] = {}
        self._simulated_balance: Dict[str, float] = {"USD": 10000.0, "USDT": 10000.0}
        self._sim_history: Dict[str, List[OHLCV]] = {}
        self._sim_last: Dict[str, float] = {}

    # ── Estado ──

    @property
    def is_simulating(self) -> bool:
        return self._simulation_mode

    @property
    def display_name(self) -> str:
        return self.market

    @property
    def source(self) -> str:
        if self._simulation_mode:
            return "simulado"
        return "real" if self.connected else "sin conexión"

    def _enter_simulation(self, reason: str) -> bool:
        """
        Se llama cuando la fuente real no está disponible.

        Regresa False siempre: el adaptador NO está conectado a la fuente real.
        """
        self.last_error = reason
        self.connected = False
        if self.allow_simulation:
            self._simulation_mode = True
            if self.force_simulation:
                logger.info(f"{self.market}: modo SIMULACIÓN (precios inventados)")
            else:
                logger.warning(f"{self.market}: {reason}. Modo SIMULACIÓN: los precios son inventados")
        else:
            self._simulation_mode = False
            # Sin llaves es una elección, no una falla
            log = logger.info if reason.startswith("sin llaves") else logger.error
            log(f"{self.market}: {reason}. Sin datos de este mercado (la simulación está apagada)")
        return False

    # ── Interfaz ──

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the exchange/broker. True sólo si quedó conectado a la fuente real."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def get_price(self, symbol: str) -> Optional[PriceData]:
        pass

    @abstractmethod
    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        pass

    @abstractmethod
    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> List[OHLCV]:
        """Velas CERRADAS, de la más vieja a la más nueva."""
        pass

    @abstractmethod
    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                 client_order_id: Optional[str] = None) -> OrderResult:
        pass

    @abstractmethod
    async def place_limit_order(self, symbol: str, side: str, quantity: float, price: float,
                                client_order_id: Optional[str] = None) -> OrderResult:
        pass

    @abstractmethod
    async def get_balance(self) -> Dict[str, float]:
        """Saldos libres por moneda. En real, lanza excepción si no se pudo leer."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        pass

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order. Default implementation for dry run."""
        return True

    async def place_notional_order(self, symbol: str, side: str, amount: float,
                                   client_order_id: Optional[str] = None) -> OrderResult:
        """Opera por monto en moneda de cotización. Por defecto convierte a cantidad con el precio actual."""
        price = await self.get_price(symbol)
        if not price or price.last <= 0:
            return OrderResult(success=False, symbol=symbol, side=side, status="failed",
                               error="Sin precio", client_order_id=client_order_id or "")
        ref = (price.ask if side == "buy" else price.bid) or price.last
        qty = self.amount_to_precision(symbol, amount / ref)
        return await self.place_market_order(symbol, side, qty, client_order_id)

    async def place_passive_order(self, symbol: str, side: str, quantity: float,
                                  client_order_id: Optional[str] = None, max_wait_s: float = 900) -> OrderResult:
        """Orden límite que espera en el libro (comisión de maker). Por defecto: orden a mercado."""
        return await self.place_market_order(symbol, side, quantity, client_order_id)

    async def place_passive_notional(self, symbol: str, side: str, amount: float,
                                     client_order_id: Optional[str] = None, max_wait_s: float = 900) -> OrderResult:
        """Como place_passive_order, pero por monto. Por defecto: compra por monto a mercado."""
        return await self.place_notional_order(symbol, side, amount, client_order_id)

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[OrderResult]:
        """Busca en el exchange una orden por nuestro id. None si no existe o no se puede saber."""
        return None

    def quote_currency(self, symbol: str) -> str:
        return symbol.split("/")[1] if "/" in symbol else "USD"

    async def close_position(self, symbol: str, side: str, quantity: float,
                             client_order_id: Optional[str] = None) -> OrderResult:
        """Cierra un largo (vendiendo) o un corto (comprando). Los adaptadores con hedging lo sobrescriben."""
        exit_side = "sell" if side == "long" else "buy"
        return await self.place_market_order(symbol, exit_side, quantity, client_order_id)

    def amount_to_precision(self, symbol: str, quantity: float) -> float:
        """Redondea la cantidad hacia abajo a lo que acepta el mercado."""
        return quantity

    def min_notional(self, symbol: str) -> float:
        """Monto mínimo por orden en moneda de cotización."""
        return 0.0

    # ── Simulación ──

    def _sim_series(self, symbol: str) -> List[OHLCV]:
        """Historial simulado persistente: se genera una vez y luego sólo se extiende."""
        hist = self._sim_history.get(symbol)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        vol = self.sim_vol_per_hour

        if not hist:
            price = self.sim_prices.get(symbol, 100.0)
            hist = []
            for i in range(500, 0, -1):
                hist.append(self._sim_candle(now - timedelta(hours=i), price, vol))
                price = hist[-1].close
            self._sim_history[symbol] = hist
            self._sim_last[symbol] = hist[-1].close

        # Extender hasta la última hora cerrada
        last_ts = hist[-1].timestamp
        while last_ts + timedelta(hours=1) < now:
            last_ts = last_ts + timedelta(hours=1)
            hist.append(self._sim_candle(last_ts, hist[-1].close, vol))
        if len(hist) > 1000:
            del hist[:-1000]
        return hist

    @staticmethod
    def _sim_candle(ts: datetime, start: float, vol: float) -> OHLCV:
        o = start
        c = o * (1 + random.gauss(0, vol))
        h = max(o, c) * (1 + abs(random.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(random.gauss(0, vol / 2)))
        return OHLCV(timestamp=ts, open=o, high=h, low=l, close=c,
                     volume=random.uniform(1000, 10000), simulated=True)

    def _sim_ohlcv(self, symbol: str, limit: int) -> List[OHLCV]:
        return list(self._sim_series(symbol)[-limit:])

    def _sim_price(self, symbol: str) -> PriceData:
        hist = self._sim_series(symbol)
        last = self._sim_last.get(symbol, hist[-1].close)
        price = last * (1 + random.gauss(0, self.sim_vol_per_hour / 20))
        self._sim_last[symbol] = price
        day = hist[-24:]
        spread = price * 0.0001
        return PriceData(
            symbol=symbol, market=self.market, timestamp=datetime.now(timezone.utc),
            bid=price - spread / 2, ask=price + spread / 2, last=price,
            volume=sum(c.volume for c in day),
            high_24h=max(c.high for c in day), low_24h=min(c.low for c in day),
            change_24h_pct=(price / day[0].open - 1) * 100,
            simulated=True,
        )

    def _simulate_order(self, symbol: str, side: str, quantity: float, price: float,
                        client_order_id: Optional[str] = None, fee_rate: Optional[float] = None) -> OrderResult:
        """Orden de papel: se llena completa al precio dado y cobra la comisión de la simulación."""
        if price <= 0:
            return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity,
                               status="failed", error="Sin precio para simular la orden", simulated=True)

        quote = self.quote_currency(symbol)
        notional = quantity * price
        fee = max(notional * (self.sim_fee if fee_rate is None else fee_rate), self.sim_min_fee)
        spot_cash = not self.supports_short and quote in self._simulated_balance
        if side == "buy" and spot_cash and self._simulated_balance[quote] < notional + fee - 1e-9:
            return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity, status="failed",
                               error=f"Saldo insuficiente de {quote}: hay {self._simulated_balance[quote]:.2f}",
                               simulated=True)

        pos = self._simulated_positions.get(symbol)
        if side == "sell" and not self.supports_short:
            held = pos.quantity if pos and pos.side == "long" else 0.0
            if quantity > held * (1 + 1e-9):
                return OrderResult(success=False, symbol=symbol, side=side, quantity=quantity, status="failed",
                                   error=f"Saldo insuficiente: se quieren vender {quantity} y hay {held}",
                                   simulated=True)

        if symbol in self._simulated_positions:
            pos = self._simulated_positions[symbol]
            same_dir = (pos.side == "long" and side == "buy") or (pos.side == "short" and side == "sell")
            if same_dir:
                new_qty = pos.quantity + quantity
                pos.entry_price = ((pos.entry_price * pos.quantity) + (price * quantity)) / new_qty
                pos.quantity = new_qty
            else:
                pos.quantity -= quantity
                if pos.quantity <= 1e-12:
                    del self._simulated_positions[symbol]
        else:
            self._simulated_positions[symbol] = Position(
                symbol=symbol, side="long" if side == "buy" else "short",
                quantity=quantity, entry_price=price, current_price=price)

        # En spot el saldo de papel se mueve con cada orden, para que quedarse sin dinero también pase en papel
        if spot_cash:
            if side == "buy":
                self._simulated_balance[quote] -= notional + fee
            else:
                self._simulated_balance[quote] += notional - fee

        return OrderResult(
            success=True,
            order_id=f"SIM_{uuid.uuid4().hex[:8]}",
            client_order_id=client_order_id or "",
            symbol=symbol, side=side, quantity=quantity, price=price,
            filled_quantity=quantity, status="filled",
            fee=fee, simulated=True,
        )
