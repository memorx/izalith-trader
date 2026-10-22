#!/usr/bin/env python3
"""
Izalith Trading System - Main Entry Point

Usage:
    python run.py                               # Run modules from .env
    python run.py --modules signals             # Run only signals
    python run.py --modules allocator,dashboard # Lab strategies live (paper) + dashboard
    python run.py --list-modules                # List available modules
"""
import argparse
import asyncio
import signal
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
warnings.filterwarnings("ignore", module="yfinance")   # avisos internos de yfinance con pandas 3

from config.settings import get_settings
from config.assets import get_asset
from core.utils.logger import setup_logging, get_logger, log_startup_banner, log_module_status
from core.utils.database import get_database
from core.adapters.crypto import CryptoAdapter
from core.adapters.forex import ForexAdapter
from core.adapters.stocks import StocksAdapter
from core.data.feed import UnifiedFeed
from core.execution import reconcile_pending
from core.portfolio.tracker import PortfolioTracker

logger = get_logger("main")

SNAPSHOT_SECONDS = 300


class IzalithSystem:
    """Main trading system orchestrator."""

    AVAILABLE_MODULES = ["signals", "dca", "grid", "scalper", "allocator", "collectors", "dashboard"]

    def __init__(self, modules: Optional[List[str]] = None):
        self.settings = get_settings()
        self.modules = modules or self.settings.active_modules
        self.adapters: Dict = {}
        self.feed: Optional[UnifiedFeed] = None
        self.portfolio: Optional[PortfolioTracker] = None
        self.db = None
        self.instances: Dict[str, object] = {}     # módulo -> objeto (para el dashboard)
        self.unrecorded_orders: List[Dict] = []
        self.module_errors: Dict[str, str] = {}
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._stopped = False

    async def initialize(self) -> bool:
        setup_logging(level=self.settings.log_level, log_dir=self.settings.logs_dir, log_to_file=True)
        log_startup_banner()
        mode = "PAPEL (DRY_RUN)" if self.settings.dry_run else "DINERO REAL"
        logger.info(f"Modo: {mode} · módulos: {', '.join(self.modules)} · zona: {self.settings.trading_timezone}")

        try:
            self.db = await get_database(self.settings.data_dir / "izalith.db", self.settings.trading_timezone)
            log_module_status("Database", "OK", str(self.settings.data_dir / "izalith.db"))
        except Exception as e:
            log_module_status("Database", "ERROR", str(e))
            return False

        await self._init_adapters()
        self.feed = UnifiedFeed(self.adapters)
        self.portfolio = PortfolioTracker(self.db, self.feed, initial_cash=self.settings.paper_cash_usd)
        await self.portfolio.load()

        def market_of(symbol: str) -> str:
            try:
                return get_asset(symbol).market.value
            except ValueError:
                a = self.feed._get_adapter_for_symbol(symbol)
                return a.market if a else "crypto"
        self.unrecorded_orders = await reconcile_pending(self.db, self.adapters, market_of)
        return True

    async def _init_adapters(self):
        s = self.settings
        # Con dinero real nunca se permiten precios inventados
        allow_sim = s.allow_simulation and s.dry_run
        common = dict(dry_run=s.dry_run, allow_simulation=allow_sim)
        self.adapters["crypto"] = CryptoAdapter(api_key=s.binance_api_key, secret=s.binance_secret,
                                                testnet=s.binance_testnet, exchange_id=s.crypto_exchange, **common)
        self.adapters["forex"] = ForexAdapter(account_id=s.oanda_account_id, api_key=s.oanda_api_key,
                                              practice=s.oanda_practice, **common)
        self.adapters["stock"] = StocksAdapter(api_key=s.alpaca_api_key, secret_key=s.alpaca_secret_key,
                                               paper=s.alpaca_paper, **common)
        names = {"crypto": s.crypto_exchange.capitalize(), "forex": "OANDA",
                 "stock": "Alpaca" if s.alpaca_api_key else "Yahoo (sólo papel)"}
        for market, adapter in self.adapters.items():
            adapter._simulated_balance = {"USD": s.paper_cash_usd, "USDT": s.paper_cash_usd}
            await adapter.connect()
            state = {"real": "OK", "simulado": "WARN", "sin conexión": "WARN"}[adapter.source]
            detail = names[market] if adapter.source == "real" else f"{adapter.source}: {adapter.last_error or ''}"
            log_module_status(market.capitalize(), state, detail[:80])

    def _module_config(self) -> dict:
        s = self.settings
        return {
            "dry_run": s.dry_run,
            "trading_timezone": s.trading_timezone,
            "signal_types": s.signal_types,
            "telegram_bot_token": s.telegram_bot_token,
            "telegram_chat_id": s.telegram_chat_id,
            "discord_webhook_url": s.discord_webhook_url,
            "dca_enabled_assets": s.dca_enabled_assets,
            "dca_amount_usd": s.dca_amount_usd,
            "dca_frequency": s.dca_frequency,
            "dca_day_of_week": s.dca_day_of_week,
            "dca_hour": s.dca_hour,
            "grid_enabled_assets": s.grid_enabled_assets,
            "grid_total_investment": s.grid_total_investment,
            "grid_levels": s.grid_levels,
            "grid_range_low": s.grid_range_low,
            "grid_range_high": s.grid_range_high,
            "grid_fee_pct": s.grid_fee_pct,
            "scalper_enabled_assets": s.scalper_enabled_assets,
            "scalper_strategies": s.scalper_strategies,
            "scalper_max_position_usd": s.scalper_max_position_usd,
            "scalper_profit_target_pct": s.scalper_profit_target_pct,
            "scalper_stop_loss_pct": s.scalper_stop_loss_pct,
            "scalper_max_daily_trades": s.scalper_max_daily_trades,
            "scalper_max_daily_loss_usd": s.scalper_max_daily_loss_usd,
            "allocator_strategies": s.allocator_strategies,
            "allocator_capital_usd": s.allocator_capital_usd,
            "allocator_check_minutes": s.allocator_check_minutes,
            "collectors_interval_minutes": s.collectors_interval_minutes,
            "dashboard_port": s.dashboard_port,
            "dashboard_host": s.dashboard_host,
            "dashboard_token": s.dashboard_token,
        }

    def _watch(self, name: str, task: asyncio.Task) -> None:
        """Si un módulo muere, que se sepa (antes se perdía en silencio)."""
        def done(t: asyncio.Task):
            if t.cancelled():
                return
            exc = t.exception()
            if exc:
                logger.error(f"El módulo {name} se detuvo por un error: {exc!r}")
                self.module_errors[name] = repr(exc)
            elif self._running:
                self.module_errors[name] = "terminó sin error pero dejó de correr"
        task.add_done_callback(done)

    async def run(self):
        self._running = True
        config = self._module_config()
        # Los recolectores van antes que el dashboard para que éste los encuentre
        order = sorted(self.modules, key=lambda m: (m == "dashboard", m))
        for name in order:
            if name not in self.AVAILABLE_MODULES:
                logger.warning(f"Unknown module: {name}")
                continue
            try:
                coro = await self._start_module(name, config)
                task = asyncio.create_task(coro, name=name)
                self._watch(name, task)
                self._tasks.append(task)
                log_module_status(name.upper(), "OK", "Started")
            except Exception as e:
                log_module_status(name.upper(), "ERROR", str(e))

        snap = asyncio.create_task(self._snapshots(), name="snapshots")
        self._tasks.append(snap)
        logger.info(f"System running with {len(self._tasks)} tasks. Press Ctrl+C to stop")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _snapshots(self):
        while self._running:
            try:
                await self.portfolio.take_snapshot()
            except Exception as e:
                logger.warning(f"Snapshot falló: {e}")
            await asyncio.sleep(SNAPSHOT_SECONDS)

    async def _start_module(self, name: str, config: dict):
        if name == "signals":
            return self._run_signals(config)
        if name == "dca":
            from modules.dca.bot import DCABot
            bot = DCABot(self.adapters, self.feed, self.portfolio, self.db, config)
        elif name == "grid":
            from modules.grid.bot import GridBot
            bot = GridBot(self.adapters, self.feed, self.portfolio, self.db, config)
        elif name == "scalper":
            from modules.scalper.executor import ScalperExecutor
            bot = ScalperExecutor(self.adapters, self.feed, self.portfolio, self.db, config)
        elif name == "allocator":
            from modules.allocator.bot import AllocatorBot
            bot = AllocatorBot(self.adapters, self.feed, self.portfolio, self.db, config)
        elif name == "collectors":
            from modules.collectors.bot import CollectorsBot
            bot = CollectorsBot(self.db, config)
        elif name == "dashboard":
            return self._run_dashboard(config)
        else:
            raise ValueError(name)
        self.instances[name] = bot
        return bot.run()

    async def _run_signals(self, config: dict):
        from modules.signals.detector import SignalDetector
        from modules.signals.telegram import TelegramNotifier
        from modules.signals.webhook import WebhookNotifier
        from config.assets import ASSETS

        detector = SignalDetector(feed=self.feed, db=self.db, enabled_signals=config["signal_types"])
        self.instances["signals"] = detector
        telegram = TelegramNotifier(bot_token=config["telegram_bot_token"], chat_id=config["telegram_chat_id"])
        webhook = WebhookNotifier(url=config["discord_webhook_url"])
        # Las estables (USDC/USDT) sólo hacen ruido en las señales
        symbols = [s for s, a in ASSETS.items() if "/" in s and "USDC" not in s]
        logger.info(f"Signals monitoring: {', '.join(symbols)}")
        try:
            while self._running:
                try:
                    for sig in await detector.analyze_all(symbols):
                        if sig.simulated:
                            continue   # datos inventados: se guardan, pero no se le mandan a nadie
                        if telegram.enabled and await telegram.send_signal(sig):
                            await self.db.mark_signal_sent(sig.db_id, "telegram")
                        if webhook.enabled and await webhook.send(sig):
                            await self.db.mark_signal_sent(sig.db_id, "discord")
                    await asyncio.sleep(30)
                except Exception as e:
                    logger.error(f"Signals error: {e}")
                    await asyncio.sleep(10)
        finally:
            await telegram.close()
            await webhook.close()

    async def _run_dashboard(self, config: dict):
        import uvicorn
        from modules.dashboard.api import create_app
        app = create_app(system=self, token=config["dashboard_token"])
        server = uvicorn.Server(uvicorn.Config(app, host=config["dashboard_host"], port=config["dashboard_port"],
                                               log_level="warning"))
        logger.info(f"Dashboard: http://{config['dashboard_host']}:{config['dashboard_port']}")
        try:
            await server.serve()
        except SystemExit:
            # uvicorn sale con SystemExit si el puerto está ocupado: el panel falla, el trading sigue
            self.module_errors["dashboard"] = f"No pudo abrir el puerto {config['dashboard_port']} (¿ocupado?). Cambia DASHBOARD_PORT."
            logger.error(self.module_errors["dashboard"])

    async def stop(self):
        if self._stopped:
            return
        self._stopped = True
        logger.info("Shutting down...")
        self._running = False
        for name, inst in self.instances.items():
            stop = getattr(inst, "stop", None)
            if stop:
                try:
                    await stop()
                except Exception:
                    pass
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        # Orden: foto final (con precios reales) → adaptadores → base de datos
        if self.portfolio and self.db and self.db.is_connected:
            try:
                await self.portfolio.take_snapshot()
            except Exception as e:
                logger.warning(f"Snapshot final falló: {e}")
        for adapter in self.adapters.values():
            try:
                await adapter.disconnect()
            except Exception:
                pass
        if self.db:
            await self.db.disconnect()
        logger.info("System stopped. Goodbye!")


def parse_args():
    parser = argparse.ArgumentParser(description="Izalith Trading System")
    parser.add_argument("--modules", type=str, help="Comma-separated list of modules to run")
    parser.add_argument("--list-modules", action="store_true", help="List available modules and exit")
    return parser.parse_args()


async def main():
    args = parse_args()
    if args.list_modules:
        print("\nAvailable modules:")
        print("  signals    - Alertas a Telegram/Discord (no opera)")
        print("  dca        - Compras periódicas")
        print("  grid       - Rejilla de compras y ventas en un rango")
        print("  scalper    - Intradía (en rojo en el laboratorio: sólo papel)")
        print("  allocator  - Estrategias del laboratorio en vivo, cada una con su capital")
        print("  collectors - Fotos de funding entre exchanges, prima de Bitso y Polymarket")
        print("  dashboard  - Panel web")
        print("\nExample: python run.py --modules allocator,collectors,dashboard")
        return

    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None
    system = IzalithSystem(modules)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(system.stop()))
        except (NotImplementedError, RuntimeError):
            pass  # Windows: Ctrl+C llega como KeyboardInterrupt / CancelledError

    try:
        if await system.initialize():
            await system.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await system.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
