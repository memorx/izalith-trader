"""
Izalith Trading System - Datos históricos para el laboratorio

Todo se descarga una vez y queda en data/cache/ como parquet. Las siguientes
corridas sólo bajan lo que falta desde la última vela guardada.

Fuentes:
- Binance spot (ccxt, público): velas de cripto
- Binance USD-M (público): historial de funding de perpetuos
- Yahoo Finance (yfinance): acciones, ETFs, forex, tasas
"""
import json
import time
import urllib.request
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from core.utils.logger import get_logger

logger = get_logger("lab.data")
warnings.filterwarnings("ignore", module="yfinance")   # avisos internos de yfinance con pandas 3

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
FAPI = "https://fapi.binance.com"

_TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _cache_path(kind: str, key: str) -> Path:
    safe = key.replace("/", "-").replace("^", "_").replace("=", "_")
    path = CACHE_DIR / kind / f"{safe}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _get_json(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "izalith-lab"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as e:
            if attempt == retries - 1:
                raise
            logger.debug(f"Reintento {attempt + 1} para {url}: {e}")
            time.sleep(2 * (attempt + 1))


# ══════════ Cripto spot (Binance) ══════════

_exchange = None


def _binance():
    global _exchange
    if _exchange is None:
        import ccxt
        _exchange = ccxt.binance({"enableRateLimit": True})
    return _exchange


def load_crypto(symbol: str, timeframe: str = "1d", since: str = "2017-01-01",
                refresh: bool = True) -> pd.DataFrame:
    """Velas OHLCV de Binance spot, índice UTC. Sólo incluye velas cerradas."""
    path = _cache_path(f"crypto_{timeframe}", symbol)
    cached = pd.read_parquet(path) if path.exists() else None
    step = _TF_MS[timeframe]

    if cached is not None and not refresh:
        return cached

    ex = _binance()
    if cached is not None and len(cached):
        start = int(cached.index[-1].timestamp() * 1000) + step
    else:
        start = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    rows = []
    now_ms = ex.milliseconds()
    while start < now_ms - step:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=start, limit=1000)
        if not batch:
            break
        rows += batch
        start = batch[-1][0] + step
        if len(batch) < 1000:
            break

    if rows:
        new = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        new["timestamp"] = pd.to_datetime(new["timestamp"], unit="ms", utc=True)
        new = new.set_index("timestamp")
        df = pd.concat([cached, new]) if cached is not None else new
        df = df[~df.index.duplicated(keep="last")].sort_index()
    else:
        df = cached if cached is not None else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"])

    # La última vela puede seguir abierta: se descarta
    if len(df) and df.index[-1].timestamp() * 1000 + step > now_ms:
        df = df.iloc[:-1]

    df.to_parquet(path)
    return df


def load_crypto_closes(symbols: List[str], timeframe: str = "1d",
                       since: str = "2017-01-01") -> pd.DataFrame:
    """Cierres de varios símbolos alineados en un solo DataFrame."""
    closes = {}
    for s in symbols:
        try:
            df = load_crypto(s, timeframe, since)
            if len(df):
                closes[s] = df["close"]
        except Exception as e:
            logger.warning(f"No se pudo bajar {s}: {e}")
    return pd.DataFrame(closes).sort_index()


# ══════════ Funding de perpetuos (Binance USD-M) ══════════

def load_funding(symbol: str, since: str = "2019-09-01") -> pd.Series:
    """
    Historial de funding de un perpetuo de Binance (p. ej. 'BTCUSDT').

    Cada valor es la tasa cobrada en ese momento (normalmente cada 8h).
    Positivo = los largos le pagan a los cortos.
    """
    path = _cache_path("funding", symbol)
    cached = pd.read_parquet(path)["rate"] if path.exists() else None

    if cached is not None and len(cached):
        start = int(cached.index[-1].timestamp() * 1000) + 1
    else:
        start = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    rows = []
    while True:
        batch = _get_json(f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}&startTime={start}&limit=1000")
        if not batch:
            break
        rows += batch
        start = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
        time.sleep(0.2)

    if rows:
        new = pd.Series(
            [float(r["fundingRate"]) for r in rows],
            index=pd.to_datetime([r["fundingTime"] for r in rows], unit="ms", utc=True),
            name="rate",
        )
        s = pd.concat([cached, new]) if cached is not None else new
        s = s[~s.index.duplicated(keep="last")].sort_index()
    else:
        s = cached if cached is not None else pd.Series(dtype=float, name="rate")

    s.to_frame("rate").to_parquet(path)
    return s


def funding_daily(symbol: str, since: str = "2019-09-01") -> pd.Series:
    """Funding sumado por día UTC (lo que cobra un corto de 1 unidad de nocional)."""
    s = load_funding(symbol, since)
    if s.empty:
        return s
    return s.resample("1D").sum()


def perp_symbols() -> List[str]:
    """Perpetuos USDT vigentes en Binance."""
    info = _get_json(f"{FAPI}/fapi/v1/exchangeInfo")
    return [
        s["symbol"] for s in info["symbols"]
        if s.get("contractType") in ("PERPETUAL", "TRADIFI_PERPETUAL") and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    ]


# ══════════ Yahoo Finance ══════════

def load_yf(ticker: str, since: str = "1995-01-01", refresh: bool = True) -> pd.DataFrame:
    """
    Velas diarias de Yahoo con precios ajustados por dividendos y splits.

    El índice se normaliza a fecha UTC a medianoche para poder alinearlo con cripto.
    """
    path = _cache_path("yf", ticker)
    if path.exists() and not refresh:
        return pd.read_parquet(path)

    if path.exists():
        cached = pd.read_parquet(path)
        age = pd.Timestamp.now(tz="UTC") - pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
        if age < pd.Timedelta(hours=6):
            return cached

    import yfinance as yf
    raw = yf.download(ticker, start=since, auto_adjust=True, progress=False, threads=False)
    if raw is None or raw.empty:
        if path.exists():
            return pd.read_parquet(path)
        raise ValueError(f"Yahoo no regresó datos para {ticker}")

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].astype(float)
    df.index = pd.to_datetime(df.index).tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    df.index = df.index.normalize()
    df.index.name = "timestamp"
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["close"])
    # Durante el día Yahoo incluye la vela de hoy a medio formar: sólo cuenta después del cierre
    ny = pd.Timestamp.now(tz="America/New_York")
    if len(df) and df.index[-1].date() == ny.date() and (ny.hour, ny.minute) < (16, 30):
        df = df.iloc[:-1]
    df.to_parquet(path)
    return df


def load_yf_closes(tickers: List[str], since: str = "1995-01-01") -> pd.DataFrame:
    closes = {}
    for t in tickers:
        try:
            closes[t] = load_yf(t, since)["close"]
        except Exception as e:
            logger.warning(f"No se pudo bajar {t}: {e}")
    return pd.DataFrame(closes).sort_index()


def tbill_daily_rate(since: str = "1995-01-01") -> pd.Series:
    """
    Rendimiento diario del efectivo en dólares (T-bill a 13 semanas, ^IRX).

    ^IRX viene en % anual; se convierte a tasa por día calendario.
    """
    irx = load_yf("^IRX", since)["close"] / 100.0
    daily = (1 + irx) ** (1 / 365) - 1
    full = daily.reindex(pd.date_range(daily.index[0], daily.index[-1], freq="D", tz="UTC")).ffill()
    return full.rename("cash")
