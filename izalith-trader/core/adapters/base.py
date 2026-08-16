"""
Izalith Trading System - Base Adapter
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


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
            'change_24h_pct': self.change_24h_pct
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
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side,
            'quantity': self.quantity,
            'price': self.price,
            'filled_quantity': self.filled_quantity,
            'status': self.status,
            'error': self.error,
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


class BaseAdapter(ABC):
    """Abstract base class for market adapters."""

    market: str = ""
    connected: bool = False

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._simulated_positions: Dict[str, Position] = {}
        self._simulated_balance: Dict[str, float] = {"USD": 10000.0, "USDT": 10000.0}

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the exchange/broker."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the exchange/broker."""
        pass

    @abstractmethod
    async def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price for a symbol."""
        pass

    @abstractmethod
    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        """Get current prices for multiple symbols."""
        pass

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get OHLCV candle data."""
        pass

    @abstractmethod
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> OrderResult:
        """Place a market order."""
        pass

    @abstractmethod
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> OrderResult:
        """Place a limit order."""
        pass

    @abstractmethod
    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        pass

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """Get open positions."""
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        pass

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order. Default implementation for dry run."""
        return True

    def _simulate_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> OrderResult:
        """Simulate an order for dry run mode."""
        import uuid

        order_id = str(uuid.uuid4())[:8]

        # Update simulated positions
        if symbol in self._simulated_positions:
            pos = self._simulated_positions[symbol]
            if side == "buy":
                new_qty = pos.quantity + quantity
                pos.entry_price = ((pos.entry_price * pos.quantity) + (price * quantity)) / new_qty
                pos.quantity = new_qty
            else:
                pos.quantity -= quantity
                if pos.quantity <= 0:
                    del self._simulated_positions[symbol]
        elif side == "buy":
            self._simulated_positions[symbol] = Position(
                symbol=symbol,
                side="long",
                quantity=quantity,
                entry_price=price,
                current_price=price
            )

        return OrderResult(
            success=True,
            order_id=f"SIM_{order_id}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            filled_quantity=quantity,
            status="filled"
        )
