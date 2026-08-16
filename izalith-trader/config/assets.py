"""
Izalith Trading System - Asset Definitions
"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List


class Market(Enum):
    """Market types supported by the system."""
    CRYPTO = "crypto"
    FOREX = "forex"
    STOCK = "stock"


@dataclass
class Asset:
    """Represents a tradeable asset."""
    symbol: str
    market: Market
    base: str
    quote: str
    name: str
    min_qty: float = 0.0
    min_notional: float = 0.0
    price_precision: int = 2
    qty_precision: int = 4

    @property
    def adapter_symbol(self) -> str:
        """Get the symbol format used by the adapter."""
        if self.market == Market.FOREX:
            return f"{self.base}_{self.quote}"
        return f"{self.base}/{self.quote}"


# Define all supported assets
ASSETS: Dict[str, Asset] = {
    # Crypto (Binance)
    "BTC/USDT": Asset(
        symbol="BTC/USDT",
        market=Market.CRYPTO,
        base="BTC",
        quote="USDT",
        name="Bitcoin",
        min_qty=0.00001,
        min_notional=10.0,
        price_precision=2,
        qty_precision=5
    ),
    "ETH/USDT": Asset(
        symbol="ETH/USDT",
        market=Market.CRYPTO,
        base="ETH",
        quote="USDT",
        name="Ethereum",
        min_qty=0.0001,
        min_notional=10.0,
        price_precision=2,
        qty_precision=4
    ),
    "SOL/USDT": Asset(
        symbol="SOL/USDT",
        market=Market.CRYPTO,
        base="SOL",
        quote="USDT",
        name="Solana",
        min_qty=0.01,
        min_notional=10.0,
        price_precision=2,
        qty_precision=2
    ),
    "DOGE/USDT": Asset(
        symbol="DOGE/USDT",
        market=Market.CRYPTO,
        base="DOGE",
        quote="USDT",
        name="Dogecoin",
        min_qty=1.0,
        min_notional=10.0,
        price_precision=5,
        qty_precision=0
    ),
    "USDT/USDC": Asset(
        symbol="USDT/USDC",
        market=Market.CRYPTO,
        base="USDT",
        quote="USDC",
        name="Stablecoin Pair",
        min_qty=1.0,
        min_notional=10.0,
        price_precision=4,
        qty_precision=2
    ),

    # Forex (OANDA)
    "EUR_USD": Asset(
        symbol="EUR_USD",
        market=Market.FOREX,
        base="EUR",
        quote="USD",
        name="Euro vs Dollar",
        min_qty=1.0,
        min_notional=1.0,
        price_precision=5,
        qty_precision=0
    ),
    "GBP_USD": Asset(
        symbol="GBP_USD",
        market=Market.FOREX,
        base="GBP",
        quote="USD",
        name="Pound vs Dollar",
        min_qty=1.0,
        min_notional=1.0,
        price_precision=5,
        qty_precision=0
    ),

    # Stocks/ETFs (Alpaca)
    "VTI": Asset(
        symbol="VTI",
        market=Market.STOCK,
        base="VTI",
        quote="USD",
        name="Vanguard Total Stock Market",
        min_qty=0.001,
        min_notional=1.0,
        price_precision=2,
        qty_precision=3
    ),
    "VOO": Asset(
        symbol="VOO",
        market=Market.STOCK,
        base="VOO",
        quote="USD",
        name="Vanguard S&P 500",
        min_qty=0.001,
        min_notional=1.0,
        price_precision=2,
        qty_precision=3
    ),
    "SPY": Asset(
        symbol="SPY",
        market=Market.STOCK,
        base="SPY",
        quote="USD",
        name="SPDR S&P 500",
        min_qty=0.001,
        min_notional=1.0,
        price_precision=2,
        qty_precision=3
    ),
}


def get_asset(symbol: str) -> Asset:
    """Get an asset by symbol."""
    # Normalize symbol
    normalized = symbol.upper().replace("-", "/").replace("_", "/")

    # Try direct lookup
    if normalized in ASSETS:
        return ASSETS[normalized]

    # Try forex format
    forex_format = symbol.upper().replace("/", "_")
    if forex_format in ASSETS:
        return ASSETS[forex_format]

    # Try stock format (no quote)
    if normalized in ASSETS:
        return ASSETS[normalized]

    raise ValueError(f"Unknown asset: {symbol}")


def get_assets_by_market(market: Market) -> List[Asset]:
    """Get all assets for a specific market."""
    return [a for a in ASSETS.values() if a.market == market]


def get_crypto_assets() -> List[Asset]:
    """Get all crypto assets."""
    return get_assets_by_market(Market.CRYPTO)


def get_forex_assets() -> List[Asset]:
    """Get all forex assets."""
    return get_assets_by_market(Market.FOREX)


def get_stock_assets() -> List[Asset]:
    """Get all stock assets."""
    return get_assets_by_market(Market.STOCK)
