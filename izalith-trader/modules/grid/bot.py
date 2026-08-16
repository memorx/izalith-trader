"""
Izalith Trading System - Grid Trading Bot
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter
from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger, log_trade

logger = get_logger("grid")


@dataclass
class GridLevel:
    """Represents a single grid level."""
    id: Optional[int]
    price: float
    side: str  # buy, sell
    quantity: float
    status: str = "pending"  # pending, filled
    filled_at: Optional[datetime] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "price": self.price,
            "side": self.side,
            "quantity": self.quantity,
            "status": self.status,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None
        }


class GridBot:
    """Grid trading bot for range-bound markets."""

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
        self.grids: Dict[str, List[GridLevel]] = {}
        self._running = False
        self._total_pnl: float = 0.0

    async def initialize(self) -> None:
        """Initialize grid from config."""
        enabled_assets = self.config.get("grid_enabled_assets", ["USDT/USDC"])
        total_investment = self.config.get("grid_total_investment", 1000.0)
        num_levels = self.config.get("grid_levels", 10)
        range_low = self.config.get("grid_range_low", 0.995)
        range_high = self.config.get("grid_range_high", 1.005)

        for symbol in enabled_assets:
            # Check for existing grid in database
            existing_levels = await self.db.get_grid_levels(symbol)

            if existing_levels:
                self.grids[symbol] = [
                    GridLevel(
                        id=level["id"],
                        price=level["level_price"],
                        side=level["side"],
                        quantity=level["quantity"],
                        status=level["status"],
                        filled_at=datetime.fromisoformat(level["filled_at"]) if level.get("filled_at") else None
                    )
                    for level in existing_levels
                ]
                logger.info(f"Loaded existing grid for {symbol}: {len(self.grids[symbol])} levels")
            else:
                # Create new grid
                await self._create_grid(symbol, range_low, range_high, num_levels, total_investment)

    async def _create_grid(
        self,
        symbol: str,
        range_low: float,
        range_high: float,
        num_levels: int,
        total_investment: float
    ) -> None:
        """Create a new grid for a symbol."""
        # Get current price
        price_data = await self.feed.get_price(symbol)
        if not price_data:
            logger.error(f"Could not get price for {symbol}")
            return

        current_price = price_data.last

        # Calculate level spacing
        level_spacing = (range_high - range_low) / (num_levels - 1)
        investment_per_level = total_investment / num_levels

        levels = []

        for i in range(num_levels):
            level_price = range_low + (i * level_spacing)
            quantity = investment_per_level / level_price

            # Levels below current price are buys, above are sells
            side = "buy" if level_price < current_price else "sell"

            # Insert into database
            level_id = await self.db.insert_grid_level(
                symbol=symbol,
                level_price=level_price,
                side=side,
                quantity=quantity
            )

            levels.append(GridLevel(
                id=level_id,
                price=level_price,
                side=side,
                quantity=quantity
            ))

        self.grids[symbol] = levels
        logger.info(f"Created grid for {symbol}: {num_levels} levels from {range_low:.4f} to {range_high:.4f}")

    async def run(self) -> None:
        """Main bot loop."""
        await self.initialize()
        self._running = True

        logger.info(f"Grid bot started with {len(self.grids)} grids")

        while self._running:
            try:
                await self.check_and_execute()
            except Exception as e:
                logger.error(f"Error in grid loop: {e}")

            await asyncio.sleep(5)  # Check every 5 seconds

    async def stop(self) -> None:
        """Stop the bot."""
        self._running = False
        logger.info("Grid bot stopped")

    async def check_and_execute(self) -> None:
        """Check price levels and execute trades."""
        for symbol, levels in self.grids.items():
            # Get current price
            price_data = await self.feed.get_price(symbol)
            if not price_data:
                continue

            current_price = price_data.last

            # Get adapter
            adapter = self.adapters.get("crypto")
            if not adapter:
                continue

            for level in levels:
                if level.status != "pending":
                    continue

                triggered = False

                # Check if buy level triggered
                if level.side == "buy" and current_price <= level.price:
                    triggered = True

                # Check if sell level triggered
                elif level.side == "sell" and current_price >= level.price:
                    triggered = True

                if triggered:
                    await self._execute_level(symbol, level, current_price, adapter)

    async def _execute_level(
        self,
        symbol: str,
        level: GridLevel,
        current_price: float,
        adapter: BaseAdapter
    ) -> None:
        """Execute a grid level trade."""
        result = await adapter.place_market_order(symbol, level.side, level.quantity)

        if result.success:
            # Mark level as filled
            level.status = "filled"
            level.filled_at = datetime.now(timezone.utc)

            if level.id:
                await self.db.update_grid_level(level.id, "filled")

            log_trade("grid", symbol, level.side, current_price, level.quantity)

            # Create opposite order
            new_side = "sell" if level.side == "buy" else "buy"

            # Calculate profit for this trade
            if level.side == "buy":
                profit_price = level.price * 1.001  # 0.1% profit target
            else:
                profit_price = level.price * 0.999

            new_id = await self.db.insert_grid_level(
                symbol=symbol,
                level_price=profit_price,
                side=new_side,
                quantity=level.quantity
            )

            # Add new level
            self.grids[symbol].append(GridLevel(
                id=new_id,
                price=profit_price,
                side=new_side,
                quantity=level.quantity
            ))

            logger.info(
                f"Grid {level.side.upper()} filled: {symbol} @ {current_price:.4f} - "
                f"Created new {new_side.upper()} @ {profit_price:.4f}"
            )

            # Track P/L
            if level.side == "sell":
                pnl = (current_price - level.price) * level.quantity
                self._total_pnl += pnl
        else:
            logger.error(f"Grid order failed: {result.error}")

    async def reset_grid(self, symbol: str) -> None:
        """Reset the grid for a symbol."""
        await self.db.clear_grid_levels(symbol)
        if symbol in self.grids:
            del self.grids[symbol]

        # Reinitialize
        total_investment = self.config.get("grid_total_investment", 1000.0)
        num_levels = self.config.get("grid_levels", 10)
        range_low = self.config.get("grid_range_low", 0.995)
        range_high = self.config.get("grid_range_high", 1.005)

        await self._create_grid(symbol, range_low, range_high, num_levels, total_investment)
        logger.info(f"Grid reset for {symbol}")

    def get_status(self) -> Dict:
        """Get current grid bot status."""
        status = {
            "running": self._running,
            "total_pnl": self._total_pnl,
            "grids": {}
        }

        for symbol, levels in self.grids.items():
            pending = [l for l in levels if l.status == "pending"]
            filled = [l for l in levels if l.status == "filled"]

            pending_buys = [l for l in pending if l.side == "buy"]
            pending_sells = [l for l in pending if l.side == "sell"]

            status["grids"][symbol] = {
                "total_levels": len(levels),
                "pending_buys": len(pending_buys),
                "pending_sells": len(pending_sells),
                "filled": len(filled),
                "levels": [l.to_dict() for l in levels if l.status == "pending"]
            }

        return status
