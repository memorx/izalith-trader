"""
Pruebas del adaptador de IBKR con un IB falso (no hace falta TWS ni IB Gateway).
"""
from types import SimpleNamespace

import pytest

from core.adapters.ibkr import IBKRAdapter


class FakeTrade:
    def __init__(self, order, fill_price, filled=True, reject_fraction=False):
        status = "Filled" if filled else "Inactive"
        if reject_fraction and order.totalQuantity != int(order.totalQuantity):
            status, filled = "Inactive", False
        self.order = order
        self.orderStatus = SimpleNamespace(status=status, filled=order.totalQuantity if filled else 0,
                                           avgFillPrice=fill_price if filled else 0)
        self.fills = [SimpleNamespace(commissionReport=SimpleNamespace(commission=1.0))] if filled else []
        self.log = []


class FakeIB:
    def __init__(self, price=500.0, reject_fraction=False):
        self.price = price
        self.reject_fraction = reject_fraction
        self.placed = []
        self._trades = []
        self._next = 1

    async def connectAsync(self, host, port, clientId):
        self.port = port

    def reqMarketDataType(self, t):
        self.mdt = t

    def managedAccounts(self):
        return ["DU1234567"]

    def disconnect(self):
        pass

    async def qualifyContractsAsync(self, c):
        return [c]

    async def reqTickersAsync(self, c):
        return [SimpleNamespace(last=self.price, bid=self.price - 0.05, ask=self.price + 0.05, close=self.price)]

    def placeOrder(self, contract, order):
        order.orderId = self._next
        self._next += 1
        self.placed.append(order)
        t = FakeTrade(order, self.price, reject_fraction=self.reject_fraction)
        self._trades.append(t)
        return t

    def trades(self):
        return self._trades

    async def reqExecutionsAsync(self):
        return []


@pytest.mark.asyncio
async def test_connects_and_reads_price():
    a = IBKRAdapter(port=4002, dry_run=False, ib=FakeIB())
    assert await a.connect() is True
    assert a.ib.mdt == 3
    p = await a.get_price("SPY")
    assert p.last == 500.0 and p.bid < p.ask


@pytest.mark.asyncio
async def test_live_notional_buy_uses_fractions_and_order_ref():
    a = IBKRAdapter(port=4002, dry_run=False, ib=FakeIB(price=765.0))
    await a.connect()
    r = await a.place_notional_order("SPY", "buy", 100.0, client_order_id="izabc")
    assert r.success and r.filled_quantity > 0 and r.filled_quantity < 1
    assert a.ib.placed[0].orderRef == "izabc"
    assert r.fee == pytest.approx(1.0)
    found = await a.get_order_by_client_id("SPY", "izabc")
    assert found and found.filled_quantity == r.filled_quantity


@pytest.mark.asyncio
async def test_rejected_fraction_falls_back_to_whole_shares():
    a = IBKRAdapter(port=4002, dry_run=False, ib=FakeIB(price=100.0, reject_fraction=True))
    await a.connect()
    r = await a.place_market_order("IEF", "buy", 2.5, client_order_id="izx")
    assert r.success and r.filled_quantity == 2.0
    assert r.client_order_id == "izx"


@pytest.mark.asyncio
async def test_too_small_amount_is_explained():
    a = IBKRAdapter(port=4002, dry_run=False, ib=FakeIB(price=500.0))
    await a.connect()
    r = await a.place_notional_order("SPY", "buy", 0.8)
    assert not r.success and "no alcanza" in r.error


@pytest.mark.asyncio
async def test_paper_mode_charges_ibkr_minimum():
    a = IBKRAdapter(dry_run=True, ib=FakeIB(price=500.0))
    await a.connect()
    r = await a.place_notional_order("SPY", "buy", 50.0)
    assert r.success and r.fee == pytest.approx(1.0)
