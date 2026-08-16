"""
Izalith Trading System - DCA Bot
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import pytz

from core.adapters.base import BaseAdapter
from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade
from config.assets import ASSETS, get_asset

logger = get_logger("dca")

# Day name to weekday number
WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6
}


@dataclass
class DCAOrder:
    """Represents a scheduled DCA order."""
    symbol: str
    amount_usd: float
    frequency: str  # hourly, daily, weekly, biweekly, monthly
    next_execution: datetime
    day_of_week: Optional[int] = None  # 0=Monday for weekly
    hour: int = 9  # Hour of day (UTC)
    total_invested: float = 0.0
    total_quantity: float = 0.0
    last_execution: Optional[datetime] = None

    @property
    def avg_price(self) -> float:
        """Average purchase price."""
        if self.total_quantity == 0:
            return 0.0
        return self.total_invested / self.total_quantity

    def should_execute_now(self) -> bool:
        """Check if this order should execute now."""
        now = datetime.now(timezone.utc)
        return now >= self.next_execution

    def calculate_next_execution(self) -> datetime:
        """Calculate the next execution time."""
        now = datetime.now(timezone.utc)

        if self.frequency == "hourly":
            # Next hour
            next_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        elif self.frequency == "daily":
            # Tomorrow at specified hour
            next_time = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
            if next_time <= now:
                next_time += timedelta(days=1)

        elif self.frequency == "weekly":
            # Next occurrence of day_of_week at specified hour
            days_ahead = self.day_of_week - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_time = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
            next_time += timedelta(days=days_ahead)

        elif self.frequency == "biweekly":
            # Every 2 weeks on specified day
            days_ahead = self.day_of_week - now.weekday()
            if days_ahead <= 0:
                days_ahead += 14
            next_time = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
            next_time += timedelta(days=days_ahead)

        elif self.frequency == "monthly":
            # First day of next month
            if now.month == 12:
                next_time = now.replace(year=now.year + 1, month=1, day=1,
                                       hour=self.hour, minute=0, second=0, microsecond=0)
            else:
                next_time = now.replace(month=now.month + 1, day=1,
                                       hour=self.hour, minute=0, second=0, microsecond=0)

        else:
            # Default to daily
            next_time = now + timedelta(days=1)

        self.next_execution = next_time
        return next_time


class DCABot:
    """Dollar Cost Averaging bot."""

    def __init__(
        self,
        adapters: Dict[str, BaseAdapter],
        feed: UnifiedFeed,
        portfolio: PortfolioTracker,
        db: Database,
        config: dict
    ):
        self.adapters = adapters
        self.feed = feed
        self.portfolio = portfolio
        self.db = db
        self.config = config
        self.orders: Dict[str, DCAOrder] = {}
        self._running = False

    async def initialize(self) -> None:
        """Initialize DCA orders from config and database."""
        # Get config values
        enabled_assets = self.config.get("dca_enabled_assets", [])
        amount_usd = self.config.get("dca_amount_usd", 50.0)
        frequency = self.config.get("dca_frequency", "weekly")
        day_of_week = WEEKDAY_MAP.get(self.config.get("dca_day_of_week", "monday").lower(), 0)
        hour = self.config.get("dca_hour", 9)

        # Load existing orders from database
        db_orders = await self.db.get_dca_orders()
        db_orders_map = {o["symbol"]: o for o in db_orders}

        # Create or update orders
        for symbol in enabled_assets:
            if symbol in db_orders_map:
                # Load from database
                db_order = db_orders_map[symbol]
                next_exec = datetime.fromisoformat(db_order["next_execution"]) if db_order.get("next_execution") else None
                order = DCAOrder(
                    symbol=symbol,
                    amount_usd=db_order["amount_usd"],
                    frequency=db_order["frequency"],
                    next_execution=next_exec or datetime.now(timezone.utc),
                    day_of_week=day_of_week,
                    hour=hour,
                    total_invested=db_order.get("total_invested", 0),
                    total_quantity=db_order.get("total_quantity", 0)
                )
                if not next_exec:
                    order.calculate_next_execution()
            else:
                # Create new order
                order = DCAOrder(
                    symbol=symbol,
                    amount_usd=amount_usd,
                    frequency=frequency,
                    next_execution=datetime.now(timezone.utc),
                    day_of_week=day_of_week,
                    hour=hour
                )
                order.calculate_next_execution()

                # Save to database
                await self.db.upsert_dca_order(
                    symbol=symbol,
                    amount_usd=amount_usd,
                    frequency=frequency,
                    next_execution=order.next_execution.isoformat()
                )

            self.orders[symbol] = order
            logger.info(f"DCA order loaded: {symbol} ${amount_usd} {frequency} (next: {order.next_execution.strftime('%Y-%m-%d %H:%M')} UTC)")

    async def run(self) -> None:
        """Main bot loop."""
        await self.initialize()
        self._running = True

        logger.info(f"DCA bot started with {len(self.orders)} orders")

        while self._running:
            try:
                await self.execute_scheduled_buys()
            except Exception as e:
                logger.error(f"Error in DCA loop: {e}")

            await asyncio.sleep(60)  # Check every minute

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        logger.info("DCA bot stopped")

    async def execute_scheduled_buys(self) -> None:
        """Execute any scheduled DCA buys."""
        for symbol, order in self.orders.items():
            if not order.should_execute_now():
                continue

            try:
                await self._execute_buy(order)
            except Exception as e:
                logger.error(f"Failed to execute DCA buy for {symbol}: {e}")

    async def _execute_buy(self, order: DCAOrder) -> None:
        """Execute a single DCA buy."""
        symbol = order.symbol

        # Get price
        price_data = await self.feed.get_price(symbol)
        if not price_data:
            logger.warning(f"Could not get price for {symbol}")
            return

        price = price_data.last

        # Calculate quantity
        quantity = order.amount_usd / price

        # Get the appropriate adapter
        try:
            asset = get_asset(symbol)
            adapter = self.adapters.get(asset.market.value)
        except ValueError:
            adapter = self.adapters.get("crypto")

        if not adapter:
            logger.error(f"No adapter found for {symbol}")
            return

        # Check if market is open
        if not adapter.is_market_open():
            logger.info(f"Market closed for {symbol}, will retry later")
            return

        # Execute order
        result = await adapter.place_market_order(symbol, "buy", quantity)

        if result.success:
            # Update order stats
            order.total_invested += order.amount_usd
            order.total_quantity += quantity
            order.last_execution = datetime.now(timezone.utc)
            order.calculate_next_execution()

            # Update database
            await self.db.update_dca_execution(
                symbol=symbol,
                quantity=quantity,
                price=price,
                next_execution=order.next_execution.isoformat()
            )

            # Log the trade
            log_trade("dca", symbol, "buy", price, quantity)

            logger.info(
                f"DCA executed: {symbol} - Bought {quantity:.6f} @ ${price:,.2f} "
                f"(Total: ${order.total_invested:,.2f}, Avg: ${order.avg_price:,.2f})"
            )

            # Add to portfolio
            await self.portfolio.add_position(
                symbol=symbol,
                market=price_data.market,
                side="buy",
                quantity=quantity,
                price=price,
                module="dca"
            )
        else:
            logger.error(f"DCA buy failed for {symbol}: {result.error}")

    def get_status(self) -> Dict:
        """Get current DCA bot status."""
        return {
            "running": self._running,
            "orders": {
                symbol: {
                    "amount_usd": order.amount_usd,
                    "frequency": order.frequency,
                    "next_execution": order.next_execution.isoformat() if order.next_execution else None,
                    "total_invested": order.total_invested,
                    "total_quantity": order.total_quantity,
                    "avg_price": order.avg_price
                }
                for symbol, order in self.orders.items()
            }
        }
