"""
Pruebas de seguridad de órdenes (revisión de Fable, 23 oct 2026).

Lo que se prueba es que una orden nunca se duplique ni se pierda: errores de red a media
orden, cancelaciones que no se confirman, órdenes que se llenan cuando el bot ya no las
esperaba y reinicios con órdenes vivas.
"""
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio

import core.adapters.crypto as crypto_mod
from core.adapters.base import OrderLookupError, OrderResult, new_client_order_id, sub_client_id
from core.adapters.crypto import CryptoAdapter
from core.adapters.ibkr import estimated_commission
from core.data.feed import UnifiedFeed
from core.execution import execute_order, reconcile_pending
from core.portfolio.tracker import PortfolioTracker
from core.utils.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test.db", tz="America/Mexico_City")
    await d.connect()
    yield d
    await d.disconnect()


# ── Ids ──

def test_client_ids_are_uuids_and_sub_ids_are_stable():
    cid = new_client_order_id()
    assert str(uuid.UUID(cid)) == cid                     # Kraken acepta UUID (o texto de 18)
    assert sub_client_id(cid, "1") == sub_client_id(cid, "1")
    assert sub_client_id(cid, "1") != sub_client_id(cid, "2")
    assert len(sub_client_id(cid, "m")) == 36


# ── Exchange falso con el comportamiento de ccxt ──

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    async def sleep(self, s):
        self.t += s


class FakeKraken:
    def __init__(self, create_error=None, keep_on_error=True, limit_fill=0.0, cancel_ok=True, lookup_ok=True):
        self.markets = {"BTC/USD": {"limits": {"amount": {"min": 0.00006}, "cost": {"min": 0.5}}}}
        self.has = {"fetchTickers": True}
        self.orders = {}
        self.create_calls = []
        self.create_error, self.keep_on_error = create_error, keep_on_error
        self.limit_fill, self.cancel_ok, self.lookup_ok = limit_fill, cancel_ok, lookup_ok

    def amount_to_precision(self, symbol, q):
        return f"{q:.8f}"

    async def fetch_ticker(self, symbol):
        return {"bid": 100.0, "ask": 100.2, "last": 100.1}

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        cid = (params or {}).get("clientOrderId")
        self.create_calls.append((type_, cid))
        amount = float(amount)
        filled = amount if type_ == "market" else min(amount, self.limit_fill)
        o = {"id": f"o{len(self.orders) + 1}", "clientOrderId": cid, "side": side, "amount": amount,
             "price": price or 100.1, "average": price or 100.1, "filled": filled,
             "status": "closed" if type_ == "market" or filled >= amount else "open", "fee": None}
        if self.create_error and type_ == "limit":
            if self.keep_on_error:
                o["status"], o["filled"] = "closed", amount     # entró y se llenó, pero la respuesta se perdió
                self.orders[o["id"]] = o
            raise Exception(self.create_error)
        self.orders[o["id"]] = o
        # Como ccxt con Kraken: AddOrder sólo contesta el txid, así que no hay status ni llenado
        return {"id": o["id"], "clientOrderId": None, "status": None, "filled": None, "amount": None,
                "price": None, "average": None, "fee": None}

    async def fetch_order(self, oid, symbol=None, params=None):
        return dict(self.orders[oid])

    async def cancel_order(self, oid, symbol=None):
        if not self.cancel_ok:
            raise Exception("cancel: timeout")
        if self.orders[oid]["status"] == "open":
            self.orders[oid]["status"] = "canceled"

    async def _list(self, open_, params):
        if not self.lookup_ok:
            raise Exception("red caída")
        cid = (params or {}).get("clientOrderId")
        return [dict(o) for o in self.orders.values()
                if (o["status"] == "open") == open_ and (cid is None or o["clientOrderId"] == cid)]

    async def fetch_open_orders(self, symbol, since=None, limit=None, params=None):
        return await self._list(True, params)

    async def fetch_closed_orders(self, symbol, since=None, limit=None, params=None):
        return await self._list(False, params)


def _live_kraken(fake, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(crypto_mod, "asyncio", SimpleNamespace(sleep=clock.sleep, get_event_loop=lambda: clock))
    a = CryptoAdapter(api_key="k", secret="s", dry_run=False, exchange_id="kraken")
    a._data = a._trade = fake
    a.connected = True
    return a


@pytest.mark.asyncio
async def test_passive_order_found_after_create_error_is_not_resent(monkeypatch):
    fake = FakeKraken(create_error="timeout")
    a = _live_kraken(fake, monkeypatch)
    cid = new_client_order_id()
    r = await a.place_passive_order("BTC/USDT", "buy", 0.5, cid)
    assert r.success and r.filled_quantity == pytest.approx(0.5)
    assert len(fake.create_calls) == 1 and fake.create_calls[0] == ("limit", sub_client_id(cid, "1"))


@pytest.mark.asyncio
async def test_passive_order_stops_when_cancel_cannot_be_confirmed(monkeypatch):
    fake = FakeKraken(limit_fill=0.2, cancel_ok=False)
    a = _live_kraken(fake, monkeypatch)
    r = await a.place_passive_order("BTC/USDT", "buy", 0.5, new_client_order_id())
    assert r.status == "en_vuelo"
    assert [c[0] for c in fake.create_calls] == ["limit"]       # ni otro intento ni el resto a mercado


@pytest.mark.asyncio
async def test_passive_order_stops_when_lookup_fails(monkeypatch):
    fake = FakeKraken(create_error="timeout", keep_on_error=False, lookup_ok=False)
    a = _live_kraken(fake, monkeypatch)
    r = await a.place_passive_order("BTC/USDT", "buy", 0.5, new_client_order_id())
    assert r.status == "en_vuelo"
    assert len(fake.create_calls) == 1


@pytest.mark.asyncio
async def test_passive_order_without_status_waits_and_cancels_before_requote(monkeypatch):
    """Fable, 23 oct: con status=None el bot no esperaba ni cancelaba y mandaba 4 órdenes por una."""
    fake = FakeKraken()
    a = _live_kraken(fake, monkeypatch)
    r = await a.place_passive_order("BTC/USDT", "buy", 0.5, new_client_order_id())
    assert [c[0] for c in fake.create_calls] == ["limit", "limit", "limit", "market"]
    limits = [o for o in fake.orders.values() if o["clientOrderId"] != fake.create_calls[-1][1]]
    assert all(o["status"] == "canceled" for o in limits)        # cada límite se canceló antes de la siguiente
    sent = sum(o["filled"] for o in fake.orders.values())
    assert sent == pytest.approx(0.5) and r.filled_quantity == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_passive_order_finishes_at_market_with_derived_id(monkeypatch):
    fake = FakeKraken()
    a = _live_kraken(fake, monkeypatch)
    cid = new_client_order_id()
    r = await a.place_passive_order("BTC/USDT", "buy", 0.5, cid)
    assert r.success and r.status == "closed" and r.filled_quantity == pytest.approx(0.5)
    assert fake.create_calls[-1] == ("market", sub_client_id(cid, "m"))
    assert all(o["status"] != "open" for o in fake.orders.values())
    found = await a.get_order_by_client_id("BTC/USDT", cid)
    assert found.filled_quantity == pytest.approx(0.5) and found.status == "closed"


@pytest.mark.asyncio
async def test_market_order_with_unknown_outcome_stays_in_flight(monkeypatch, db):
    fake = FakeKraken(lookup_ok=False)

    async def boom(*a, **k):
        raise Exception("timeout")
    fake.create_order = boom
    a = _live_kraken(fake, monkeypatch)
    r = await execute_order(db, a, "dca", "BTC/USDT", "buy", quantity=0.1, purpose="p-mkt")
    assert r.status == "en_vuelo"
    assert (await db.get_recent_orders())[0]["status"] == "open"


@pytest.mark.asyncio
async def test_kraken_trades_against_usd_and_min_uses_amount(monkeypatch):
    a = _live_kraken(FakeKraken(), monkeypatch)
    assert a.resolve_symbol("BTC/USDT") == "BTC/USD"
    await a.get_price("BTC/USDT")
    assert a.min_notional("BTC/USDT") == pytest.approx(max(0.5, 0.00006 * 100.1 * 1.01))


# ── Órdenes anteriores con el mismo propósito ──

def _stub(lookup=None, paper=False, placed=None, cancel=True):
    async def get_order_by_client_id(symbol, cid):
        if isinstance(lookup, Exception):
            raise lookup
        return lookup

    async def place(symbol, side, amount, cid):
        return placed or OrderResult(success=True, symbol=symbol, side=side, quantity=1, price=100, filled_quantity=1,
                                     status="closed", fee=0.1, client_order_id=cid)

    async def cancel_open_by_client_id(symbol, cid):
        return cancel

    return SimpleNamespace(supports_short=False, is_paper=paper, get_order_by_client_id=get_order_by_client_id,
                           place_notional_order=place, cancel_open_by_client_id=cancel_open_by_client_id, sent=[])


async def _prior(db, status="open", purpose="p1"):
    await db.insert_order_intent("dca", "BTC/USDT", "buy", "prev-1", amount=50, purpose=purpose)
    await db.update_order("prev-1", status)


@pytest.mark.asyncio
async def test_prior_order_still_live_is_en_vuelo(db):
    await _prior(db)
    live = OrderResult(success=True, filled_quantity=0, status="open")
    r = await execute_order(db, _stub(live), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert r.status == "en_vuelo" and not r.success
    assert len(await db.get_recent_orders()) == 1


@pytest.mark.asyncio
async def test_prior_order_filled_late_is_returned_once(db):
    await _prior(db)
    done = OrderResult(success=True, order_id="x", filled_quantity=0.4, price=101, fee=0.2, status="closed")
    r = await execute_order(db, _stub(done), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert r.success and r.status == "filled_late" and r.filled_quantity == pytest.approx(0.4)
    again = await execute_order(db, _stub(done), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert again.status == "duplicate"


@pytest.mark.asyncio
async def test_prior_order_gone_allows_resend(db):
    await _prior(db)
    r = await execute_order(db, _stub(None), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert r.success and r.status == "closed"
    statuses = sorted(o["status"] for o in await db.get_recent_orders())
    assert statuses == ["failed", "filled"]


@pytest.mark.asyncio
async def test_lookup_error_does_not_resend(db):
    await _prior(db, status="pending")
    r = await execute_order(db, _stub(OrderLookupError("red")), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert r.status == "en_vuelo"
    assert len(await db.get_recent_orders()) == 1


@pytest.mark.asyncio
async def test_filled_unrecorded_is_handed_to_module_once(db):
    await db.insert_order_intent("dca", "BTC/USDT", "buy", "prev-1", amount=50, purpose="p1")
    await db.update_order("prev-1", "filled_unrecorded", "x", 100.0, 0.5, 0.1)
    r = await execute_order(db, _stub(None), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")
    assert r.success and r.status == "filled_late" and r.filled_quantity == pytest.approx(0.5)
    assert (await execute_order(db, _stub(None), "dca", "BTC/USDT", "buy", amount=50, purpose="p1")).status == "duplicate"


@pytest.mark.asyncio
async def test_accepted_unfilled_order_is_en_vuelo_not_failed(db):
    accepted = OrderResult(success=True, filled_quantity=0, status="submitted")
    r = await execute_order(db, _stub(placed=accepted), "dca", "VOO", "buy", amount=50, purpose="p2")
    assert r.status == "en_vuelo"
    assert (await db.get_recent_orders())[0]["status"] == "open"


@pytest.mark.asyncio
async def test_reconcile_cancels_live_orders_and_keeps_fills(db):
    await _prior(db, status="pending")
    calls = []
    partial = OrderResult(success=True, filled_quantity=0.3, price=100, status="open")
    after = OrderResult(success=True, filled_quantity=0.3, price=100, status="canceled")
    seq = [partial, after]

    async def lookup(symbol, cid):
        return seq.pop(0)

    async def cancel(symbol, cid):
        calls.append(cid)
        return True
    adapter = SimpleNamespace(is_paper=False, get_order_by_client_id=lookup, cancel_open_by_client_id=cancel)
    problems = await reconcile_pending(db, {"crypto": adapter}, lambda s: "crypto")
    assert calls == ["prev-1"]
    assert problems and problems[0]["status"] == "filled_unrecorded" and problems[0]["problema"]


@pytest.mark.asyncio
async def test_reconcile_leaves_orders_it_cannot_check(db):
    await _prior(db, status="pending")
    adapter = _stub(OrderLookupError("red"))
    await reconcile_pending(db, {"crypto": adapter}, lambda s: "crypto")
    assert (await db.get_recent_orders())[0]["status"] == "pending"


# ── Papel después de reiniciar ──

@pytest.mark.asyncio
async def test_paper_sell_works_after_restart(db):
    adapter = CryptoAdapter(dry_run=True, simulate=True)
    await adapter.connect()
    feed = UnifiedFeed({"crypto": adapter})
    tracker = PortfolioTracker(db, feed, initial_cash=1_000)
    await tracker.add_position("ETH/USDT", "crypto", "buy", 0.5, 100.0, module="alloc:x")

    # "Reinicio": adaptador nuevo (sin memoria) y portafolio recargado desde la base
    fresh = CryptoAdapter(dry_run=True, simulate=True)
    await fresh.connect()
    t2 = PortfolioTracker(db, UnifiedFeed({"crypto": fresh}), initial_cash=1_000)
    await t2.load()
    fresh.paper_cash_provider, fresh.paper_holdings_provider = t2.get_cash_usd, t2.held_quantity
    sell = await fresh.place_market_order("ETH/USDT", "sell", 0.5)
    assert sell.success
    too_big = await fresh.place_market_order("BTC/USDT", "buy", 1.0)   # BTC a decenas de miles: no alcanza
    assert not too_big.success and "Saldo insuficiente" in too_big.error


@pytest.mark.asyncio
async def test_tracker_merges_duplicate_open_trades(db):
    feed = UnifiedFeed({})
    t = PortfolioTracker(db, feed, initial_cash=1_000)
    await t.add_position("BTC/USDT", "crypto", "buy", 1.0, 100.0, module="dca", fee=0.1)
    # Un segundo trade abierto del mismo módulo y símbolo (datos viejos)
    await db._conn.execute("INSERT INTO trades (timestamp, module, symbol, market, side, entry_price, quantity, status, fees) "
                           "VALUES ('2026-10-01T00:00:00', 'dca', 'BTC/USDT', 'crypto', 'long', 200.0, 1.0, 'open', 0.2)")
    await db._conn.commit()
    t2 = PortfolioTracker(db, feed, initial_cash=1_000)
    await t2.load()
    pos = t2.get_position("BTC/USDT", module="dca")
    assert pos.quantity == pytest.approx(2.0) and pos.entry_price == pytest.approx(150.0)
    assert len(await db.get_open_trades("dca")) == 1
    assert t2.cash_usd == pytest.approx(t.cash_usd - 200.0 - 0.2)


def test_ibkr_commission_estimate():
    assert estimated_commission(10, 50) == pytest.approx(1.0)        # mínimo 1 USD
    assert estimated_commission(0.5, 100) == pytest.approx(0.5)      # tope de 1% del monto
    assert estimated_commission(1000, 10) == pytest.approx(5.0)      # 0.005 por acción


# ── Llenados huérfanos y órdenes atoradas (segunda revisión de Fable) ──

@pytest.mark.asyncio
async def test_unrecorded_fill_is_taken_once_by_its_module(db):
    from core.execution import take_unrecorded
    await db.insert_order_intent("alloc:x", "BTC/USDT", "buy", "c1", amount=50, purpose="alloc:x:drift-2026102310:BTC/USDT:buy")
    await db.update_order("c1", "filled_unrecorded", "x", 100.0, 0.4, 0.2)
    assert await take_unrecorded(db, "alloc:otra") == []
    got = await take_unrecorded(db, "alloc:x")
    assert len(got) == 1 and got[0].filled_quantity == pytest.approx(0.4) and got[0].side == "buy"
    assert await take_unrecorded(db, "alloc:x") == []


@pytest.mark.asyncio
async def test_allocator_waits_while_an_order_is_in_flight(db):
    from modules.allocator.bot import AllocatorBot
    await db.insert_order_intent("alloc:x", "BTC/USDT", "buy", "c2", amount=50, purpose="alloc:x:drift-2026102310:BTC/USDT:buy")
    await db.update_order("c2", "open")
    bot = AllocatorBot({}, SimpleNamespace(_get_adapter_for_symbol=lambda s: None), None, db, {})
    bot.state["x"] = {}

    async def no_decision(key):
        raise AssertionError("no debió calcular ni operar con una orden en vuelo")
    bot._decision = no_decision
    await bot.step("x")
    assert bot.state["x"]["status"].startswith("esperando")


@pytest.mark.asyncio
async def test_stuck_order_becomes_an_alert(db):
    from core.execution import order_problems
    await db.insert_order_intent("dca", "BTC/USDT", "buy", "c3", amount=50, purpose="p-stuck")
    await db._conn.execute("UPDATE orders SET created_at = '2026-01-01T00:00:00' WHERE client_order_id = 'c3'")
    await db._conn.commit()
    problems = await order_problems(db)
    assert problems and problems[0]["client_order_id"] == "c3" and "sin resolverse" in problems[0]["problema"]


def test_tools_use_the_same_database_as_the_bot():
    from config.settings import Settings
    assert Settings(dry_run=True).db_path.name == "izalith.db"
    assert Settings(dry_run=False, binance_api_key="k").db_path.name == "izalith_real.db"
