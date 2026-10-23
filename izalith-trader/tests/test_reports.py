"""
Pruebas de reportes, alertas y exportación para el contador.
"""
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from core.adapters.crypto import CryptoAdapter
from core.data.feed import UnifiedFeed
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from modules.reports.bot import ReportsBot
from tools.exportar import rows_for_csv


@pytest_asyncio.fixture
async def system(tmp_path):
    db = Database(tmp_path / "r.db", tz="America/Mexico_City")
    await db.connect()
    adapter = CryptoAdapter(dry_run=True, simulate=True)
    await adapter.connect()
    feed = UnifiedFeed({"crypto": adapter})
    portfolio = PortfolioTracker(db, feed, initial_cash=200)
    await portfolio.load()
    sysobj = SimpleNamespace(db=db, feed=feed, portfolio=portfolio, instances={}, module_errors={},
                             unrecorded_orders=[], settings=SimpleNamespace(dry_run=True))
    yield sysobj
    await db.disconnect()


@pytest.mark.asyncio
async def test_summary_has_value_and_positions(system):
    await system.portfolio.add_position("BTC/USDT", "crypto", "buy", 0.001, 100.0, module="alloc:x")
    bot = ReportsBot(system, {})
    text = await bot.build_summary()
    assert "PAPEL" in text and "Valor:" in text and "BTC/USDT" in text


@pytest.mark.asyncio
async def test_alert_is_sent_once_until_resolved(system):
    bot = ReportsBot(system, {})
    system.module_errors["allocator"] = "boom"
    await bot.check_alerts()
    assert "modulo:allocator" in bot._alerted
    await bot.check_alerts()                      # no se repite
    assert len(bot._alerted) == 1
    system.module_errors.clear()
    await bot.check_alerts()                      # resuelto: se olvida
    assert not bot._alerted


@pytest.mark.asyncio
async def test_unrecorded_order_is_a_problem(system):
    system.unrecorded_orders.append({"client_order_id": "iz1", "side": "buy", "symbol": "BTC/USDT"})
    problems = ReportsBot(system, {}).current_problems()
    assert any(k.startswith("orden:iz1") for k in problems)


def test_missing_keys_is_not_an_alert():
    feed = SimpleNamespace(get_market_status=lambda: {
        "forex": {"name": "Forex", "connected": False, "simulating": False, "last_error": "sin llaves de OANDA"}})
    sysobj = SimpleNamespace(module_errors={}, unrecorded_orders=[], feed=feed, settings=SimpleNamespace(dry_run=True))
    assert ReportsBot(sysobj, {}).current_problems() == {}


@pytest.mark.asyncio
async def test_export_rows_skip_nothing_and_translate(system):
    await system.portfolio.add_position("ETH/USDT", "crypto", "buy", 1.0, 100.0, module="dca", fee=0.1)
    await system.portfolio.close_position("ETH/USDT", 110.0, module="dca", fee=0.11)
    trades = await system.db.get_closed_trades()
    rows = list(rows_for_csv(trades, ZoneInfo("America/Mexico_City")))
    assert rows[0]["lado"] == "largo"
    assert rows[0]["resultado_usd"] == pytest.approx(9.79)
    assert rows[0]["fecha_salida_local"]
