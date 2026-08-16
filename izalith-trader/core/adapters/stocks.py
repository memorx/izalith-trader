"""
Izalith Trading System - Stocks Adapter (Alpaca)
"""
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import pytz

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position
from core.utils.logger import get_logger

logger = get_logger("stocks")

# Simulated base prices for fallback
SIMULATED_STOCK_PRICES = {
    "VTI": 280.0,
    "VOO": 520.0,
    "SPY": 570.0,
    "AAPL": 195.0,
    "MSFT": 420.0,
    "GOOGL": 175.0,
}


class StocksAdapter(BaseAdapter):
    """Alpaca stocks adapter with simulation fallback."""

    market = "stock"

    def __init__(
        self,
        api_key: str = "",
        secret_key: str = "",
        paper: bool = True,
        dry_run: bool = True
    ):
        super().__init__(dry_run)
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self._api = None
        self._data_client = None
        self._simulation_mode = False
        self._simulated_prices = SIMULATED_STOCK_PRICES.copy()

    async def connect(self) -> bool:
        """Connect to Alpaca with fallback to simulation."""
        try:
            if self.api_key and self.secret_key:
                try:
                    from alpaca.trading.client import TradingClient
                    from alpaca.data.historical import StockHistoricalDataClient

                    self._api = TradingClient(
                        api_key=self.api_key,
                        secret_key=self.secret_key,
                        paper=self.paper
                    )

                    self._data_client = StockHistoricalDataClient(
                        api_key=self.api_key,
                        secret_key=self.secret_key
                    )

                    # Verify connection
                    account = self._api.get_account()
                    self.connected = True
                    self._simulation_mode = False
                    mode = "paper" if self.paper else "live"
                    logger.info(f"Alpaca connected ({mode})")
                    return True

                except ImportError:
                    logger.warning("alpaca-trade-api not installed, using simulation mode")
                    await self._enable_simulation_mode()
                    return True
                except Exception as e:
                    logger.warning(f"Alpaca connection failed: {e}, using simulation mode")
                    await self._enable_simulation_mode()
                    return True
            else:
                # No credentials - use simulation
                await self._enable_simulation_mode()
                return True

        except Exception as e:
            logger.warning(f"Alpaca setup failed: {e}, using simulation mode")
            await self._enable_simulation_mode()
            return True

    async def _enable_simulation_mode(self):
        """Enable simulation mode."""
        self._simulation_mode = True
        self.connected = True
        logger.info("Stocks adapter running in SIMULATION mode")

    def _generate_simulated_price(self, symbol: str) -> float:
        """Generate a realistic simulated stock price."""
        if symbol not in self._simulated_prices:
            self._simulated_prices[symbol] = SIMULATED_STOCK_PRICES.get(symbol, 100.0)

        base_price = self._simulated_prices[symbol]

        # Stocks have moderate volatility - about 0.05% std dev
        change_pct = random.gauss(0, 0.0005)
        new_price = base_price * (1 + change_pct)

        self._simulated_prices[symbol] = new_price
        return new_price

    def _get_simulated_price(self, symbol: str) -> PriceData:
        """Get simulated stock price data."""
        price = self._generate_simulated_price(symbol)
        spread = price * 0.0001  # 0.01% spread

        base = SIMULATED_STOCK_PRICES.get(symbol, 100.0)
        change_24h = ((price - base) / base) * 100

        return PriceData(
            symbol=symbol,
            market=self.market,
            timestamp=datetime.now(timezone.utc),
            bid=price - spread/2,
            ask=price + spread/2,
            last=price,
            spread=spread,
            spread_pct=(spread/price) * 100,
            volume=random.uniform(100000, 1000000),
            high_24h=price * 1.01,
            low_24h=price * 0.99,
            change_24h_pct=change_24h
        )

    def _generate_simulated_ohlcv(self, symbol: str, limit: int) -> List[OHLCV]:
        """Generate simulated OHLCV data for stocks."""
        base_price = self._simulated_prices.get(symbol, SIMULATED_STOCK_PRICES.get(symbol, 100.0))
        candles = []
        now = datetime.now(timezone.utc)

        for i in range(limit, 0, -1):
            timestamp = now - timedelta(hours=i)

            # Stocks have moderate volatility
            volatility = 0.003  # 0.3% per candle
            open_price = base_price * (1 + random.gauss(0, volatility))
            close_price = open_price * (1 + random.gauss(0, volatility))
            high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, volatility/2)))
            low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, volatility/2)))
            volume = random.uniform(50000, 200000)

            candles.append(OHLCV(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume
            ))

            base_price = close_price

        if candles:
            self._simulated_prices[symbol] = candles[-1].close

        return candles

    async def disconnect(self) -> None:
        """Disconnect from Alpaca."""
        self._api = None
        self._data_client = None
        self.connected = False
        logger.info("Alpaca disconnected")

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price for a symbol."""
        if self._simulation_mode:
            return self._get_simulated_price(symbol)

        if not self._data_client:
            return self._get_simulated_price(symbol)

        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            from alpaca.data.requests import StockLatestTradeRequest

            # Get latest trade
            trade_req = StockLatestTradeRequest(symbol_or_symbols=symbol)
            trades = self._data_client.get_stock_latest_trade(trade_req)
            trade = trades.get(symbol)

            # Get latest quote
            quote_req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self._data_client.get_stock_latest_quote(quote_req)
            quote = quotes.get(symbol)

            if trade:
                return PriceData(
                    symbol=symbol,
                    market=self.market,
                    timestamp=trade.timestamp.replace(tzinfo=timezone.utc),
                    bid=quote.bid_price if quote else trade.price,
                    ask=quote.ask_price if quote else trade.price,
                    last=trade.price,
                    volume=trade.size
                )

        except Exception as e:
            logger.debug(f"Falling back to simulation for {symbol}: {e}")

        return self._get_simulated_price(symbol)

    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        """Get current prices for multiple symbols."""
        prices = []
        for symbol in symbols:
            price = await self.get_price(symbol)
            if price:
                prices.append(price)
        return prices

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get OHLCV candle data."""
        if self._simulation_mode:
            return self._generate_simulated_ohlcv(symbol, limit)

        if not self._data_client:
            return self._generate_simulated_ohlcv(symbol, limit)

        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            # Map timeframe
            tf_map = {
                "1m": TimeFrame.Minute,
                "1h": TimeFrame.Hour,
                "1d": TimeFrame.Day,
                "1w": TimeFrame.Week,
            }
            alpaca_tf = tf_map.get(timeframe, TimeFrame.Hour)

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=30)

            request = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=alpaca_tf,
                start=start,
                end=end,
                limit=limit
            )

            bars = self._data_client.get_stock_bars(request)
            symbol_bars = bars.get(symbol, [])

            return [
                OHLCV(
                    timestamp=bar.timestamp.replace(tzinfo=timezone.utc),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume
                )
                for bar in symbol_bars
            ]

        except Exception as e:
            logger.debug(f"Falling back to simulation for OHLCV: {e}")
            return self._generate_simulated_ohlcv(symbol, limit)

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float
    ) -> OrderResult:
        """Place a market order."""
        price_data = await self.get_price(symbol)
        price = price_data.last if price_data else 0

        if self.dry_run or self._simulation_mode:
            result = self._simulate_order(symbol, side, quantity, price)
            logger.info(f"[SIMULATED] Market {side} {quantity:.4f} {symbol} @ ${price:.2f}")
            return result

        if not self._api:
            return OrderResult(success=False, error="Not authenticated")

        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY
            )

            order = self._api.submit_order(request)
            logger.info(f"Market {side} {quantity} {symbol} - Order ID: {order.id}")

            return OrderResult(
                success=True,
                order_id=str(order.id),
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=float(order.filled_avg_price or 0),
                filled_quantity=float(order.filled_qty or 0),
                status=order.status.value
            )

        except Exception as e:
            logger.error(f"Error placing market order: {e}")
            return OrderResult(success=False, error=str(e))

    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float
    ) -> OrderResult:
        """Place a limit order."""
        if self.dry_run or self._simulation_mode:
            result = self._simulate_order(symbol, side, quantity, price)
            logger.info(f"[SIMULATED] Limit {side} {quantity:.4f} {symbol} @ ${price:.2f}")
            return result

        if not self._api:
            return OrderResult(success=False, error="Not authenticated")

        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.GTC,
                limit_price=price
            )

            order = self._api.submit_order(request)
            logger.info(f"Limit {side} {quantity} {symbol} @ ${price:.2f} - Order ID: {order.id}")

            return OrderResult(
                success=True,
                order_id=str(order.id),
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                status=order.status.value
            )

        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            return OrderResult(success=False, error=str(e))

    async def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        if self.dry_run or self._simulation_mode:
            return self._simulated_balance

        if not self._api:
            return self._simulated_balance

        try:
            account = self._api.get_account()
            return {
                "USD": float(account.cash),
                "buying_power": float(account.buying_power),
                "portfolio_value": float(account.portfolio_value),
                "equity": float(account.equity)
            }

        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return self._simulated_balance

    async def get_positions(self) -> List[Position]:
        """Get open positions."""
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())

        if not self._api:
            return list(self._simulated_positions.values())

        try:
            positions = self._api.get_all_positions()
            return [
                Position(
                    symbol=pos.symbol,
                    side="long" if float(pos.qty) > 0 else "short",
                    quantity=abs(float(pos.qty)),
                    entry_price=float(pos.avg_entry_price),
                    current_price=float(pos.current_price),
                    unrealized_pnl=float(pos.unrealized_pl),
                    unrealized_pnl_pct=float(pos.unrealized_plpc) * 100
                )
                for pos in positions
            ]

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return list(self._simulated_positions.values())

    def is_market_open(self) -> bool:
        """
        Check if US stock market is open.
        NYSE: Monday-Friday 9:30am - 4:00pm EST
        """
        est = pytz.timezone('US/Eastern')
        now = datetime.now(est)

        # Weekend
        if now.weekday() >= 5:
            return False

        # Check hours
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

        return market_open <= now <= market_close

    async def cancel_order(self, order_id: str, symbol: str = "") -> bool:
        """Cancel an open order."""
        if self.dry_run or self._simulation_mode:
            logger.info(f"[SIMULATED] Cancelled order {order_id}")
            return True

        if not self._api:
            return False

        try:
            self._api.cancel_order_by_id(order_id)
            logger.info(f"Cancelled order {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    @property
    def is_simulating(self) -> bool:
        """Check if adapter is in simulation mode."""
        return self._simulation_mode
