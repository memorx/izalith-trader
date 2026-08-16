#!/usr/bin/env python3
"""
Izalith Trading System - Main Entry Point

Usage:
    python run.py                    # Run modules from .env
    python run.py --modules signals  # Run only signals
    python run.py --modules signals,dashboard  # Run signals and dashboard
    python run.py --list-modules     # List available modules
"""
import argparse
import asyncio
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import get_settings
from config.assets import ASSETS
from core.utils.logger import setup_logging, get_logger, log_startup_banner, log_module_status
from core.utils.database import get_database
from core.adapters.crypto import CryptoAdapter
from core.adapters.forex import ForexAdapter
from core.adapters.stocks import StocksAdapter
from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker

logger = get_logger("main")


class IzalithSystem:
    """Main trading system orchestrator."""

    AVAILABLE_MODULES = ["signals", "dca", "grid", "scalper", "dashboard"]

    def __init__(self, modules: Optional[List[str]] = None):
        self.settings = get_settings()
        self.modules = modules or self.settings.active_modules
        self.adapters: Dict = {}
        self.feed: Optional[UnifiedFeed] = None
        self.portfolio: Optional[PortfolioTracker] = None
        self.db = None
        self._tasks: List[asyncio.Task] = []
        self._running = False

    async def initialize(self) -> bool:
        """Initialize all components."""
        log_startup_banner()
        logger.info(f"Initializing Izalith Trading System...")
        logger.info(f"Active modules: {', '.join(self.modules)}")
        logger.info(f"DRY_RUN: {self.settings.dry_run}")

        # Setup logging
        setup_logging(
            level=self.settings.log_level,
            log_dir=self.settings.logs_dir,
            log_to_file=True
        )

        # Initialize database
        try:
            self.db = await get_database(self.settings.data_dir / "izalith.db")
            log_module_status("Database", "OK", str(self.settings.data_dir / "izalith.db"))
        except Exception as e:
            log_module_status("Database", "ERROR", str(e))
            return False

        # Initialize adapters
        await self._init_adapters()

        # Initialize feed
        self.feed = UnifiedFeed(self.adapters)

        # Initialize portfolio
        self.portfolio = PortfolioTracker(self.db, self.feed)

        return True

    async def _init_adapters(self):
        """Initialize market adapters."""
        # Crypto adapter
        self.adapters["crypto"] = CryptoAdapter(
            api_key=self.settings.binance_api_key,
            secret=self.settings.binance_secret,
            testnet=self.settings.binance_testnet,
            dry_run=self.settings.dry_run
        )
        connected = await self.adapters["crypto"].connect()
        log_module_status("Crypto", "OK" if connected else "WARN", "Binance" if connected else "Simulation mode")

        # Forex adapter
        self.adapters["forex"] = ForexAdapter(
            account_id=self.settings.oanda_account_id,
            api_key=self.settings.oanda_api_key,
            practice=self.settings.oanda_practice,
            dry_run=self.settings.dry_run
        )
        connected = await self.adapters["forex"].connect()
        log_module_status("Forex", "OK" if connected else "WARN", "OANDA" if connected else "Limited mode")

        # Stocks adapter
        self.adapters["stock"] = StocksAdapter(
            api_key=self.settings.alpaca_api_key,
            secret_key=self.settings.alpaca_secret_key,
            paper=self.settings.alpaca_paper,
            dry_run=self.settings.dry_run
        )
        connected = await self.adapters["stock"].connect()
        log_module_status("Stocks", "OK" if connected else "WARN", "Alpaca" if connected else "Limited mode")

    async def run(self):
        """Run the trading system."""
        self._running = True

        # Start modules
        for module_name in self.modules:
            if module_name not in self.AVAILABLE_MODULES:
                logger.warning(f"Unknown module: {module_name}")
                continue

            try:
                task = await self._start_module(module_name)
                if task:
                    self._tasks.append(task)
                    log_module_status(module_name.upper(), "OK", "Started")
            except Exception as e:
                log_module_status(module_name.upper(), "ERROR", str(e))

        logger.info(f"\nSystem running with {len(self._tasks)} active tasks")
        logger.info("Press Ctrl+C to stop\n")

        # Wait for all tasks
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _start_module(self, name: str) -> Optional[asyncio.Task]:
        """Start a specific module."""
        config = self._get_module_config()

        if name == "signals":
            return asyncio.create_task(self._run_signals(config))
        elif name == "dca":
            return asyncio.create_task(self._run_dca(config))
        elif name == "grid":
            return asyncio.create_task(self._run_grid(config))
        elif name == "scalper":
            return asyncio.create_task(self._run_scalper(config))
        elif name == "dashboard":
            return asyncio.create_task(self._run_dashboard(config))

        return None

    def _get_module_config(self) -> dict:
        """Get config dict from settings."""
        return {
            "dry_run": self.settings.dry_run,
            # Signals
            "signal_types": self.settings.signal_types,
            "telegram_bot_token": self.settings.telegram_bot_token,
            "telegram_chat_id": self.settings.telegram_chat_id,
            "discord_webhook_url": self.settings.discord_webhook_url,
            # DCA
            "dca_enabled_assets": self.settings.dca_enabled_assets,
            "dca_amount_usd": self.settings.dca_amount_usd,
            "dca_frequency": self.settings.dca_frequency,
            "dca_day_of_week": self.settings.dca_day_of_week,
            "dca_hour": self.settings.dca_hour,
            # Grid
            "grid_enabled_assets": self.settings.grid_enabled_assets,
            "grid_total_investment": self.settings.grid_total_investment,
            "grid_levels": self.settings.grid_levels,
            "grid_range_low": self.settings.grid_range_low,
            "grid_range_high": self.settings.grid_range_high,
            # Scalper
            "scalper_enabled_assets": self.settings.scalper_enabled_assets,
            "scalper_strategies": self.settings.scalper_strategies,
            "scalper_max_position_usd": self.settings.scalper_max_position_usd,
            "scalper_profit_target_pct": self.settings.scalper_profit_target_pct,
            "scalper_stop_loss_pct": self.settings.scalper_stop_loss_pct,
            "scalper_max_daily_trades": self.settings.scalper_max_daily_trades,
            "scalper_max_daily_loss_usd": self.settings.scalper_max_daily_loss_usd,
            # Dashboard
            "dashboard_port": self.settings.dashboard_port,
            "dashboard_host": self.settings.dashboard_host,
        }

    async def _run_signals(self, config: dict):
        """Run the signals module."""
        from modules.signals.detector import SignalDetector
        from modules.signals.telegram import TelegramNotifier
        from modules.signals.webhook import WebhookNotifier

        detector = SignalDetector(
            feed=self.feed,
            db=self.db,
            enabled_signals=config["signal_types"]
        )

        telegram = TelegramNotifier(
            bot_token=config["telegram_bot_token"],
            chat_id=config["telegram_chat_id"]
        )

        webhook = WebhookNotifier(
            url=config["discord_webhook_url"]
        )

        # Get crypto symbols
        symbols = [s for s in ASSETS.keys() if "/" in s]
        logger.info(f"Signals monitoring: {', '.join(symbols[:5])}...")

        while self._running:
            try:
                signals = await detector.analyze_all(symbols)

                for sig in signals:
                    # Send notifications
                    if telegram.enabled:
                        await telegram.send_signal(sig)
                        await self.db.mark_signal_sent(sig.db_id, "telegram")

                    if webhook.enabled:
                        await webhook.send(sig)
                        await self.db.mark_signal_sent(sig.db_id, "discord")

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.error(f"Signals error: {e}")
                await asyncio.sleep(10)

        await telegram.close()
        await webhook.close()

    async def _run_dca(self, config: dict):
        """Run the DCA module."""
        from modules.dca.bot import DCABot

        bot = DCABot(
            adapters=self.adapters,
            feed=self.feed,
            portfolio=self.portfolio,
            db=self.db,
            config=config
        )

        await bot.run()

    async def _run_grid(self, config: dict):
        """Run the Grid module."""
        from modules.grid.bot import GridBot

        bot = GridBot(
            adapters=self.adapters,
            feed=self.feed,
            portfolio=self.portfolio,
            db=self.db,
            config=config
        )

        await bot.run()

    async def _run_scalper(self, config: dict):
        """Run the Scalper module."""
        from modules.scalper.executor import ScalperExecutor

        executor = ScalperExecutor(
            adapters=self.adapters,
            feed=self.feed,
            portfolio=self.portfolio,
            db=self.db,
            config=config
        )

        await executor.run()

    async def _run_dashboard(self, config: dict):
        """Run the Dashboard module."""
        import uvicorn
        from modules.dashboard.api import create_app

        app = create_app(
            feed=self.feed,
            portfolio=self.portfolio,
            db=self.db,
            module_status={"active_modules": self.modules}
        )

        server_config = uvicorn.Config(
            app,
            host=config["dashboard_host"],
            port=config["dashboard_port"],
            log_level="warning"
        )

        server = uvicorn.Server(server_config)
        logger.info(f"Dashboard available at http://{config['dashboard_host']}:{config['dashboard_port']}")

        await server.serve()

    async def stop(self):
        """Stop the trading system."""
        logger.info("\nShutting down...")
        self._running = False

        # Cancel tasks
        for task in self._tasks:
            task.cancel()

        # Wait for cancellation
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Disconnect adapters
        for name, adapter in self.adapters.items():
            await adapter.disconnect()

        # Disconnect database
        if self.db:
            await self.db.disconnect()

        # Take final snapshot
        if self.portfolio:
            await self.portfolio.take_snapshot()

        logger.info("System stopped. Goodbye!")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Izalith Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run.py                         # Run with .env settings
    python run.py --modules signals       # Run only signals module
    python run.py --modules signals,dashboard  # Run signals + dashboard
    python run.py --list-modules          # Show available modules
        """
    )

    parser.add_argument(
        "--modules",
        type=str,
        help="Comma-separated list of modules to run"
    )

    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="List available modules and exit"
    )

    return parser.parse_args()


async def main():
    """Main entry point."""
    args = parse_args()

    if args.list_modules:
        print("\nAvailable modules:")
        print("  signals   - Monitor markets and send alerts (Telegram, Discord)")
        print("  dca       - Dollar Cost Averaging automatic purchases")
        print("  grid      - Grid trading for range-bound markets")
        print("  scalper   - Active trading with multiple strategies")
        print("  dashboard - Web dashboard for monitoring")
        print("\nExample: python run.py --modules signals,dashboard")
        return

    # Parse modules
    modules = None
    if args.modules:
        modules = [m.strip() for m in args.modules.split(",")]

    # Create and run system
    system = IzalithSystem(modules)

    # Handle signals
    loop = asyncio.get_event_loop()

    def handle_shutdown():
        asyncio.create_task(system.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        if await system.initialize():
            await system.run()
    except KeyboardInterrupt:
        await system.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await system.stop()


if __name__ == "__main__":
    asyncio.run(main())
