"""
Izalith Trading System - Ideas de la sesión con Fable (24 oct 2026)

Fable propuso ~20 ideas con su razón económica, datos, regla y qué resultado las mata
(docs/IDEAS-2026-10-24.md). Aquí van las que se pueden probar ya. Las reglas y parámetros
se fijaron el 24 oct 2026 ANTES de correr ningún backtest de ellas, y no se ajustan después.

Las que buscan una ventaja nueva son "inventadas": además del semáforo tienen que pasar
"no fue suerte" contra su base, y cada una (con sus variantes) sube el número de ideas
probadas para todas. Las que sólo cambian cómo se ejecuta una estrategia de libro (dónde
queda el efectivo, universo sin ver el futuro) se califican como estrategias normales.
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from lab.catalog import (CRYPTO_MAJORS, Ctx, Output, Spec, _weights_output, every, last_of_period, on_change)


# ── B1. Tendencia BTC+ETH con banda de tolerancia ──

def s_vt_banda(ctx: Ctx, band=0.10, sma=100, target_vol=0.25, vol_window=30) -> Output:
    """
    La tendencia BTC+ETH con volatilidad controlada, pero sólo se mueve si cambia la señal de
    tendencia o si el peso objetivo de algún activo cambió más de `band` desde el último ajuste.
    Hoy rebalancea ~40 veces al año y en Kraken eso cuesta ~3.9 puntos al año.
    """
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    n = len(px.columns)
    rv = px.pct_change().rolling(vol_window).std() * np.sqrt(365)
    trend = (px > px.rolling(sma).mean()).astype(float)
    w = (trend * (target_vol / n / rv).clip(upper=1.0 / n)).fillna(0.0)
    flips = (trend.diff().abs().sum(axis=1) > 0).to_numpy()
    weekly = every(w, "W-MON").to_numpy()
    t = pd.DataFrame(np.nan, index=w.index, columns=w.columns)
    last = None
    for i in range(len(w)):
        row = w.iloc[i].to_numpy()
        if last is None or flips[i] or (weekly[i] and np.max(np.abs(row - last)) > band):
            t.iloc[i] = row
            last = row
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── B2. Faber con la pata de efectivo en BIL (T-bills) ──

def s_faber_bil(ctx: Ctx, asset="SPY", parking="BIL", months=10) -> Output:
    """
    Faber de libro, pero cuando está fuera del índice el dinero va a BIL (T-bills de 1-3 meses)
    en vez de quedarse como efectivo. En la cuenta real el efectivo ocioso no gana nada
    (Kraken 0%, IBKR 0% con menos de 10 mil dólares); BIL sí, a cambio de una orden más.
    No es una ventaja nueva: es dónde poner el efectivo.
    """
    px = ctx.etf_close([asset, parking]).dropna()
    close = px[asset]
    month_end = last_of_period(px.index, "ME")
    monthly = close[month_end]
    sma = monthly.rolling(months).mean()
    sig = (monthly > sma).astype(float).where(sma.notna())
    on = sig.reindex(px.index).ffill().fillna(0.0)
    target = pd.DataFrame({asset: on, parking: 1.0 - on}, index=px.index)
    t = on_change(target)
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), asset, bench_name=f"Comprar y aguantar {asset}")


# ── A2. BTC + oro en tendencia con volatilidad controlada (todo en Kraken) ──

def _gold_crypto(ctx: Ctx, index: pd.DatetimeIndex) -> pd.Series:
    """
    Oro en el calendario de cripto: PAXG (oro tokenizado, se opera en Kraken) desde que hay
    velas; antes, GLD (Yahoo) como sustituto. Los fines de semana el oro no se mueve.
    """
    gld = ctx.etf_close(["GLD"])["GLD"].dropna()
    gld.index = gld.index.tz_localize("UTC") if gld.index.tz is None else gld.index.tz_convert("UTC")
    gld = gld.reindex(index.union(gld.index)).ffill().reindex(index)
    ret = gld.pct_change()
    try:
        paxg = ctx.crypto_close(["PAXG/USDT"])["PAXG/USDT"].dropna().reindex(index)
        p_ret = paxg.pct_change()
        ret = p_ret.where(p_ret.notna(), ret)
    except Exception:
        pass
    return (1 + ret.fillna(0.0)).cumprod()


def s_btc_oro(ctx: Ctx, sma=100, target_vol=0.25, vol_window=30) -> Output:
    """
    Como la tendencia BTC+ETH con volatilidad controlada, pero con BTC y oro: la misma prima de
    tendencia en dos activos que casi no se mueven juntos, y los dos en una sola cuenta (Kraken
    tiene PAXG). Cada uno entra si está arriba de su promedio de `sma` días, con tamaño para
    apuntar a `target_vol` de volatilidad; revisión semanal y en cada cambio de tendencia.
    """
    btc = ctx.crypto_close(["BTC/USDT"]).dropna()
    px = btc.copy()
    px["PAXG/USDT"] = _gold_crypto(ctx, btc.index)
    px = px.dropna()
    n = len(px.columns)
    rv = px.pct_change().rolling(vol_window).std() * np.sqrt(365)
    trend = (px > px.rolling(sma).mean()).astype(float)
    w = (trend * (target_vol / n / rv).clip(upper=1.0 / n)).fillna(0.0)
    flips = trend.diff().abs().sum(axis=1) > 0
    weekly = every(w, "W-MON")
    t = w.copy()
    t[~(flips | weekly)] = np.nan
    # PAXG tiene el libro delgado: se le cobra el deslizamiento de una altcoin (revisión de Fable, 24 oct)
    costs = {"BTC/USDT": ctx.cost("binance_spot"), "PAXG/USDT": ctx.cost("altcoin_spot")}
    out = _weights_output(ctx, px, t, costs, "BTC/USDT", min_trade=0.02)
    out.extra["aviso"] = ("Antes de 2020 el oro es GLD (Yahoo) como sustituto de PAXG. La idea del oro se "
                          "propuso conociendo su racha de 2019-2025: por eso es inventada.")
    return out


# ── A1. Faber con oro y bonos (GTAA-3) ──

def s_gtaa3(ctx: Ctx, months=10, assets=("SPY", "GLD", "IEF")) -> Output:
    """
    Faber (2007) en tres activos que se parecen poco: S&P 500, oro y bonos del Tesoro. Cada mes,
    cada uno ocupa un tercio si cerró arriba de su promedio de `months` meses; si no, ese tercio
    queda en efectivo.
    """
    px = ctx.etf_close(list(assets)).dropna()
    month_end = last_of_period(px.index, "ME")
    monthly = px[month_end]
    sma = monthly.rolling(months).mean()
    sig = (monthly > sma).astype(float).where(sma.notna()) / len(assets)
    target = sig.reindex(px.index).ffill().fillna(0.0)
    t = on_change(target)
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), "SPY", cash=ctx.cash_for(px.index))


# ── A3. Faber con volatilidad manejada (Moreira y Muir, 2017) ──

def s_faber_vol(ctx: Ctx, target_vol=0.12, vol_window=21, months=10, band=0.20, asset="SPY") -> Output:
    """
    Faber, pero cuando está dentro el tamaño es min(1, `target_vol` / volatilidad de 21 días):
    cuando la volatilidad sube, el rendimiento esperado no sube igual, así que se baja la
    exposición. Revisión mensual; sólo se mueve si el peso cambia más de `band` o cambia la señal.
    """
    px = ctx.etf_close([asset]).dropna()
    close = px[asset]
    month_end = last_of_period(px.index, "ME")
    monthly = close[month_end]
    sma = monthly.rolling(months).mean()
    sig = (monthly > sma).astype(float).where(sma.notna())
    rv = close.pct_change().rolling(vol_window).std() * np.sqrt(252)
    size = (target_vol / rv).clip(upper=1.0)
    want = (sig * size[month_end]).dropna()
    t = pd.DataFrame(np.nan, index=px.index, columns=[asset])
    last = None
    for ts, wv in want.items():
        flip = last is not None and (wv > 0) != (last > 0)
        if last is None or flip or abs(wv - last) > band:
            t.loc[ts, asset] = wv
            last = wv
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), asset, cash=ctx.cash_for(px.index))


# ── A6. Rotación cripto con universo sin ver el futuro ──

# Monedas candidatas fijadas el 24 oct 2026: las que estuvieron entre las grandes en algún
# momento de 2017-2024 y todavía tienen velas en Binance. Las que desaparecieron (LUNA, FTT,
# XMR...) no tienen datos gratis: el sesgo de supervivencia baja, pero no desaparece.
XS_CANDIDATES = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
    "TRX/USDT", "LTC/USDT", "LINK/USDT", "BCH/USDT", "DOT/USDT", "AVAX/USDT", "XLM/USDT",
    "ETC/USDT", "ATOM/USDT", "UNI/USDT", "FIL/USDT", "NEAR/USDT", "AAVE/USDT",
    "EOS/USDT", "NEO/USDT", "IOTA/USDT", "XTZ/USDT", "ZEC/USDT", "DASH/USDT", "BAT/USDT",
    "VET/USDT", "THETA/USDT", "ALGO/USDT", "ICP/USDT", "SAND/USDT", "MANA/USDT", "AXS/USDT",
    "EGLD/USDT", "HBAR/USDT", "QNT/USDT", "CRV/USDT", "CAKE/USDT", "ONT/USDT", "ZIL/USDT",
    "WAVES/USDT", "ICX/USDT", "QTUM/USDT", "OMG/USDT", "CHZ/USDT", "GRT/USDT", "COMP/USDT",
]


def s_xs_pit(ctx: Ctx, lookback=28, top=3, regime_sma=100, universe_size=20, min_history=90) -> Output:
    """
    La rotación a las 3 cripto más fuertes, pero el universo de cada lunes son las 20 monedas con
    más volumen en dólares de los últimos 30 días, entre las que ya tenían al menos 90 días de
    velas en esa fecha. Así no se eligen hoy las ganadoras de ayer.
    """
    closes, volumes = {}, {}
    for sym in XS_CANDIDATES:
        try:
            df = ctx.crypto_df(sym, "1d", ctx.crypto_since)
        except Exception:
            continue
        if len(df):
            closes[sym] = df["close"]
            volumes[sym] = df["close"] * df["volume"]
    px = pd.DataFrame(closes).sort_index()
    qvol = pd.DataFrame(volumes).reindex(px.index).rolling(30, min_periods=20).sum()
    btc = px["BTC/USDT"]
    regime = btc > btc.rolling(regime_sma).mean()
    mom = px / px.shift(lookback) - 1
    history_ok = px.notna().rolling(min_history).sum() >= min_history
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[every(t, "W-MON").to_numpy()]:
        row = pd.Series(0.0, index=px.columns)
        if regime.get(ts, False):
            eligible = qvol.loc[ts][history_ok.loc[ts]].dropna().nlargest(universe_size).index
            cand = mom.loc[ts, eligible].dropna()
            for sym in cand.nlargest(top).index:
                row[sym] = 1.0 / top
        t.loc[ts] = row
    out = _weights_output(ctx, px, t, ctx.cost("altcoin_spot"), "BTC/USDT")
    out.extra["aviso"] = ("Sesgo de supervivencia sustancial en 2017-2019: las 48 candidatas se eligieron "
                          "sabiendo cuáles sobrevivieron y cuáles crecieron; las que murieron (LUNA, FTT, NANO...) "
                          "no están. El rendimiento es una cota superior, no una estimación.")
    return out


IDEAS: List[Spec] = [
    Spec("fab_vt_banda", "Tendencia BTC+ETH con banda (idea de Fable)", 3, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La misma tendencia con volatilidad controlada, pero sólo se mueve si el peso cambia más de 10 puntos o cambia la tendencia: menos órdenes, menos comisión.",
         "Se queda más tiempo con un tamaño que ya no es el ideal.", 100, "Semanal",
         s_vt_banda, {}, {"band": [0.05, 0.15]}, datos="BTC y ETH (Binance) desde 2017"),
    Spec("fab_faber_bil", "Faber con el efectivo en T-bills (idea de Fable)", 2, "Acciones EE.UU.",
         "IBKR",
         "Faber de libro, pero fuera del índice el dinero va a BIL (bonos del Tesoro de 1 a 3 meses) en vez de quedarse sin rendir.",
         "Una orden más en cada cambio de señal; con 200 dólares el mínimo de 1 dólar de IBKR pesa.", 500, "Mensual",
         s_faber_bil, {}, {"months": [8, 12]}, datos="SPY + BIL (Yahoo) desde 2007"),
    Spec("fab_btc_oro", "BTC y oro en tendencia (idea de Fable)", 3, "Cripto y oro tokenizado",
         "Kraken (BTC y PAXG en la misma cuenta)",
         "Tendencia con volatilidad controlada en BTC y en oro: dos activos que casi no se mueven juntos y que se pueden tener en una sola cuenta.",
         "El oro tokenizado depende de su emisor (Paxos); el oro puede pasar años de lado.", 100, "Semanal",
         s_btc_oro, {}, {"sma": [50, 200], "target_vol": [0.20, 0.30]},
         datos="BTC (Binance) + PAXG desde 2020 y GLD (Yahoo) antes"),
    Spec("fab_gtaa3", "Faber con oro y bonos (idea de Fable)", 2, "Acciones, oro y bonos EE.UU.",
         "IBKR",
         "Faber en tres activos que se parecen poco: S&P 500, oro y bonos del Tesoro, un tercio cada uno mientras esté en tendencia.",
         "Con 200 dólares son unas 6 órdenes al año con mínimo de 1 dólar cada una.", 500, "Mensual",
         s_gtaa3, {}, {"months": [6, 8, 12]}, datos="SPY, GLD e IEF (Yahoo) desde 2004"),
    Spec("fab_faber_vol", "Faber con volatilidad manejada (idea de Fable)", 2, "Acciones EE.UU.",
         "IBKR",
         "Faber, pero dentro del índice el tamaño baja cuando la volatilidad sube (Moreira y Muir, 2017).",
         "Más órdenes que Faber; puede quedarse chico en un rebote fuerte después de una caída.", 500, "Mensual",
         s_faber_vol, {}, {"target_vol": [0.10, 0.15]}, datos="SPY (Yahoo) desde 1995"),
    Spec("fab_xs_pit", "Rotación cripto sin ver el futuro (idea de Fable)", 4, "Cripto (altcoins)",
         "Binance (Kraken tiene menos monedas)",
         "La rotación a las 3 cripto más fuertes, pero eligiendo el universo de cada semana con el volumen que había en ese momento: dice si el 97% de la rotación original es real.",
         "Caídas de 60% o más en altcoins; las monedas que desaparecieron no están en los datos.", 500, "Semanal",
         s_xs_pit, {}, {"top": [2, 5], "lookback": [14, 56]}, datos="48 monedas candidatas (Binance) desde 2017"),
]

# Las que buscan una ventaja nueva se miden contra su versión de libro
BASES: Dict[str, str] = {
    "fab_btc_oro": "trend_voltarget",
    "fab_gtaa3": "faber_trend",
    "fab_faber_vol": "faber_trend",
}

for _s in IDEAS:
    if _s.key in BASES:
        _s.inventada = True
        _s.base = BASES[_s.key]

# 24 oct 2026, ronda 2: la banda no busca una ventaja nueva (misma señal, menos órdenes), así que
# pasa a la clase "ejecución" que propuso Fable. La regla se fijó antes de calcular si la banda pasa.
_banda = next(s for s in IDEAS if s.key == "fab_vt_banda")
_banda.ejecucion, _banda.base = True, "trend_voltarget"
