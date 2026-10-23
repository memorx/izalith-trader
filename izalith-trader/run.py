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
import logging
import os
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
from core.adapters.base import BaseAdapter
from core.risk_guard import RiskGuard, set_guard
from core.portfolio.tracker import PortfolioTracker

logger = get_logger("main")

SNAPSHOT_SECONDS = 300
RECONCILE_MIN_AGE_S = 1800   # órdenes a medias sin moverse en 30 min: ya nadie las está esperando
LOCK_PORT = 47651   # candado: sólo una copia del bot puede tener este puerto
STOP_FILE = Path(__file__).parent / "data" / "STOP"   # tools/detener.bat lo crea para un apagado limpio


def acquire_single_instance_lock(port: int = LOCK_PORT):
    """
    Dos copias del bot sobre la misma base mandarían órdenes dobles. El sistema operativo libera
    el puerto solo si el proceso muere, así que el candado nunca se queda pegado.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        return None
    return sock


class IzalithSystem:
    """Main trading system orchestrator."""

    AVAILABLE_MODULES = ["signals", "dca", "grid", "scalper", "allocator", "collectors", "reports", "dashboard"]

    def __init__(self, modules: Optional[List[str]] = None):
        self.settings = get_settings()
        self.modules = modules or self.settings.active_modules
        self.adapters: Dict = {}
        self.feed: Optional[UnifiedFeed] = None
        self.portfolio: Optional[PortfolioTracker] = None
        self.db = None
        self.instances: Dict[str, object] = {}     # módulo -> objeto (para el dashboard)
        self.unrecorded_orders: List[Dict] = []     # órdenes que necesitan atención (ver core/execution.py)
        self.capital_warnings: List[str] = []
        self.module_errors: Dict[str, str] = {}
        self.guard: Optional[RiskGuard] = None
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._stopped = False
        self._stop_task: Optional[asyncio.Task] = None

    @property
    def mode_label(self) -> str:
        live = self.settings.live_markets
        names = {"crypto": "cripto", "stock": "acciones", "forex": "forex"}
        return "PAPEL" if not live else "DINERO REAL en " + ", ".join(names[m] for m in live)

    def _live_gate(self) -> Optional[str]:
        """Las dos llaves para operar en real. Regresa el motivo si falta alguna."""
        s = self.settings
        if s.all_paper:
            return None
        if not s.live_confirmed:
            return (f"El .env pide operar en real ({', '.join(s.live_markets)}) pero falta LIVE_CONFIRMED=true. "
                    f"Es la segunda llave: así un arranque automático nunca pone dinero real sin que lo hayas decidido.")
        if s.real_capital_usd <= 0:
            return ("Falta REAL_CAPITAL_USD: el dinero real (en USD) que va a manejar el bot. "
                    "El freno de emergencia y el efectivo se miden contra ese número.")
        return None

    def _market_of(self, symbol: str) -> str:
        try:
            return get_asset(symbol).market.value
        except ValueError:
            a = self.feed._get_adapter_for_symbol(symbol) if self.feed else None
            return a.market if a else "crypto"

    async def initialize(self) -> bool:
        setup_logging(level=self.settings.log_level, log_dir=self.settings.logs_dir, log_to_file=True)
        log_startup_banner()
        s = self.settings
        logger.info(f"Modo: {self.mode_label} · módulos: {', '.join(self.modules)} · zona: {s.trading_timezone}")
        blocked = self._live_gate()
        if blocked:
            logger.error(blocked)
            return False

        db_path = s.db_path   # izalith.db en papel, izalith_real.db si algún mercado opera en real
        try:
            self.db = await get_database(db_path, s.trading_timezone)
            log_module_status("Database", "OK", str(db_path))
        except Exception as e:
            log_module_status("Database", "ERROR", str(e))
            return False

        await self._init_adapters()
        self.feed = UnifiedFeed(self.adapters)
        initial = s.paper_cash_usd if s.all_paper else s.real_capital_usd
        self.portfolio = PortfolioTracker(self.db, self.feed, initial_cash=initial)
        await self.portfolio.load()
        for adapter in self.adapters.values():
            # En papel, el saldo y las tenencias salen del portafolio: sobreviven a un reinicio
            adapter.paper_cash_provider = self.portfolio.get_cash_usd
            adapter.paper_holdings_provider = self.portfolio.held_quantity
        if not s.all_paper:
            await self._check_live_capital()
            if any(a.is_paper for a in self.adapters.values() if a.connected or a.is_simulating):
                logger.warning("Hay mercados en papel junto con mercados en real: comparten la cuenta de efectivo "
                               "del bot. Para medir bien, opera todo en real o todo en papel.")

        self.unrecorded_orders = await reconcile_pending(self.db, self.adapters, self._market_of)

        self.guard = RiskGuard(self.db, self.settings.max_drawdown_pct)
        await self.guard.load()
        await self.guard.update(self.portfolio.get_total_value_usd())
        set_guard(self.guard)
        return True

    async def _check_live_capital(self) -> None:
        """¿Hay en las cuentas reales al menos lo que dice REAL_CAPITAL_USD? Si no, el freno mide mal."""
        s = self.settings
        cash, failed = 0.0, []
        for market in s.live_markets:
            adapter = self.adapters.get(market)
            try:
                bal = await adapter.get_balance()
                cash += sum(v for k, v in bal.items() if k in ("USD", "USDT", "USDC"))
            except Exception as e:
                failed.append(f"{market} ({e})")
        held = sum(p.value for p in self.portfolio.positions.values() if p.market in s.live_markets)
        if failed:
            self.capital_warnings.append("No se pudo leer el saldo real de " + ", ".join(failed))
        elif cash + held < s.real_capital_usd * 0.98:
            self.capital_warnings.append(
                f"En las cuentas hay {cash + held:,.2f} USD (efectivo + posiciones del bot) y REAL_CAPITAL_USD dice "
                f"{s.real_capital_usd:,.2f}: baja REAL_CAPITAL_USD o deposita la diferencia")
        for w in self.capital_warnings:
            logger.error(w)

    async def _init_adapters(self):
        s = self.settings
        # Con dinero real en cualquier mercado nunca se permiten precios inventados
        allow_sim = s.allow_simulation and s.all_paper

        def common(market: str) -> dict:
            return dict(dry_run=s.market_dry_run(market), allow_simulation=allow_sim)
        self.adapters["crypto"] = CryptoAdapter(api_key=s.binance_api_key, secret=s.binance_secret,
                                                testnet=s.binance_testnet, exchange_id=s.crypto_exchange,
                                                quote=s.crypto_quote, **common("crypto"))
        self.adapters["forex"] = ForexAdapter(account_id=s.oanda_account_id, api_key=s.oanda_api_key,
                                              practice=s.oanda_practice, **common("forex"))
        if s.stocks_broker == "ibkr":
            from core.adapters.ibkr import IBKRAdapter
            self.adapters["stock"] = IBKRAdapter(host=s.ibkr_host, port=s.ibkr_port, client_id=s.ibkr_client_id,
                                                 **common("stock"))
        else:
            self.adapters["stock"] = StocksAdapter(api_key=s.alpaca_api_key, secret_key=s.alpaca_secret_key,
                                                   paper=s.alpaca_paper, **common("stock"))
        names = {"crypto": s.crypto_exchange.capitalize(), "forex": "OANDA",
                 "stock": "IBKR" if s.stocks_broker == "ibkr" else "Alpaca" if s.alpaca_api_key else "Yahoo (sólo papel)"}
        for market, adapter in self.adapters.items():
            adapter._simulated_balance = {"USD": s.paper_cash_usd, "USDT": s.paper_cash_usd}
            await adapter.connect()
            state = {"real": "OK", "simulado": "WARN", "sin conexión": "WARN"}[adapter.source]
            detail = names[market] if adapter.source == "real" else f"{adapter.source}: {adapter.last_error or ''}"
            log_module_status(market.capitalize(), state, detail[:80])

    def _module_config(self) -> dict:
        s = self.settings
        return {
            "dry_run": s.all_paper,            # con cualquier mercado en real, nunca se opera sobre precios inventados
            "live_markets": s.live_markets,
            "real_capital_usd": s.real_capital_usd,
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
            "allocator_passive_orders": s.allocator_passive_orders,
            "collectors_interval_minutes": s.collectors_interval_minutes,
            "banxico_token": s.banxico_token,
            "report_hour": s.report_hour,
            "healthcheck_url": s.healthcheck_url,
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
        self._tasks.append(asyncio.create_task(self._watch_stop_file(), name="stop-file"))
        logger.info(f"System running with {len(self._tasks)} tasks. Press Ctrl+C to stop")
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _snapshots(self):
        while self._running:
            try:
                await self.portfolio.take_snapshot()
                if self.guard:
                    await self.guard.update(self.portfolio.get_total_value_usd())
            except Exception as e:
                logger.warning(f"Snapshot falló: {e}")
            try:
                # Órdenes que quedaron a medias por una excepción o una red caída, sin esperar al reinicio
                self.unrecorded_orders = await reconcile_pending(self.db, self.adapters, self._market_of,
                                                                 min_age_s=RECONCILE_MIN_AGE_S)
            except Exception as e:
                logger.warning(f"Reconciliación falló: {e}")
            await asyncio.sleep(SNAPSHOT_SECONDS)

    async def _watch_stop_file(self):
        """tools/detener.bat crea data/STOP: se apaga en orden (sin cortar una orden a medias a la fuerza)."""
        while self._running:
            if STOP_FILE.exists():
                try:
                    STOP_FILE.unlink()
                except OSError:
                    pass
                logger.info("Se pidió detener el bot (data/STOP)")
                asyncio.create_task(self.stop())
                return
            await asyncio.sleep(2)

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
            s = self.settings
            total = s.allocator_capital_usd * len(s.allocator_strategies)
            if not s.all_paper and total > s.real_capital_usd + 1e-6:
                raise ValueError(f"El allocator pide {total:,.2f} USD ({len(s.allocator_strategies)} estrategias x "
                                 f"{s.allocator_capital_usd:,.2f}) y REAL_CAPITAL_USD es {s.real_capital_usd:,.2f}")
            bot = AllocatorBot(self.adapters, self.feed, self.portfolio, self.db, config)
        elif name == "collectors":
            from modules.collectors.bot import CollectorsBot
            bot = CollectorsBot(self.db, config)
        elif name == "reports":
            from modules.reports.bot import ReportsBot
            bot = ReportsBot(self, config)
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
        """
        Apaga en orden. Se puede llamar varias veces (el archivo STOP, Ctrl+C y el final de main):
        todas esperan al MISMO apagado. Antes la segunda llamada regresaba de inmediato, main()
        terminaba y asyncio cancelaba el apagado a la mitad.
        """
        if self._stop_task is None:
            self._stop_task = asyncio.ensure_future(self._shutdown())
        await asyncio.shield(self._stop_task)

    async def _shutdown(self):
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
        print("  collectors - Fotos de funding, dólar cripto por país, termómetros y Polymarket")
        print("  reports    - Resumen diario y alertas por Telegram, latido para healthchecks.io")
        print("  dashboard  - Panel web")
        print("\nExample: python run.py --modules allocator,collectors,dashboard")
        return

    modules = [m.strip() for m in args.modules.split(",")] if args.modules else None
    lock = acquire_single_instance_lock()
    if lock is None:
        print("Ya hay otra copia de Izalith corriendo en esta computadora. Ciérrala primero "
              "(o deténla con tools/detener.bat).")
        return
    system = IzalithSystem(modules)
    if STOP_FILE.exists():
        STOP_FILE.unlink()   # una petición de alto vieja no debe apagar el bot recién arrancado

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
        # El apagado ya cerró la base y los adaptadores. Algún hilo de red (yfinance, ccxt) puede
        # tardar minutos en soltar y asyncio.run lo esperaría con el proceso colgado: se sale ya.
        logging.shutdown()
        lock.close()
        os._exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
