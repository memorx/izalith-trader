"""
Izalith Trading System - Portfolio Tracker
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.utils.logger import get_logger
from core.utils.database import Database
from core.data.feed import UnifiedFeed

logger = get_logger("portfolio")


@dataclass
class Position:
    """Represents an open position."""
    symbol: str
    market: str
    side: str  # long, short
    quantity: float
    entry_price: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float = 0.0
    trade_id: Optional[int] = None

    @property
    def value(self) -> float:
        """Current position value."""
        return self.quantity * self.current_price

    @property
    def cost_basis(self) -> float:
        """Original cost of position."""
        return self.quantity * self.entry_price

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized profit/loss."""
        if self.side == "long":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P/L as percentage."""
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100

    def to_dict(self) -> Dict:
        return {
            'symbol': self.symbol,
            'market': self.market,
            'side': self.side,
            'quantity': self.quantity,
            'entry_price': self.entry_price,
            'entry_time': self.entry_time.isoformat(),
            'current_price': self.current_price,
            'value': self.value,
            'cost_basis': self.cost_basis,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_pnl_pct': self.unrealized_pnl_pct
        }


class PortfolioTracker:
    """
    Tracks portfolio positions, cash, and performance.
    """

    def __init__(
        self,
        db: Database,
        feed: UnifiedFeed,
        initial_cash: float = 10000.0
    ):
        self.db = db
        self.feed = feed
        self.positions: Dict[str, Position] = {}
        self.cash: Dict[str, float] = {
            "USD": initial_cash,
            "USDT": initial_cash
        }
        self._realized_pnl_today: float = 0.0
        self._last_snapshot_time: Optional[datetime] = None

    async def add_position(
        self,
        symbol: str,
        market: str,
        side: str,
        quantity: float,
        price: float,
        module: str = "manual",
        strategy: Optional[str] = None
    ) -> Position:
        """Add a new position or average into existing."""
        # Record trade in database
        trade_id = await self.db.insert_trade(
            module=module,
            symbol=symbol,
            market=market,
            side=side,
            entry_price=price,
            quantity=quantity,
            strategy=strategy
        )

        if symbol in self.positions:
            # Average into existing position
            pos = self.positions[symbol]
            if pos.side == side:
                # Same direction - average in
                total_qty = pos.quantity + quantity
                avg_price = ((pos.entry_price * pos.quantity) + (price * quantity)) / total_qty
                pos.entry_price = avg_price
                pos.quantity = total_qty
            else:
                # Opposite direction - reduce position
                if quantity >= pos.quantity:
                    # Close entire position and open new
                    await self.close_position(symbol, price)
                    remaining = quantity - pos.quantity
                    if remaining > 0:
                        pos = Position(
                            symbol=symbol,
                            market=market,
                            side=side,
                            quantity=remaining,
                            entry_price=price,
                            current_price=price,
                            trade_id=trade_id
                        )
                        self.positions[symbol] = pos
                else:
                    pos.quantity -= quantity
        else:
            # New position
            pos = Position(
                symbol=symbol,
                market=market,
                side=side,
                quantity=quantity,
                entry_price=price,
                current_price=price,
                trade_id=trade_id
            )
            self.positions[symbol] = pos

        # Update cash
        cost = quantity * price
        quote_currency = "USD" if market in ["stock", "forex"] else "USDT"
        if side == "buy":
            self.cash[quote_currency] = self.cash.get(quote_currency, 0) - cost
        else:
            self.cash[quote_currency] = self.cash.get(quote_currency, 0) + cost

        logger.info(f"Position opened: {side.upper()} {quantity} {symbol} @ ${price:,.2f}")
        return pos

    async def close_position(
        self,
        symbol: str,
        exit_price: float
    ) -> Optional[float]:
        """Close an existing position and return realized P/L."""
        if symbol not in self.positions:
            logger.warning(f"No position to close for {symbol}")
            return None

        pos = self.positions[symbol]
        pnl = pos.unrealized_pnl

        # Update trade in database
        if pos.trade_id:
            await self.db.close_trade(pos.trade_id, exit_price)

        # Update cash
        quote_currency = "USD" if pos.market in ["stock", "forex"] else "USDT"
        if pos.side == "long":
            self.cash[quote_currency] = self.cash.get(quote_currency, 0) + (pos.quantity * exit_price)
        else:
            self.cash[quote_currency] = self.cash.get(quote_currency, 0) - (pos.quantity * exit_price)

        self._realized_pnl_today += pnl

        # Remove position
        del self.positions[symbol]

        pnl_pct = (pnl / pos.cost_basis) * 100 if pos.cost_basis else 0
        logger.info(f"Position closed: {symbol} P/L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)")

        return pnl

    async def update_prices(self) -> None:
        """Update current prices for all positions."""
        if not self.positions:
            return

        symbols = list(self.positions.keys())
        prices = await self.feed.get_prices(symbols)

        for symbol, price_data in prices.items():
            if symbol in self.positions:
                self.positions[symbol].current_price = price_data.last

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get a specific position."""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self.positions.values())

    def get_total_value_usd(self) -> float:
        """Get total portfolio value in USD."""
        cash_total = sum(self.cash.values())
        positions_total = sum(pos.value for pos in self.positions.values())
        return cash_total + positions_total

    def get_positions_value(self) -> float:
        """Get total value of all positions."""
        return sum(pos.value for pos in self.positions.values())

    def get_cash_usd(self) -> float:
        """Get total cash in USD."""
        return sum(self.cash.values())

    def get_unrealized_pnl(self) -> float:
        """Get total unrealized P/L."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_realized_pnl_today(self) -> float:
        """Get realized P/L for today."""
        return self._realized_pnl_today

    async def take_snapshot(self) -> None:
        """Save a portfolio snapshot to the database."""
        await self.update_prices()

        total_value = self.get_total_value_usd()
        cash = self.get_cash_usd()
        positions_value = self.get_positions_value()
        unrealized_pnl = self.get_unrealized_pnl()
        realized_pnl = self.get_realized_pnl_today()

        await self.db.insert_portfolio_snapshot(
            total_value_usd=total_value,
            cash_usd=cash,
            positions_value_usd=positions_value,
            unrealized_pnl=unrealized_pnl,
            realized_pnl_today=realized_pnl
        )

        self._last_snapshot_time = datetime.now(timezone.utc)
        logger.debug(f"Portfolio snapshot: ${total_value:,.2f} (P/L: ${unrealized_pnl:+,.2f})")

    def get_summary(self) -> Dict:
        """Get portfolio summary."""
        return {
            "total_value_usd": self.get_total_value_usd(),
            "cash_usd": self.get_cash_usd(),
            "positions_value_usd": self.get_positions_value(),
            "unrealized_pnl": self.get_unrealized_pnl(),
            "realized_pnl_today": self.get_realized_pnl_today(),
            "position_count": len(self.positions),
            "positions": [p.to_dict() for p in self.positions.values()]
        }

    def reset_daily_stats(self) -> None:
        """Reset daily statistics (call at market open)."""
        self._realized_pnl_today = 0.0
        logger.info("Daily stats reset")
