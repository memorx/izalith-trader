"""
Izalith Trading System - Risk Manager
"""
from typing import Tuple, Dict, Optional
from datetime import datetime, timezone

from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger
from .strategies.base import TradeSignal

logger = get_logger("risk")


class RiskManager:
    """
    Manages risk for the scalper module.

    Checks:
    - Max positions per market
    - Max total positions
    - Max daily loss not exceeded
    - Market is open
    - No duplicate position in same asset
    """

    def __init__(
        self,
        portfolio: PortfolioTracker,
        db: Database,
        config: dict
    ):
        self.portfolio = portfolio
        self.db = db

        # Config
        self.max_position_usd = config.get("scalper_max_position_usd", 500.0)
        self.max_daily_trades = config.get("scalper_max_daily_trades", 20)
        self.max_daily_loss_usd = config.get("scalper_max_daily_loss_usd", 100.0)
        self.max_positions_per_market = config.get("max_positions_per_market", 3)
        self.max_total_positions = config.get("max_total_positions", 5)

    async def can_open_position(
        self,
        symbol: str,
        signal: TradeSignal,
        adapter_is_open: bool = True
    ) -> Tuple[bool, str]:
        """
        Check if a new position can be opened.

        Returns:
            Tuple of (can_open, reason)
        """
        # Check market is open
        if not adapter_is_open:
            return False, "Market is closed"

        # Check for existing position in same symbol
        existing = self.portfolio.get_position(symbol)
        if existing:
            return False, f"Already have position in {symbol}"

        # Check max positions per market
        market_positions = [
            p for p in self.portfolio.get_all_positions()
            if p.market == signal.strategy  # Simplification
        ]
        if len(market_positions) >= self.max_positions_per_market:
            return False, f"Max positions per market reached ({self.max_positions_per_market})"

        # Check max total positions
        total_positions = len(self.portfolio.get_all_positions())
        if total_positions >= self.max_total_positions:
            return False, f"Max total positions reached ({self.max_total_positions})"

        # Check daily trade count
        trade_count = await self.db.get_trade_count_today("scalper")
        if trade_count >= self.max_daily_trades:
            return False, f"Max daily trades reached ({self.max_daily_trades})"

        # Check daily loss
        daily_pnl = await self.db.get_daily_pnl()
        if daily_pnl <= -self.max_daily_loss_usd:
            return False, f"Max daily loss reached (${self.max_daily_loss_usd})"

        # Check confidence threshold
        if signal.confidence < 0.5:
            return False, f"Signal confidence too low ({signal.confidence:.0%})"

        return True, "OK"

    def calculate_position_size(
        self,
        symbol: str,
        signal: TradeSignal,
        current_price: float
    ) -> float:
        """
        Calculate appropriate position size based on risk parameters.

        Returns:
            Quantity to trade
        """
        # Base position size on max position USD
        base_size = self.max_position_usd / current_price

        # Adjust by confidence
        adjusted_size = base_size * signal.confidence

        # Calculate risk-adjusted size based on stop loss distance
        if signal.stop_loss > 0:
            stop_distance_pct = abs(current_price - signal.stop_loss) / current_price
            # Risk no more than 1% of max position on any single trade
            max_risk = self.max_position_usd * 0.01
            risk_adjusted_size = max_risk / (stop_distance_pct * current_price)
            adjusted_size = min(adjusted_size, risk_adjusted_size)

        # Check remaining daily loss budget
        remaining_loss_budget = self.max_daily_loss_usd + self.portfolio.get_realized_pnl_today()
        if remaining_loss_budget < 0:
            return 0.0

        return max(adjusted_size, 0.0)

    async def check_daily_limits(self) -> Dict:
        """Check current status against daily limits."""
        trade_count = await self.db.get_trade_count_today("scalper")
        daily_pnl = await self.db.get_daily_pnl()
        position_count = len(self.portfolio.get_all_positions())

        return {
            "trades_today": trade_count,
            "max_trades": self.max_daily_trades,
            "trades_remaining": max(0, self.max_daily_trades - trade_count),
            "daily_pnl": daily_pnl,
            "max_loss": self.max_daily_loss_usd,
            "loss_remaining": max(0, self.max_daily_loss_usd + daily_pnl),
            "positions_open": position_count,
            "max_positions": self.max_total_positions,
            "can_trade": trade_count < self.max_daily_trades and daily_pnl > -self.max_daily_loss_usd
        }

    def get_status(self) -> Dict:
        """Get risk manager status."""
        return {
            "max_position_usd": self.max_position_usd,
            "max_daily_trades": self.max_daily_trades,
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_positions_per_market": self.max_positions_per_market,
            "max_total_positions": self.max_total_positions
        }
