"""
Izalith Trading System - Crypto Adapter (Binance via ccxt)
"""
import asyncio
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position
from core.utils.logger import get_logger

logger = get_logger("crypto")

# Simulated base prices for fallback
SIMULATED_PRICES = {
    "BTC/USDT": 67500.0,
    "ETH/USDT": 3450.0,
    "SOL/USDT": 145.0,
    "DOGE/USDT": 0.12,
    "USDT/USDC": 1.0001,
}


class CryptoAdapter(BaseAdapter):
    """Binance crypto adapter using ccxt with simulation fallback."""

    market = "crypto"

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        testnet: bool = True,
        dry_run: bool = True
    ):
        super().__init__(dry_run)
        self.api_key = api_key
        self.secret = secret
        self.testnet = testnet
        self._exchange = None
        self._simulation_mode = False
        self._simulated_prices = SIMULATED_PRICES.copy()
        self._price_history: Dict[str, List[float]] = {}

    async def connect(self) -> bool:
        """Connect to Binance with fallback to simulation mode."""
        try:
            import ccxt.async_support as ccxt

            options = {
                'enableRateLimit': True,
                'rateLimit': 100,
                'timeout': 10000,  # 10 second timeout
            }

            if self.api_key and self.secret:
                self._exchange = ccxt.binance({
                    'apiKey': self.api_key,
                    'secret': self.secret,
                    'sandbox': self.testnet,
                    'options': options
                })
            else:
                # Public API only - use production for public data
                self._exchange = ccxt.binance({
                    'sandbox': False,
                    'options': options
                })

            # Try to load markets with timeout
            try:
                await asyncio.wait_for(
                    self._exchange.load_markets(),
                    timeout=15.0
                )
                self.connected = True
                self._simulation_mode = False

                mode = "testnet" if self.testnet else "live"
                auth = "authenticated" if self.api_key else "public"
                logger.info(f"Binance connected ({mode}, {auth})")
                return True

            except asyncio.TimeoutError:
                logger.warning("Binance connection timed out, using simulation mode")
                await self._enable_simulation_mode()
                return True

        except ImportError:
            logger.warning("ccxt not installed, using simulation mode")
            await self._enable_simulation_mode()
            return True

        except Exception as e:
            logger.warning(f"Binance connection failed: {e}, using simulation mode")
            await self._enable_simulation_mode()
            return True

    async def _enable_simulation_mode(self):
        """Enable simulation mode with realistic price generation."""
        self._simulation_mode = True
        self.connected = True
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None

        # Initialize price history for realistic simulation
        for symbol, base_price in SIMULATED_PRICES.items():
            self._price_history[symbol] = [base_price]
            self._simulated_prices[symbol] = base_price

        logger.info("Crypto adapter running in SIMULATION mode")

    def _generate_simulated_price(self, symbol: str) -> float:
        """Generate a realistic simulated price with random walk."""
        if symbol not in self._simulated_prices:
            self._simulated_prices[symbol] = 100.0
            self._price_history[symbol] = [100.0]

        base_price = self._simulated_prices[symbol]

        # Random walk: small percentage change
        change_pct = random.gauss(0, 0.001)  # 0.1% std dev
        new_price = base_price * (1 + change_pct)

        # Keep price history for OHLCV simulation
        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append(new_price)

        # Keep last 200 prices
        if len(self._price_history[symbol]) > 200:
            self._price_history[symbol] = self._price_history[symbol][-200:]

        self._simulated_prices[symbol] = new_price
        return new_price

    def _generate_simulated_ohlcv(self, symbol: str, limit: int) -> List[OHLCV]:
        """Generate simulated OHLCV data."""
        if symbol not in self._simulated_prices:
            self._simulated_prices[symbol] = SIMULATED_PRICES.get(symbol, 100.0)

        base_price = self._simulated_prices[symbol]
        candles = []
        now = datetime.now(timezone.utc)

        for i in range(limit, 0, -1):
            timestamp = now - timedelta(hours=i)

            # Generate realistic OHLCV
            volatility = 0.005  # 0.5% per candle
            open_price = base_price * (1 + random.gauss(0, volatility))
            close_price = open_price * (1 + random.gauss(0, volatility))
            high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, volatility/2)))
            low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, volatility/2)))
            volume = random.uniform(1000, 10000) * base_price

            candles.append(OHLCV(
                timestamp=timestamp,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume
            ))

            base_price = close_price

        # Update current price to last close
        if candles:
            self._simulated_prices[symbol] = candles[-1].close

        return candles

    async def disconnect(self) -> None:
        """Disconnect from Binance."""
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None
        self.connected = False
        logger.info("Binance disconnected")

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price for a symbol."""
        if self._simulation_mode:
            return self._get_simulated_price(symbol)

        if not self._exchange:
            return self._get_simulated_price(symbol)

        try:
            ticker = await self._exchange.fetch_ticker(symbol)

            return PriceData(
                symbol=symbol,
                market=self.market,
                timestamp=datetime.now(timezone.utc),
                bid=ticker.get('bid', 0) or 0,
                ask=ticker.get('ask', 0) or 0,
                last=ticker.get('last', 0) or 0,
                volume=ticker.get('quoteVolume', 0) or 0,
                high_24h=ticker.get('high', 0) or 0,
                low_24h=ticker.get('low', 0) or 0,
                change_24h_pct=ticker.get('percentage', 0) or 0
            )

        except Exception as e:
            logger.debug(f"Falling back to simulation for {symbol}: {e}")
            return self._get_simulated_price(symbol)

    def _get_simulated_price(self, symbol: str) -> PriceData:
        """Get simulated price data."""
        price = self._generate_simulated_price(symbol)
        spread = price * 0.0001  # 0.01% spread

        # Simulate 24h stats
        base = SIMULATED_PRICES.get(symbol, 100.0)
        change_24h = ((price - base) / base) * 100

        return PriceData(
            symbol=symbol,
            market=self.market,
            timestamp=datetime.now(timezone.utc),
            bid=price - spread/2,
            ask=price + spread/2,
            last=price,
            volume=random.uniform(1000000, 5000000),
            high_24h=price * 1.02,
            low_24h=price * 0.98,
            change_24h_pct=change_24h
        )

    async def get_prices(self, symbols: List[str]) -> List[PriceData]:
        """Get current prices for multiple symbols."""
        if self._simulation_mode:
            return [self._get_simulated_price(s) for s in symbols]

        if not self._exchange:
            return [self._get_simulated_price(s) for s in symbols]

        try:
            tickers = await self._exchange.fetch_tickers(symbols)
            prices = []

            for symbol in symbols:
                if symbol in tickers:
                    ticker = tickers[symbol]
                    prices.append(PriceData(
                        symbol=symbol,
                        market=self.market,
                        timestamp=datetime.now(timezone.utc),
                        bid=ticker.get('bid', 0) or 0,
                        ask=ticker.get('ask', 0) or 0,
                        last=ticker.get('last', 0) or 0,
                        volume=ticker.get('quoteVolume', 0) or 0,
                        high_24h=ticker.get('high', 0) or 0,
                        low_24h=ticker.get('low', 0) or 0,
                        change_24h_pct=ticker.get('percentage', 0) or 0
                    ))
                else:
                    prices.append(self._get_simulated_price(symbol))

            return prices

        except Exception as e:
            logger.debug(f"Falling back to simulation for prices: {e}")
            return [self._get_simulated_price(s) for s in symbols]

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get OHLCV candle data."""
        if self._simulation_mode:
            return self._generate_simulated_ohlcv(symbol, limit)

        if not self._exchange:
            return self._generate_simulated_ohlcv(symbol, limit)

        try:
            ohlcv = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return [
                OHLCV(
                    timestamp=datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc),
                    open=candle[1],
                    high=candle[2],
                    low=candle[3],
                    close=candle[4],
                    volume=candle[5]
                )
                for candle in ohlcv
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
            logger.info(f"[SIMULATED] Market {side} {quantity:.6f} {symbol} @ ${price:,.2f}")
            return result

        if not self._exchange or not self.api_key:
            return OrderResult(success=False, error="Not authenticated")

        try:
            order = await self._exchange.create_market_order(symbol, side, quantity)
            logger.info(f"Market {side} {quantity} {symbol} - Order ID: {order['id']}")

            return OrderResult(
                success=True,
                order_id=order['id'],
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=order.get('average', 0) or order.get('price', 0),
                filled_quantity=order.get('filled', quantity),
                status=order.get('status', 'filled')
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
            logger.info(f"[SIMULATED] Limit {side} {quantity:.6f} {symbol} @ ${price:,.2f}")
            return result

        if not self._exchange or not self.api_key:
            return OrderResult(success=False, error="Not authenticated")

        try:
            order = await self._exchange.create_limit_order(symbol, side, quantity, price)
            logger.info(f"Limit {side} {quantity} {symbol} @ ${price:,.2f} - Order ID: {order['id']}")

            return OrderResult(
                success=True,
                order_id=order['id'],
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                filled_quantity=order.get('filled', 0),
                status=order.get('status', 'open')
            )

        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            return OrderResult(success=False, error=str(e))

    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        if self.dry_run or self._simulation_mode:
            return self._simulated_balance

        if not self._exchange or not self.api_key:
            return self._simulated_balance

        try:
            balance = await self._exchange.fetch_balance()
            return {
                currency: data['free']
                for currency, data in balance.items()
                if isinstance(data, dict) and data.get('free', 0) > 0
            }

        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return self._simulated_balance

    async def get_positions(self) -> List[Position]:
        """Get open positions."""
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())

        # For spot trading, positions are derived from non-zero balances
        balance = await self.get_balance()
        positions = []

        for currency, amount in balance.items():
            if currency not in ['USD', 'USDT', 'USDC', 'BUSD'] and amount > 0:
                symbol = f"{currency}/USDT"
                price_data = await self.get_price(symbol)
                if price_data:
                    positions.append(Position(
                        symbol=symbol,
                        side="long",
                        quantity=amount,
                        entry_price=0,  # Unknown for spot
                        current_price=price_data.last
                    ))

        return positions

    def is_market_open(self) -> bool:
        """Crypto markets are always open."""
        return True

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an open order."""
        if self.dry_run or self._simulation_mode:
            logger.info(f"[SIMULATED] Cancelled order {order_id}")
            return True

        if not self._exchange or not self.api_key:
            return False

        try:
            await self._exchange.cancel_order(order_id, symbol)
            logger.info(f"Cancelled order {order_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    @property
    def is_simulating(self) -> bool:
        """Check if adapter is in simulation mode."""
        return self._simulation_mode
