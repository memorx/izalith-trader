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


@pytest.mark.asyncio
async def test_every_stop_call_waits_for_the_same_shutdown():
    """Antes la segunda llamada regresaba de inmediato y asyncio cancelaba el apagado a la mitad."""
    import asyncio
    import run
    system = run.IzalithSystem(["dashboard"])
    calls = []

    async def slow_shutdown():
        calls.append("inicio")
        await asyncio.sleep(0.2)
        calls.append("fin")
    system._shutdown = slow_shutdown
    first = asyncio.create_task(system.stop())
    await asyncio.sleep(0.05)
    await system.stop()                     # la del final de main()
    assert calls == ["inicio", "fin"]       # una sola vez, y ya terminó
    await first


# ── Ideas de Fable del 24 oct ──

def _fake_ctx(monkeypatch):
    from lab.catalog import Ctx
    rng = np.random.default_rng(7)
    idx = pd.date_range("2019-01-01", periods=900, freq="D", tz="UTC")
    px = pd.DataFrame({"BTC/USDT": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.04, 900))),
                       "ETH/USDT": 50 * np.exp(np.cumsum(rng.normal(0.001, 0.05, 900)))}, index=idx)
    ctx = Ctx(refresh=False)
    monkeypatch.setattr(ctx, "crypto_close", lambda symbols: px[symbols])
    return ctx


def test_band_trades_less_than_weekly_rebalancing(monkeypatch):
    from lab.catalog import s_trend_voltarget
    from lab.ideas_fable import s_vt_banda
    ctx = _fake_ctx(monkeypatch)
    base = s_trend_voltarget(ctx)
    banda = s_vt_banda(ctx, band=0.10)
    assert banda.extra["rebalances"] < base.extra["rebalances"]
    assert set(banda.extra["universe"]) == {"BTC/USDT", "ETH/USDT"}


def test_allocator_skips_strategies_too_small_for_their_fees(tmp_path, monkeypatch, db):
    import json
    import modules.allocator.bot as alloc
    rows = [{"estrategia": "rsi2_spy", "lugar": "ibkr", "capital": c, "cagr": v}
            for c, v in [(100, -0.05), (200, 0.026), (1000, 0.07), (5000, 0.071)]]
    rows += [{"estrategia": "trend_voltarget", "lugar": "kraken_limite", "capital": c, "cagr": 0.18} for c in (100, 200, 5000)]
    path = tmp_path / "small.json"
    path.write_text(json.dumps({"filas": rows}), encoding="utf-8")
    monkeypatch.setattr(alloc, "SMALL_ACCOUNT", path)
    bot = alloc.AllocatorBot({}, SimpleNamespace(_get_adapter_for_symbol=lambda s: None), None, db,
                             {"allocator_strategies": ["rsi2_spy", "trend_voltarget"], "allocator_capital_usd": 200,
                              "crypto_exchange": "kraken"})
    assert bot.size_drag("rsi2_spy") == pytest.approx(0.045)
    assert bot._validate() == ["trend_voltarget"]
    assert bot.state["rsi2_spy"]["status"].startswith("en pausa")
