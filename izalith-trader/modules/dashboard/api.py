"""
Izalith Trading System - Dashboard API
"""
import asyncio
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from core.utils.logger import get_logger

logger = get_logger("dashboard")


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


def create_app(
    feed: UnifiedFeed,
    portfolio: PortfolioTracker,
    db: Database,
    module_status: Dict[str, Any] = None
) -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(
        title="Izalith Trading System",
        description="Trading system dashboard API",
        version="1.0.0"
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # WebSocket manager
    manager = ConnectionManager()

    # Store references
    app.state.feed = feed
    app.state.portfolio = portfolio
    app.state.db = db
    app.state.module_status = module_status or {}

    # Mount static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Serve the dashboard HTML."""
        html_path = static_dir / "index.html"
        if html_path.exists():
            return HTMLResponse(content=html_path.read_text())
        return HTMLResponse(content="<h1>Izalith Trading System</h1><p>Dashboard not found</p>")

    @app.get("/api/status")
    async def get_status():
        """Get overall system status."""
        market_status = feed.get_market_status()
        portfolio_summary = portfolio.get_summary()

        return {
            "status": "running",
            "markets": market_status,
            "portfolio": portfolio_summary,
            "modules": app.state.module_status
        }

    @app.get("/api/prices")
    async def get_prices():
        """Get current prices for all assets."""
        prices = await feed.get_all_prices()
        return {
            symbol: price.to_dict()
            for symbol, price in prices.items()
        }

    @app.get("/api/price/{symbol}")
    async def get_price(symbol: str):
        """Get current price for a specific symbol."""
        price = await feed.get_price(symbol)
        if price:
            return price.to_dict()
        return JSONResponse(status_code=404, content={"error": "Symbol not found"})

    @app.get("/api/positions")
    async def get_positions():
        """Get all open positions."""
        positions = portfolio.get_all_positions()
        return [pos.to_dict() for pos in positions]

    @app.get("/api/trades")
    async def get_trades(limit: int = 50):
        """Get recent trades."""
        trades = await db.get_recent_trades(limit)
        return trades

    @app.get("/api/trades/today")
    async def get_trades_today():
        """Get today's trades."""
        trades = await db.get_trades_today()
        return trades

    @app.get("/api/signals")
    async def get_signals():
        """Get today's signals."""
        signals = await db.get_signals_today()
        return signals

    @app.get("/api/performance")
    async def get_performance():
        """Get performance metrics."""
        daily_pnl = await db.get_daily_pnl()
        trade_count = await db.get_trade_count_today()
        snapshots = await db.get_portfolio_snapshots(24)

        return {
            "daily_pnl": daily_pnl,
            "trade_count_today": trade_count,
            "portfolio_value": portfolio.get_total_value_usd(),
            "unrealized_pnl": portfolio.get_unrealized_pnl(),
            "snapshots": snapshots
        }

    @app.get("/api/dca/status")
    async def get_dca_status():
        """Get DCA bot status."""
        orders = await db.get_dca_orders()
        return {
            "orders": orders,
            "active": "dca" in app.state.module_status.get("active_modules", [])
        }

    @app.get("/api/grid/status")
    async def get_grid_status():
        """Get Grid bot status."""
        # Get grid levels from database
        grids = {}
        for symbol in ["USDT/USDC"]:  # Add more as needed
            levels = await db.get_grid_levels(symbol)
            if levels:
                grids[symbol] = levels

        return {
            "grids": grids,
            "active": "grid" in app.state.module_status.get("active_modules", [])
        }

    @app.websocket("/ws/prices")
    async def websocket_prices(websocket: WebSocket):
        """WebSocket endpoint for real-time price updates."""
        await manager.connect(websocket)

        try:
            while True:
                # Get latest prices
                prices = await feed.get_all_prices()
                price_data = {
                    symbol: price.to_dict()
                    for symbol, price in prices.items()
                }

                await websocket.send_json({
                    "type": "prices",
                    "data": price_data
                })

                await asyncio.sleep(2)  # Update every 2 seconds

        except WebSocketDisconnect:
            manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            manager.disconnect(websocket)

    return app
