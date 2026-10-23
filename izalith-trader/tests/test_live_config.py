"""
Pruebas de configuración en real, calendario de la bolsa, candados del allocator y datos del
laboratorio (revisión de Fable, 23 oct 2026).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
import pytz

from config.settings import Settings
from core.market_calendar import nyse_is_open
from core.utils.database import Database
from modules.allocator.bot import AllocatorBot


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test.db", tz="America/Mexico_City")
    await d.connect()
    yield d
    await d.disconnect()


# ── Papel o real, por mercado ──

def test_dry_run_is_per_market():
    s = Settings(dry_run=True, stocks_dry_run=False, stocks_broker="ibkr", binance_api_key="k")
    assert s.market_dry_run("crypto") and not s.market_dry_run("stock")
    assert s.live_markets == ["stock"]           # IBKR en real no prende Kraken
    assert not s.all_paper
    assert Settings(dry_run=False).live_markets == []     # sin llaves no hay nada que operar


def test_live_needs_both_keys_and_real_capital():
    import run
    system = run.IzalithSystem(["dashboard"])
    system.settings = Settings(dry_run=False, binance_api_key="k")
    assert "LIVE_CONFIRMED" in system._live_gate()
    system.settings = Settings(dry_run=False, binance_api_key="k", live_confirmed=True)
    assert "REAL_CAPITAL_USD" in system._live_gate()
    system.settings = Settings(dry_run=False, binance_api_key="k", live_confirmed=True, real_capital_usd=200)
    assert system._live_gate() is None
    system.settings = Settings(dry_run=True, binance_api_key="k")
    assert system._live_gate() is None and system.mode_label == "PAPEL"


# ── Calendario de NYSE ──

def test_nyse_calendar():
    ny = pytz.timezone("America/New_York")
    assert nyse_is_open(ny.localize(datetime(2026, 10, 23, 10, 0)))          # viernes normal
    assert not nyse_is_open(ny.localize(datetime(2026, 11, 26, 11, 0)))      # Acción de Gracias
    assert nyse_is_open(ny.localize(datetime(2026, 11, 27, 12, 30)))         # cierre temprano...
    assert not nyse_is_open(ny.localize(datetime(2026, 11, 27, 13, 30)))     # ...a las 13:00
    assert not nyse_is_open(ny.localize(datetime(2026, 7, 3, 11, 0)))        # 4 de julio en sábado: se descansa el viernes
    assert not nyse_is_open(ny.localize(datetime(2026, 10, 24, 11, 0)))      # sábado


# ── Laboratorio ──

def test_weekly_rebalance_falls_on_monday():
    from lab.catalog import every
    idx = pd.date_range("2026-01-01", "2026-03-31", freq="D", tz="UTC")
    mask = every(pd.DataFrame(index=idx), "W-MON")
    days = set(idx[mask.to_numpy()].day_of_week[1:])        # la primera barra del historial puede ser otro día
    assert days == {0}


def test_load_crypto_uses_cache_when_network_fails(tmp_path, monkeypatch):
    import lab.data as D
    idx = pd.date_range("2026-10-01", periods=5, freq="D", tz="UTC")
    cached = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": np.arange(5.0), "volume": 1.0}, index=idx)
    path = tmp_path / "btc.parquet"
    cached.to_parquet(path)
    monkeypatch.setattr(D, "_cache_path", lambda kind, symbol: path)

    class Down:
        def milliseconds(self):
            return int(datetime.now(timezone.utc).timestamp() * 1000)

        def fetch_ohlcv(self, *a, **k):
            raise ConnectionError("sin red")
    monkeypatch.setattr(D, "_binance", lambda: Down())
    df = D.load_crypto("BTC/USDT")
    assert len(df) == 5 and df["close"].iloc[-1] == 4.0


# ── Candados del allocator ──

def _bot(db, markets, paper=True):
    adapters = {m: SimpleNamespace(market=m, is_paper=paper) for m in set(markets.values())}
    feed = SimpleNamespace(_get_adapter_for_symbol=lambda s: adapters.get(markets.get(s)))
    return AllocatorBot(adapters, feed, None, db, {})


def _recent(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.mark.asyncio
async def test_allocator_pauses_when_an_asset_goes_missing(db):
    bot = _bot(db, {"BTC/USDT": "crypto", "ETH/USDT": "crypto"})
    full = {"universe": ["BTC/USDT", "ETH/USDT"], "asset_ends": {"BTC/USDT": _recent(), "ETH/USDT": _recent()}}
    assert await bot._data_problem("alloc:x", full, {"BTC/USDT": 0.5}, {}) is None
    short = {"universe": ["BTC/USDT"], "asset_ends": {"BTC/USDT": _recent()}}
    problem = await bot._data_problem("alloc:x", short, {"BTC/USDT": 1.0}, {})
    assert problem and "ETH/USDT" in problem


@pytest.mark.asyncio
async def test_allocator_pauses_on_stale_data(db):
    bot = _bot(db, {"BTC/USDT": "crypto"})
    extra = {"universe": ["BTC/USDT"], "asset_ends": {"BTC/USDT": _recent(days=5)}}
    assert "viejos" in await bot._data_problem("alloc:y", extra, {}, {})


@pytest.mark.asyncio
async def test_allocator_refuses_mixed_accounts_only_in_real(db):
    markets = {"BTC/USDT": "crypto", "SPY": "stock"}
    extra = {"universe": ["BTC/USDT", "SPY"], "asset_ends": {"BTC/USDT": _recent(), "SPY": _recent()}}
    assert await _bot(db, markets, paper=True)._data_problem("alloc:p", extra, {"SPY": 1.0}, {}) is None
    problem = await _bot(db, markets, paper=False)._data_problem("alloc:r", extra, {"SPY": 1.0}, {})
    assert problem and "cuentas distintas" in problem
