"""
Izalith Trading System - Forex Adapter (OANDA API v3)
"""
import aiohttp
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import pytz

from .base import BaseAdapter, PriceData, OHLCV, OrderResult, Position
from core.utils.logger import get_logger

logger = get_logger("forex")

# OANDA API URLs
PRACTICE_URL = "https://api-fxpractice.oanda.com/v3"
LIVE_URL = "https://api-fxtrade.oanda.com/v3"

# Simulated base prices for fallback
SIMULATED_FOREX_PRICES = {
    "EUR_USD": 1.0850,
    "GBP_USD": 1.2650,
    "USD_JPY": 154.50,
    "EUR/USD": 1.0850,
    "GBP/USD": 1.2650,
}


class ForexAdapter(BaseAdapter):
    """OANDA forex adapter with simulation fallback."""

    market = "forex"

    def __init__(
        self,
        account_id: str = "",
        api_key: str = "",
        practice: bool = True,
        dry_run: bool = True
    ):
        super().__init__(dry_run)
        self.account_id = account_id
        self.api_key = api_key
        self.practice = practice
        self.base_url = PRACTICE_URL if practice else LIVE_URL
        self._session: Optional[aiohttp.ClientSession] = None
        self._simulation_mode = False
        self._simulated_prices = SIMULATED_FOREX_PRICES.copy()

    @property
    def _headers(self) -> Dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def connect(self) -> bool:
        """Connect to OANDA with fallback to simulation."""
        try:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

            if self.api_key and self.account_id:
                # Verify credentials
                try:
                    async with self._session.get(
                        f"{self.base_url}/accounts/{self.account_id}",
                        headers=self._headers
                    ) as resp:
                        if resp.status == 200:
                            self.connected = True
                            self._simulation_mode = False
                            mode = "practice" if self.practice else "live"
                            logger.info(f"OANDA connected ({mode})")
                            return True
                        else:
                            logger.warning(f"OANDA auth failed: {resp.status}, using simulation")
                            await self._enable_simulation_mode()
                            return True
                except Exception as e:
                    logger.warning(f"OANDA connection failed: {e}, using simulation")
                    await self._enable_simulation_mode()
                    return True
            else:
                # No credentials - use simulation
                await self._enable_simulation_mode()
                return True

        except Exception as e:
            logger.warning(f"OANDA setup failed: {e}, using simulation")
            await self._enable_simulation_mode()
            return True

    async def _enable_simulation_mode(self):
        """Enable simulation mode."""
        self._simulation_mode = True
        self.connected = True
        logger.info("Forex adapter running in SIMULATION mode")

    def _normalize_symbol(self, symbol: str) -> str:
        """Convert symbol to OANDA format (EUR_USD)."""
        return symbol.replace("/", "_")

    def _denormalize_symbol(self, symbol: str) -> str:
        """Convert OANDA format back to standard (EUR/USD)."""
        return symbol.replace("_", "/")

    def _generate_simulated_price(self, symbol: str) -> float:
        """Generate a realistic simulated forex price."""
        normalized = self._normalize_symbol(symbol)

        if normalized not in self._simulated_prices:
            self._simulated_prices[normalized] = SIMULATED_FOREX_PRICES.get(normalized, 1.0)

        base_price = self._simulated_prices[normalized]

        # Forex has smaller movements - about 0.01% std dev
        change_pct = random.gauss(0, 0.0001)
        new_price = base_price * (1 + change_pct)

        self._simulated_prices[normalized] = new_price
        return new_price

    def _get_simulated_price(self, symbol: str) -> PriceData:
        """Get simulated forex price data."""
        price = self._generate_simulated_price(symbol)
        spread = price * 0.00002  # 2 pip spread

        return PriceData(
            symbol=symbol,
            market=self.market,
            timestamp=datetime.now(timezone.utc),
            bid=price - spread/2,
            ask=price + spread/2,
            last=price,
            spread=spread,
            spread_pct=(spread/price) * 100,
            volume=random.uniform(100000, 500000)
        )

    def _generate_simulated_ohlcv(self, symbol: str, limit: int) -> List[OHLCV]:
        """Generate simulated OHLCV data for forex."""
        normalized = self._normalize_symbol(symbol)
        base_price = self._simulated_prices.get(normalized, 1.0)
        candles = []
        now = datetime.now(timezone.utc)

        for i in range(limit, 0, -1):
            timestamp = now - timedelta(hours=i)

            # Forex has lower volatility
            volatility = 0.001  # 0.1% per candle
            open_price = base_price * (1 + random.gauss(0, volatility))
            close_price = open_price * (1 + random.gauss(0, volatility))
            high_price = max(open_price, close_price) * (1 + abs(random.gauss(0, volatility/2)))
            low_price = min(open_price, close_price) * (1 - abs(random.gauss(0, volatility/2)))
            volume = random.uniform(10000, 50000)

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
            self._simulated_prices[normalized] = candles[-1].close

        return candles

    async def disconnect(self) -> None:
        """Disconnect from OANDA."""
        if self._session:
            await self._session.close()
            self._session = None
        self.connected = False
        logger.info("OANDA disconnected")

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price for a symbol."""
        if self._simulation_mode:
            return self._get_simulated_price(symbol)

        if not self._session:
            return self._get_simulated_price(symbol)

        oanda_symbol = self._normalize_symbol(symbol)

        try:
            url = f"{self.base_url}/accounts/{self.account_id}/pricing"
            params = {"instruments": oanda_symbol}

            async with self._session.get(url, headers=self._headers, params=params) as resp:
                if resp.status != 200:
                    return self._get_simulated_price(symbol)

                data = await resp.json()
                prices = data.get("prices", [])

                if not prices:
                    return self._get_simulated_price(symbol)

                price_data = prices[0]
                bid = float(price_data.get("bids", [{}])[0].get("price", 0))
                ask = float(price_data.get("asks", [{}])[0].get("price", 0))

                return PriceData(
                    symbol=symbol,
                    market=self.market,
                    timestamp=datetime.now(timezone.utc),
                    bid=bid,
                    ask=ask,
                    last=(bid + ask) / 2,
                    volume=0
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
        timeframe: str = "H1",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get OHLCV candle data."""
        if self._simulation_mode:
            return self._generate_simulated_ohlcv(symbol, limit)

        if not self._session:
            return self._generate_simulated_ohlcv(symbol, limit)

        oanda_symbol = self._normalize_symbol(symbol)

        # Map timeframe to OANDA granularity
        tf_map = {
            "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
            "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
            "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30",
            "H1": "H1", "H4": "H4", "D": "D", "W": "W"
        }
        granularity = tf_map.get(timeframe, "H1")

        try:
            url = f"{self.base_url}/instruments/{oanda_symbol}/candles"
            params = {
                "granularity": granularity,
                "count": limit,
                "price": "M"
            }

            async with self._session.get(url, headers=self._headers, params=params) as resp:
                if resp.status != 200:
                    return self._generate_simulated_ohlcv(symbol, limit)

                data = await resp.json()
                candles = data.get("candles", [])

                return [
                    OHLCV(
                        timestamp=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                        open=float(c["mid"]["o"]),
                        high=float(c["mid"]["h"]),
                        low=float(c["mid"]["l"]),
                        close=float(c["mid"]["c"]),
                        volume=c.get("volume", 0)
                    )
                    for c in candles
                    if c.get("complete", True)
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
            logger.info(f"[SIMULATED] Market {side} {quantity} {symbol} @ {price:.5f}")
            return result

        if not self._session or not self.api_key:
            return OrderResult(success=False, error="Not authenticated")

        oanda_symbol = self._normalize_symbol(symbol)
        units = int(quantity) if side == "buy" else int(-quantity)

        try:
            url = f"{self.base_url}/accounts/{self.account_id}/orders"
            body = {
                "order": {
                    "type": "MARKET",
                    "instrument": oanda_symbol,
                    "units": str(units),
                    "timeInForce": "FOK"
                }
            }

            async with self._session.post(url, headers=self._headers, json=body) as resp:
                data = await resp.json()

                if resp.status == 201 and "orderFillTransaction" in data:
                    fill = data["orderFillTransaction"]
                    return OrderResult(
                        success=True,
                        order_id=fill["id"],
                        symbol=symbol,
                        side=side,
                        quantity=abs(float(fill["units"])),
                        price=float(fill["price"]),
                        filled_quantity=abs(float(fill["units"])),
                        status="filled"
                    )
                else:
                    error = data.get("errorMessage", "Unknown error")
                    return OrderResult(success=False, error=error)

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
            logger.info(f"[SIMULATED] Limit {side} {quantity} {symbol} @ {price:.5f}")
            return result

        if not self._session or not self.api_key:
            return OrderResult(success=False, error="Not authenticated")

        oanda_symbol = self._normalize_symbol(symbol)
        units = int(quantity) if side == "buy" else int(-quantity)

        try:
            url = f"{self.base_url}/accounts/{self.account_id}/orders"
            body = {
                "order": {
                    "type": "LIMIT",
                    "instrument": oanda_symbol,
                    "units": str(units),
                    "price": str(price),
                    "timeInForce": "GTC"
                }
            }

            async with self._session.post(url, headers=self._headers, json=body) as resp:
                data = await resp.json()

                if resp.status == 201:
                    order = data.get("orderCreateTransaction", {})
                    return OrderResult(
                        success=True,
                        order_id=order.get("id", ""),
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        price=price,
                        status="pending"
                    )
                else:
                    error = data.get("errorMessage", "Unknown error")
                    return OrderResult(success=False, error=error)

        except Exception as e:
            logger.error(f"Error placing limit order: {e}")
            return OrderResult(success=False, error=str(e))

    async def get_balance(self) -> Dict[str, float]:
        """Get account balance."""
        if self.dry_run or self._simulation_mode:
            return self._simulated_balance

        if not self._session or not self.api_key:
            return self._simulated_balance

        try:
            url = f"{self.base_url}/accounts/{self.account_id}/summary"

            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    account = data.get("account", {})
                    return {
                        "USD": float(account.get("balance", 0)),
                        "marginAvailable": float(account.get("marginAvailable", 0)),
                        "unrealizedPL": float(account.get("unrealizedPL", 0))
                    }

        except Exception as e:
            logger.error(f"Error fetching balance: {e}")

        return self._simulated_balance

    async def get_positions(self) -> List[Position]:
        """Get open positions."""
        if self.dry_run or self._simulation_mode:
            return list(self._simulated_positions.values())

        if not self._session or not self.api_key:
            return list(self._simulated_positions.values())

        try:
            url = f"{self.base_url}/accounts/{self.account_id}/openPositions"

            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    positions = []

                    for pos in data.get("positions", []):
                        long_units = float(pos.get("long", {}).get("units", 0))
                        short_units = float(pos.get("short", {}).get("units", 0))

                        if long_units > 0:
                            positions.append(Position(
                                symbol=self._denormalize_symbol(pos["instrument"]),
                                side="long",
                                quantity=long_units,
                                entry_price=float(pos["long"].get("averagePrice", 0)),
                                unrealized_pnl=float(pos["long"].get("unrealizedPL", 0))
                            ))

                        if short_units < 0:
                            positions.append(Position(
                                symbol=self._denormalize_symbol(pos["instrument"]),
                                side="short",
                                quantity=abs(short_units),
                                entry_price=float(pos["short"].get("averagePrice", 0)),
                                unrealized_pnl=float(pos["short"].get("unrealizedPL", 0))
                            ))

                    return positions

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")

        return list(self._simulated_positions.values())

    def is_market_open(self) -> bool:
        """
        Check if forex market is open.
        Forex: Sunday 5pm EST - Friday 5pm EST
        """
        est = pytz.timezone('US/Eastern')
        now = datetime.now(est)

        # Market closed on Saturday
        if now.weekday() == 5:
            return False

        # Market closed on Sunday before 5pm
        if now.weekday() == 6 and now.hour < 17:
            return False

        # Market closed on Friday after 5pm
        if now.weekday() == 4 and now.hour >= 17:
            return False

        return True

    @property
    def is_simulating(self) -> bool:
        """Check if adapter is in simulation mode."""
        return self._simulation_mode
