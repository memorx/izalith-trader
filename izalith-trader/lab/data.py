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


# ══════════ Termómetros: macro, stablecoins, volatilidad, apalancamiento ══════════
#
# FRED no siempre contesta (desde la red de Memo no respondió el 22 oct 2026), así que cada
# serie macro tiene un equivalente de Yahoo:
#   curva 10 años - 3 meses  -> ^TNX - ^IRX
#   miedo crediticio         -> HYG / IEF (bonos de alto riesgo contra bonos del Tesoro)
#   miedo del mercado        -> ^VIX
#   dólar                    -> DX-Y.NYB

def _cached_series(kind: str, key: str, fetch, max_age_hours: float = 12) -> pd.Series:
    path = _cache_path(kind, key)
    if path.exists():
        age = pd.Timestamp.now(tz="UTC") - pd.Timestamp(path.stat().st_mtime, unit="s", tz="UTC")
        if age < pd.Timedelta(hours=max_age_hours):
            return pd.read_parquet(path)["value"]
    try:
        s = fetch()
        s = s[~s.index.duplicated(keep="last")].sort_index().rename("value")
        s.to_frame().to_parquet(path)
        return s
    except Exception as e:
        if path.exists():
            logger.warning(f"{kind}/{key}: se usa la copia guardada ({e})")
            return pd.read_parquet(path)["value"]
        raise


def fred_series(series_id: str) -> pd.Series:
    """Serie de FRED (Reserva Federal de St. Louis) por su CSV público."""
    def fetch():
        req = urllib.request.Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            df = pd.read_csv(resp)
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna().set_index(pd.to_datetime(df.dropna()["date"], utc=True))["value"]
    return _cached_series("fred", series_id, fetch, max_age_hours=24)


def yield_curve() -> pd.Series:
    """Diferencia 10 años - 3 meses en puntos porcentuales. Negativa = curva invertida."""
    try:
        return fred_series("T10Y3M")
    except Exception:
        tnx = load_yf("^TNX", "1995-01-01")["close"]
        irx = load_yf("^IRX", "1995-01-01")["close"]
        return (tnx - irx).dropna().rename("value")


def credit_stress() -> pd.Series:
    """HYG/IEF normalizado: baja cuando los bonos de alto riesgo sufren contra los del Tesoro."""
    hyg = load_yf("HYG", "2007-01-01")["close"]
    ief = load_yf("IEF", "2002-01-01")["close"]
    return (hyg / ief).dropna().rename("value")


def boc_series(series: str = "V39079") -> pd.Series:
    """Banco de Canadá (Valet, sin llave). V39079 = tasa objetivo; FXUSDCAD = dólar canadiense."""
    def fetch():
        data = _get_json(f"https://www.bankofcanada.ca/valet/observations/{series}/json?start_date=2000-01-01")
        obs = [(o["d"], float(o[series]["v"])) for o in data["observations"] if o.get(series, {}).get("v")]
        return pd.Series([v for _, v in obs], index=pd.to_datetime([d for d, _ in obs], utc=True))
    return _cached_series("boc", series, fetch)


def banxico_series(series: str, token: str) -> pd.Series:
    """Banxico SIE (pide token gratuito). SF61745 = tasa objetivo, SF43718 = tipo de cambio FIX."""
    if not token:
        raise ValueError("Falta BANXICO_TOKEN (se saca gratis en banxico.org.mx/SieAPIRest)")

    def fetch():
        data = _get_json(f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/{series}/datos/2000-01-01/"
                         f"{pd.Timestamp.now():%Y-%m-%d}?token={token}")
        rows = data["bmx"]["series"][0]["datos"]
        vals = [(r["fecha"], float(r["dato"].replace(",", ""))) for r in rows if r["dato"] not in ("N/E", "")]
        return pd.Series([v for _, v in vals], index=pd.to_datetime([f for f, _ in vals], format="%d/%m/%Y", utc=True))
    return _cached_series("banxico", series, fetch)


def stablecoin_supply() -> pd.Series:
    """Total de stablecoins en circulación en dólares (DefiLlama). Si crece, entra dinero a cripto."""
    def fetch():
        data = _get_json("https://stablecoins.llama.fi/stablecoincharts/all")
        return pd.Series([float(d["totalCirculatingUSD"].get("peggedUSD", 0)) for d in data],
                         index=pd.to_datetime([int(d["date"]) for d in data], unit="s", utc=True))
    return _cached_series("defillama", "stablecoins", fetch)


def deribit_dvol(currency: str = "BTC") -> pd.Series:
    """Volatilidad implícita a 30 días que el mercado de opciones espera para BTC o ETH (DVOL, Deribit)."""
    def fetch():
        end = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)
        start = int(pd.Timestamp("2021-03-01", tz="UTC").timestamp() * 1000)
        rows = []
        while end > start:
            data = _get_json(f"https://www.deribit.com/api/v2/public/get_volatility_index_data?currency={currency}"
                             f"&start_timestamp={start}&end_timestamp={end}&resolution=1D")["result"]
            batch = data.get("data") or []
            if not batch:
                break
            rows += batch
            cont = data.get("continuation")
            if not cont or cont >= end:
                break
            end = int(cont)
            time.sleep(0.2)
        return pd.Series([r[4] for r in rows], index=pd.to_datetime([r[0] for r in rows], unit="ms", utc=True))
    return _cached_series("deribit", f"dvol_{currency}", fetch)
