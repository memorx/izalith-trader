"""
Izalith Trading System - Unified Data Feed
"""
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Callable, Awaitable
import pandas as pd

from core.adapters.base import BaseAdapter, PriceData, OHLCV
from core.utils.logger import get_logger
from config.assets import ASSETS, Asset, Market

logger = get_logger("feed")


class UnifiedFeed:
    """
    Unified data feed that aggregates data from all market adapters.
    """

    def __init__(self, adapters: Dict[str, BaseAdapter]):
        """
        Initialize the unified feed.

        Args:
            adapters: Dict mapping market names to adapter instances
                      e.g., {"crypto": CryptoAdapter(), "forex": ForexAdapter(), ...}
        """
        self.adapters = adapters
        self._price_cache: Dict[str, PriceData] = {}
        self._subscribers: List[Callable[[Dict[str, PriceData]], Awaitable[None]]] = []
        self._running = False

    def _get_adapter_for_symbol(self, symbol: str) -> Optional[BaseAdapter]:
        """Get the appropriate adapter for a symbol."""
        # Try to find the asset definition
        normalized = symbol.upper().replace("-", "/")

        if normalized in ASSETS:
            asset = ASSETS[normalized]
            market_name = asset.market.value
            return self.adapters.get(market_name)

        # Try forex format
        forex_format = symbol.upper().replace("/", "_")
        if forex_format in ASSETS:
            return self.adapters.get("forex")

        # Fallback: guess based on symbol format
        if "_" in symbol:
            return self.adapters.get("forex")
        elif "/" in symbol:
            return self.adapters.get("crypto")
        else:
            return self.adapters.get("stock")

    async def get_price(self, symbol: str) -> Optional[PriceData]:
        """Get current price for a symbol."""
        adapter = self._get_adapter_for_symbol(symbol)
        if not adapter:
            logger.warning(f"No adapter found for {symbol}")
            return None

        price = await adapter.get_price(symbol)
        if price:
            self._price_cache[symbol] = price
        return price

    async def get_prices(self, symbols: List[str]) -> Dict[str, PriceData]:
        """Get current prices for multiple symbols."""
        # Group symbols by adapter
        by_adapter: Dict[str, List[str]] = {}
        for symbol in symbols:
            adapter = self._get_adapter_for_symbol(symbol)
            if adapter:
                market = adapter.market
                if market not in by_adapter:
                    by_adapter[market] = []
                by_adapter[market].append(symbol)

        # Fetch prices in parallel
        tasks = []
        for market, market_symbols in by_adapter.items():
            adapter = self.adapters.get(market)
            if adapter:
                tasks.append(adapter.get_prices(market_symbols))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Combine results
        prices = {}
        for result in results:
            if isinstance(result, list):
                for price in result:
                    prices[price.symbol] = price
                    self._price_cache[price.symbol] = price

        return prices

    async def get_all_prices(self) -> Dict[str, PriceData]:
        """Get prices for all configured assets."""
        symbols = list(ASSETS.keys())
        return await self.get_prices(symbols)

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100
    ) -> List[OHLCV]:
        """Get OHLCV data for a symbol."""
        adapter = self._get_adapter_for_symbol(symbol)
        if not adapter:
            logger.warning(f"No adapter found for {symbol}")
            return []

        return await adapter.get_ohlcv(symbol, timeframe, limit)

    async def get_ohlcv_df(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100
    ) -> pd.DataFrame:
        """Get OHLCV data as a pandas DataFrame."""
        ohlcv = await self.get_ohlcv(symbol, timeframe, limit)

        if not ohlcv:
            return pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        data = [c.to_dict() for c in ohlcv]
        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        return df

    def get_cached_price(self, symbol: str) -> Optional[PriceData]:
        """Get cached price without fetching."""
        return self._price_cache.get(symbol)

    def get_all_cached_prices(self) -> Dict[str, PriceData]:
        """Get all cached prices."""
        return self._price_cache.copy()

    async def subscribe(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, PriceData]], Awaitable[None]],
        interval: float = 1.0
    ) -> None:
        """
        Subscribe to price updates.

        Args:
            symbols: List of symbols to subscribe to
            callback: Async function called with price updates
            interval: Update interval in seconds
        """
        self._subscribers.append(callback)

        async def update_loop():
            self._running = True
            while self._running:
                try:
                    prices = await self.get_prices(symbols)
                    if prices:
                        await callback(prices)
                except Exception as e:
                    logger.error(f"Error in price update: {e}")

                await asyncio.sleep(interval)

        asyncio.create_task(update_loop())

    def unsubscribe_all(self) -> None:
        """Stop all subscriptions."""
        self._running = False
        self._subscribers.clear()

    async def check_adapters(self) -> Dict[str, bool]:
        """Check connectivity of all adapters."""
        status = {}
        for name, adapter in self.adapters.items():
            status[name] = adapter.connected
        return status

    def get_market_status(self) -> Dict[str, Dict]:
        """Get market open/closed status for all adapters."""
        status = {}
        for name, adapter in self.adapters.items():
            status[name] = {
                "connected": adapter.connected,
                "market_open": adapter.is_market_open(),
                "dry_run": adapter.dry_run
            }
        return status
