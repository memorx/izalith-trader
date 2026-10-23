"""
Pruebas del núcleo en vivo: lo que toca dinero (posiciones, P&L, efectivo, órdenes, límites).

Cada prueba usa una base SQLite temporal y adaptadores en simulación.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from core.adapters.crypto import CryptoAdapter
from core.data.feed import UnifiedFeed
from core.execution import execute_order, reconcile_pending
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database
from modules.dca.bot import next_run
from modules.grid.bot import GridBot


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test.db", tz="America/Mexico_City")
    await d.connect()
    yield d
    await d.disconnect()


@pytest_asyncio.fixture
async def stack(db):
    adapter = CryptoAdapter(dry_run=True, simulate=True)
    await adapter.connect()
    feed = UnifiedFeed({"crypto": adapter})
    tracker = PortfolioTracker(db, feed, initial_cash=10_000)
    await tracker.load()
    return db, adapter, feed, tracker


# ── P&L y posiciones ──

@pytest.mark.asyncio
async def test_long_pnl_sign_is_positive_when_price_rises(stack):
    """Antes 'buy' se trataba como corto y una ganancia salía como pérdida."""
    db, _, _, tracker = stack
    pos = await tracker.add_position("BTC/USDT", "crypto", "buy", 1.0, 100.0, module="dca")
    assert pos.side == "long"
    pos.current_price = 110.0
    assert pos.unrealized_pnl == pytest.approx(10.0)
    pnl = await tracker.close_position("BTC/USDT", 110.0, module="dca")
    assert pnl == pytest.approx(10.0)
    assert tracker.cash_usd == pytest.approx(10_010.0)


@pytest.mark.asyncio
async def test_partial_close_realizes_pnl_and_keeps_rest(stack):
    db, _, _, tracker = stack
    await tracker.add_position("ETH/USDT", "crypto", "buy", 2.0, 100.0, module="scalper", fee=0.2)
    pnl = await tracker.reduce_position("ETH/USDT", 1.0, 120.0, module="scalper", fee=0.12)
    # (120-100)*1 - mitad de la comisión de entrada (0.1) - comisión de salida (0.12)
    assert pnl == pytest.approx(19.78)
    pos = tracker.get_position("ETH/USDT", module="scalper")
    assert pos.quantity == pytest.approx(1.0)
    assert pos.fees == pytest.approx(0.1)
    assert await db.get_realized_pnl_total() == pytest.approx(19.78)


@pytest.mark.asyncio
async def test_modules_do_not_mix_positions(stack):
    """DCA y scalper con el mismo símbolo: el cierre de uno no toca al otro."""
    _, _, _, tracker = stack
    await tracker.add_position("BTC/USDT", "crypto", "buy", 1.0, 100.0, module="dca")
    await tracker.add_position("BTC/USDT", "crypto", "buy", 0.5, 100.0, module="scalper")
    await tracker.close_position("BTC/USDT", 105.0, module="scalper")
    assert tracker.get_position("BTC/USDT", module="dca").quantity == pytest.approx(1.0)
    assert tracker.get_position("BTC/USDT", module="scalper") is None


@pytest.mark.asyncio
async def test_short_pnl(stack):
    _, _, _, tracker = stack
    await tracker.add_position("EUR_USD", "forex", "sell", 1000, 1.10, module="scalper")
    pnl = await tracker.close_position("EUR_USD", 1.08, module="scalper")
    assert pnl == pytest.approx(20.0)
    assert tracker.cash_usd == pytest.approx(10_020.0)


@pytest.mark.asyncio
async def test_reload_from_db_rebuilds_positions_and_cash(stack):
    """Un reinicio no deja posiciones huérfanas ni cambia el efectivo."""
    db, _, feed, tracker = stack
    await tracker.add_position("BTC/USDT", "crypto", "buy", 0.5, 100.0, module="scalper", fee=0.05,
                               stop_loss=97.0, take_profit=105.0, strategy="momentum")
    await tracker.add_position("ETH/USDT", "crypto", "buy", 1.0, 50.0, module="dca", fee=0.05)
    await tracker.close_position("ETH/USDT", 60.0, module="dca", fee=0.06)
    cash_before = tracker.cash_usd

    fresh = PortfolioTracker(db, feed, initial_cash=10_000)
    await fresh.load()
    pos = fresh.get_position("BTC/USDT", module="scalper")
    assert pos is not None and pos.stop_loss == 97.0 and pos.strategy == "momentum"
    assert fresh.cash_usd == pytest.approx(cash_before)


# ── Día local y límites diarios ──

@pytest.mark.asyncio
async def test_daily_pnl_counts_trades_closed_today_even_if_opened_yesterday(stack):
    db, _, _, tracker = stack
    await tracker.add_position("BTC/USDT", "crypto", "buy", 1.0, 100.0, module="scalper")
    # Se finge que la entrada fue hace dos días
    old = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None).isoformat()
    await db._conn.execute("UPDATE trades SET timestamp = ?", (old,))
    await db._conn.commit()
    await tracker.close_position("BTC/USDT", 80.0, module="scalper")
    assert await db.get_daily_pnl("scalper") == pytest.approx(-20.0)


@pytest.mark.asyncio
async def test_day_starts_at_local_midnight(db):
    start = datetime.fromisoformat(db.day_start_utc()).replace(tzinfo=timezone.utc)
    local = start.astimezone(ZoneInfo("America/Mexico_City"))
    assert (local.hour, local.minute) == (0, 0)


# ── Órdenes ──

@pytest.mark.asyncio
async def test_same_purpose_is_not_sent_twice(stack):
    db, adapter, _, _ = stack
    first = await execute_order(db, adapter, "dca", "BTC/USDT", "buy", amount=50, purpose="dca:BTC/USDT:slot1")
    second = await execute_order(db, adapter, "dca", "BTC/USDT", "buy", amount=50, purpose="dca:BTC/USDT:slot1")
    assert first.success and first.filled_quantity > 0
    assert second.success is False and second.status == "duplicate"
    orders = await db.get_recent_orders()
    assert len(orders) == 1 and orders[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_failed_order_does_not_block_retry(stack):
    db, adapter, _, _ = stack
    bad = await execute_order(db, adapter, "grid", "BTC/USDT", "sell", quantity=5, purpose="grid:x")
    assert bad.success is False
    again = await execute_order(db, adapter, "grid", "BTC/USDT", "sell", quantity=5, purpose="grid:x")
    assert again.status != "duplicate"


@pytest.mark.asyncio
async def test_pending_orders_are_resolved_on_restart(stack):
    db, adapter, _, _ = stack
    await db.insert_order_intent("dca", "BTC/USDT", "buy", "iz-test-1", amount=50, purpose="p1")
    unresolved = await reconcile_pending(db, {"crypto": adapter}, lambda s: "crypto")
    assert unresolved == []
    assert (await db.get_recent_orders())[0]["status"] == "failed"


# ── DCA ──

def test_dca_weekly_next_run_in_local_time():
    tz = ZoneInfo("America/Mexico_City")
    after = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)  # miércoles
    nxt = next_run("weekly", 9, 0, tz, after).astimezone(tz)
    assert nxt.weekday() == 0 and nxt.hour == 9 and nxt > after


def test_dca_unknown_frequency_fails_loudly():
    """Antes 'Weekly' o un typo caían a compra diaria sin aviso."""
    with pytest.raises(ValueError):
        next_run("semanal", 9, 0, ZoneInfo("America/Mexico_City"))


# ── Grid ──

def test_grid_refuses_steps_smaller_than_fees():
    bot = GridBot({}, None, None, None, {})
    with pytest.raises(ValueError):
        bot._validate(0.995, 1.005, 10, fee=0.001, spread_pct=0.0001)
    assert bot._validate(0.98, 1.02, 5, fee=0.001, spread_pct=0.0001) > 0


# ── Comisiones reales en papel y órdenes pasivas ──

@pytest.mark.asyncio
async def test_kraken_paper_charges_real_fees():
    adapter = CryptoAdapter(dry_run=True, simulate=True, exchange_id="kraken")
    await adapter.connect()
    market = await adapter.place_market_order("BTC/USDT", "buy", 0.01)
    passive = await adapter.place_passive_order("BTC/USDT", "buy", 0.01)
    assert market.fee == pytest.approx(market.price * 0.01 * 0.008, rel=1e-6)
    assert passive.fee == pytest.approx(passive.price * 0.01 * 0.004, rel=1e-6)


@pytest.mark.asyncio
async def test_passive_buy_by_amount_through_execution(stack):
    db, _, _, _ = stack
    adapter = CryptoAdapter(dry_run=True, simulate=True, exchange_id="kraken")
    await adapter.connect()
    res = await execute_order(db, adapter, "alloc:x", "ETH/USDT", "buy", amount=100, purpose="p-passive", passive=True)
    assert res.success and res.filled_quantity > 0
    assert res.filled_quantity * res.price + res.fee <= 100 + 1e-6


# ── Freno de emergencia ──

@pytest.mark.asyncio
async def test_brake_activates_and_blocks_only_new_exposure(stack):
    from core.risk_guard import RiskGuard, set_guard
    db, adapter, _, tracker = stack
    guard = RiskGuard(db, max_drawdown_pct=25)
    await guard.load()
    await guard.update(200.0)
    assert not guard.active
    assert await guard.update(140.0) is True          # -30% desde el máximo
    set_guard(guard)
    try:
        buy = await execute_order(db, adapter, "alloc:x", "BTC/USDT", "buy", amount=20, purpose="b1")
        assert buy.status == "freno" and not buy.success
        await tracker.add_position("ETH/USDT", "crypto", "buy", 0.01, 100.0, module="m")
        adapter._simulated_positions.clear()
        await adapter.place_market_order("ETH/USDT", "buy", 0.01)
        sell = await execute_order(db, adapter, "m", "ETH/USDT", "sell", quantity=0.01, close_side="long", purpose="s1")
        assert sell.status != "freno"                  # salir siempre se puede
    finally:
        set_guard(None)


@pytest.mark.asyncio
async def test_brake_survives_restart_and_release(stack):
    from core.risk_guard import RiskGuard
    db, *_ = stack
    g = RiskGuard(db, 25)
    await g.update(100.0)
    await g.update(70.0)
    again = RiskGuard(db, 25)
    await again.load()
    assert again.active
    await again.release()
    third = RiskGuard(db, 25)
    await third.load()
    assert not third.active and third.peak == pytest.approx(70.0)


def test_single_instance_lock():
    import run
    first = run.acquire_single_instance_lock(47999)
    assert first is not None
    try:
        assert run.acquire_single_instance_lock(47999) is None
    finally:
        first.close()
