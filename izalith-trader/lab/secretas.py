"""
Izalith Trading System - Estrategias inventadas ("secretas")

Reglas para inventar sin engañarse:
1. Cada idea tiene una razón económica escrita ANTES de probarla.
2. Los parámetros se fijan antes de ver resultados y no se ajustan después.
3. Pasan las mismas pruebas que todas, más la de "no fue suerte" (Sharpe deflactado):
   como se probaron varias, la mejor tiene que ser mejor de lo que saldría por azar.

Fijadas el 22 oct 2026, antes de correr ningún backtest de ellas.
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from lab import data as D
from lab.catalog import (CRYPTO_MAJORS, EMERGENTES, PAISES, Ctx, Output, Spec, _carry_frame, _country_prices,
                         _weights_output, every, get_spec, last_of_period, on_change)
from lab.engine import Costs


# ── 1. Freno de euforia ──

def s_freno_euforia(ctx: Ctx, window=14, pct=0.90, history=365) -> Output:
    """
    La tendencia BTC+ETH con volatilidad controlada, pero en cero mientras el funding de BTC
    de las últimas 2 semanas esté en su 10% más alto del último año (todos apalancados en largo).
    """
    fund = ctx.funding(["BTCUSDT"])["BTCUSDT"]
    f = fund.rolling(window, min_periods=window).mean()
    thr = f.rolling(history, min_periods=history).quantile(pct)
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    px = px[px.index >= fund.index[0]]
    euphoric = (f > thr).reindex(px.index).fillna(False).astype(bool)
    n = len(px.columns)
    rv = px.pct_change().rolling(30).std() * np.sqrt(365)
    trend = (px > px.rolling(100).mean()).astype(float)
    w = (trend * (0.25 / n / rv).clip(upper=1.0 / n)).fillna(0.0)
    w[euphoric.to_numpy()] = 0.0
    flips = (w > 0).astype(int).diff().abs().sum(axis=1) > 0
    t = w.copy()
    t[~(flips | every(w, "W-MON"))] = np.nan
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── 2. Capitulación ──

def s_capitulacion(ctx: Ctx, fund_days=7, drop=-0.15, hold=30) -> Output:
    """Compra BTC por 30 días cuando el funding de la semana es negativo y BTC cayó más de 15% en un mes."""
    spot = ctx.crypto_close(["BTC/USDT"]).dropna()
    fund = ctx.funding(["BTCUSDT"])["BTCUSDT"]
    px = spot[spot.index >= fund.index[0]]
    c = px["BTC/USDT"]
    f = fund.rolling(fund_days, min_periods=fund_days).mean().reindex(px.index)
    ret30 = c / c.shift(30) - 1
    signal = (f < 0) & (ret30 < drop)
    state, until, vals = 0.0, None, []
    for i, ts in enumerate(px.index):
        if signal.iat[i]:
            until = i + hold
            state = 1.0
        elif until is not None and i >= until:
            state, until = 0.0, None
        vals.append(state)
    t = pd.DataFrame({"BTC/USDT": vals}, index=px.index)
    changed = t["BTC/USDT"].diff().fillna(1) != 0
    t[~changed] = np.nan
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT")


# ── 3. El peso como semáforo ──

def s_peso_semaforo(ctx: Ctx, sma=100) -> Output:
    """Emergentes en partes iguales sólo cuando el peso está fuerte (USD/MXN abajo de su promedio de 100 días)."""
    px = _country_prices(ctx, EMERGENTES)
    usdmxn = D.load_yf("MXN=X", "2000-01-01", refresh=ctx.refresh)["close"]
    usdmxn = usdmxn.reindex(px.index).ffill()
    strong = (usdmxn < usdmxn.rolling(sma).mean()) & usdmxn.notna()
    alive = px.notna()
    w = alive.astype(float).div(alive.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    w = w.mul(strong.astype(float), axis=0)
    flips = strong.astype(int).diff().abs() > 0
    t = w.copy()
    t[~(flips | every(w, "MS")).to_numpy()] = np.nan
    t = t[t.index >= usdmxn.dropna().index[0]]
    px = px[px.index >= t.index[0]]
    out = _weights_output(ctx, px, t, ctx.cost("intl_etf"), "EWW", cash=ctx.cash_for(px.index), min_trade=0.01)
    out.extra["aviso"] = "Sesgo de supervivencia en los ETFs de país (sin Rusia ni Colombia)."
    return out


# ── 4. Cobre contra oro ──

def s_cobre_oro(ctx: Ctx, lookback=126) -> Output:
    """Cada mes: S&P 500 si el cobre le gana al oro en 6 meses (crecimiento); si no, bonos del Tesoro."""
    px = ctx.etf_close(["SPY", "IEF"]).dropna()
    hg = D.load_yf("HG=F", "2000-01-01", refresh=ctx.refresh)["close"]
    gc = D.load_yf("GC=F", "2000-01-01", refresh=ctx.refresh)["close"]
    ratio = (hg / gc).reindex(px.index).ffill()
    growth = ratio > ratio.rolling(lookback).mean()
    month_end = last_of_period(px.index, "ME")
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[month_end.to_numpy()]:
        if pd.isna(ratio.get(ts)) or pd.isna(ratio.rolling(lookback).mean().get(ts)):
            continue
        t.loc[ts] = pd.Series({"SPY": 1.0, "IEF": 0.0} if growth.get(ts) else {"SPY": 0.0, "IEF": 1.0})
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), "SPY", cash=ctx.cash_for(px.index))


# ── 5. Países con moneda fuerte ──

MONEDA = {
    "EWW": "MXN=X", "EWZ": "BRL=X", "ECH": "CLP=X", "EPU": "PEN=X", "ARGT": "ARS=X",
    "EWJ": "JPY=X", "EWY": "KRW=X", "EWT": "TWD=X", "INDA": "INR=X", "MCHI": "CNY=X", "EWH": None,
    "EWS": "SGD=X", "EWM": "MYR=X", "THD": "THB=X", "EIDO": "IDR=X", "EPHE": "PHP=X", "VNM": "VND=X",
    "EZA": "ZAR=X", "AFK": "ZAR=X", "KSA": None, "TUR": "TRY=X", "UAE": None, "QAT": None,
    "EWG": "EUR=X", "EWU": "GBP=X", "EWQ": "EUR=X", "EWP": "EUR=X", "EWI": "EUR=X", "EPOL": "PLN=X",
    "GREK": "EUR=X", "SPY": None, "EWC": "CAD=X", "EWA": "AUD=X",
}


def s_paises_moneda_fuerte(ctx: Ctx, top=4, lookback=252, skip=21, fx_window=126, fx_drop=0.10) -> Output:
    """Rotación a los 4 países más fuertes, pero sin los que se devaluaron más de 10% en 6 meses."""
    tickers = list(PAISES)
    px = _country_prices(ctx, tickers)
    cash = ctx.cash_for(px.index)
    cash_12m = (1 + cash).rolling(lookback).apply(np.prod, raw=True) - 1
    mom = px.shift(skip) / px.shift(lookback) - 1
    fx = {}
    for tk, sym in MONEDA.items():
        if sym and sym not in fx:
            try:
                fx[sym] = D.load_yf(sym, "2000-01-01", refresh=ctx.refresh)["close"].reindex(px.index).ffill()
            except Exception:
                fx[sym] = None
    # USD/local sube = la moneda local se devalúa
    deval = {tk: (fx[sym] / fx[sym].shift(fx_window) - 1) if sym and fx.get(sym) is not None else None
             for tk, sym in MONEDA.items()}
    month_end = last_of_period(px.index, "ME")
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[month_end.to_numpy()]:
        row = pd.Series(0.0, index=px.columns)
        m = mom.loc[ts].dropna()
        m = m[m > (cash_12m.get(ts, 0.0) or 0.0)]
        ok = [tk for tk in m.index if deval.get(tk) is None or not (deval[tk].get(ts, 0.0) > fx_drop)]
        for tk in m[ok].nlargest(top).index:
            row[tk] = 1.0 / top
        t.loc[ts] = row
    out = _weights_output(ctx, px, t, ctx.cost("intl_etf"), "SPY", bench_name="Comprar y aguantar SPY", cash=cash)
    out.extra["aviso"] = "Sesgo de supervivencia en los ETFs de país (sin Rusia ni Colombia)."
    return out


# ── 6. BTC contra ETH ──

def s_btc_eth_relativo(ctx: Ctx, lookback=60, sma=100) -> Output:
    """Cada semana, todo en la que vaya ganando de BTC y ETH en 60 días, sólo si está arriba de su promedio de 100."""
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    mom = px / px.shift(lookback) - 1
    above = px > px.rolling(sma).mean()
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[every(t, "W-MON").to_numpy()]:
        row = pd.Series(0.0, index=px.columns)
        m = mom.loc[ts].dropna()
        if len(m) == 2:
            best = m.idxmax()
            if above.at[ts, best]:
                row[best] = 1.0
        t.loc[ts] = row
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── 7. Carry de oro ──

def s_oro_carry(ctx: Ctx, notional=0.5, lookback=7) -> Output:
    """Oro tokenizado (PAXG) en spot y corto en su perpetuo, por el mismo monto; cobra el funding si es positivo."""
    px, fund, perps = _carry_frame(ctx, ["PAXG/USDT"])
    trail = fund.rolling(lookback, min_periods=lookback).mean()
    t = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    on = (trail["PAXGUSDT"] > 0).astype(float)
    t["PAXG/USDT"] = on * notional
    t["PAXGUSDT"] = -on * notional
    flips = t.diff().abs().sum(axis=1) > 0
    t[~(flips | every(t, "W-MON"))] = np.nan
    costs = {"PAXG/USDT": ctx.cost("binance_spot"), "PAXGUSDT": ctx.cost("binance_perp")}
    out = _weights_output(ctx, px, t, costs, "PAXG/USDT", bench_name="Comprar y aguantar PAXG (oro)",
                          funding=fund, perp_cols=perps, min_trade=0.01)
    out.extra["aviso"] = "Historial corto: el perpetuo de PAXG existe desde marzo de 2025."
    return out


# ── 8 a 11. Termómetros como filtro (fijadas el 22 oct 2026, antes de probarlas) ──

def _with_filter(ctx: Ctx, base_key: str, risk_on: pd.Series) -> Output:
    """
    La estrategia base tal cual, pero en efectivo mientras el termómetro diga "fuera".
    Se trata la curva de la base como un activo: entrar o salir cuesta 0.15% por lado.
    """
    spec = get_spec(base_key)
    base = spec.run(ctx, **spec.params)
    curve = (1 + base.returns).cumprod()
    risk_on = risk_on.dropna()
    start = max(curve.index[0], risk_on.index[0])
    curve = curve[curve.index >= start]
    on = risk_on.astype(float).reindex(curve.index, method="ffill").fillna(0.0)
    px = curve.to_frame("base")
    t = on_change(on.to_frame("base"))
    cash = ctx.cash_for(px.index) if base.cash is not None else None
    out = _weights_output(ctx, px, t, Costs(0.001 * ctx.cost_mult, 0.0005 * ctx.cost_mult), "base", cash=cash)
    if base.benchmark is not None:
        out.benchmark = base.benchmark[base.benchmark.index >= start]
        out.benchmark = out.benchmark / out.benchmark.iloc[0]
        out.benchmark_name = base.benchmark_name
    # Lo que el allocator tiene que replicar: el objetivo de la base, o nada si el filtro dice "fuera"
    now_on = bool(on.iloc[-1] > 0)
    flips = on.diff().fillna(0) != 0
    last_flip = on.index[flips.to_numpy()][-1].isoformat() if flips.any() else on.index[0].isoformat()
    out.extra.update({
        "target": (base.extra.get("target") or {}) if now_on else {},
        "target_date": max(base.extra.get("target_date") or "", last_flip),
        "perp_cols": base.extra.get("perp_cols") or [],
        "universe": base.extra.get("universe") or [],
        "asset_ends": base.extra.get("asset_ends") or {},
        "filtro_activo": not now_on,
        "sesgo_seleccion": bool(base.extra.get("sesgo_seleccion")),
    })
    if base.extra.get("aviso"):
        out.extra["aviso"] = base.extra["aviso"]
    return out


def s_filtro_credito(ctx: Ctx, sma=100, margin=0.02) -> Output:
    """Portafolio chico, en efectivo cuando los bonos de alto riesgo sufren (HYG/IEF 2% abajo de su promedio de 100 días)."""
    c = D.credit_stress()
    return _with_filter(ctx, "combo_chico", c >= c.rolling(sma).mean() * (1 - margin))


def s_filtro_curva(ctx: Ctx) -> Output:
    """Tendencia del S&P 500, pero sin acciones mientras la curva de tasas esté invertida."""
    return _with_filter(ctx, "faber_trend", D.yield_curve() >= 0)


def s_filtro_stablecoins(ctx: Ctx, days=30) -> Output:
    """Tendencia BTC+ETH sólo si el total de stablecoins creció en el último mes (entra dinero a cripto)."""
    s = D.stablecoin_supply()
    s = s.resample("1D").last().ffill()
    return _with_filter(ctx, "trend_voltarget", s > s.shift(days))


def s_filtro_panico(ctx: Ctx, limit=70.0) -> Output:
    """Tendencia BTC+ETH, en efectivo mientras la volatilidad que espera el mercado de opciones pase de 70%."""
    return _with_filter(ctx, "trend_voltarget", D.deribit_dvol("BTC") < limit)


SECRETAS: List[Spec] = [
    Spec("sec_freno_euforia", "Freno de euforia (inventada)", 2, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La tendencia BTC+ETH con volatilidad controlada, pero se sale cuando el funding está en su 10% más alto del año: cuando todos están apalancados en largo vienen las cascadas de liquidaciones.",
         "Puede salirse justo antes de las mejores semanas de un mercado alcista.", 100, "Semanal",
         s_freno_euforia, {}, {"pct": [0.8, 0.95], "window": [7, 30]}, datos="BTC, ETH + funding de BTC desde 2019"),
    Spec("sec_capitulacion", "Comprar la capitulación (inventada)", 3, "Cripto",
         "Kraken / Binance (sólo spot)",
         "Compra BTC 30 días cuando el funding es negativo (cortos amontonados) y BTC cayó más de 15% en un mes: las ventas forzadas se agotan y los cortos tienen que recomprar.",
         "Comprar en una caída que sigue (2022); pocas señales.", 100, "Por evento",
         s_capitulacion, {}, {"drop": [-0.10, -0.20], "hold": [15, 60]}, datos="BTC + funding desde 2019"),
    Spec("sec_peso_semaforo", "El peso como semáforo (inventada)", 2, "Acciones de 22 países emergentes",
         "IBKR (ETFs de país)",
         "Emergentes sólo cuando el peso mexicano está fuerte: el peso es la moneda emergente más líquida del mundo y la primera que siente cuando el apetito de riesgo sube o baja.",
         "El peso puede moverse por razones sólo de México.", 1000, "Mensual",
         s_peso_semaforo, {}, {"sma": [50, 200]}, datos="22 ETFs de país + USD/MXN desde 2003"),
    Spec("sec_cobre_oro", "Cobre contra oro (inventada)", 2, "Acciones y bonos EE.UU.",
         "IBKR",
         "S&P 500 cuando el cobre le gana al oro en 6 meses (la economía crece), bonos cuando el oro le gana (miedo).",
         "Shocks de oferta en metales que no tienen que ver con la economía.", 500, "Mensual",
         s_cobre_oro, {}, {"lookback": [63, 252]}, datos="SPY, IEF, cobre (HG) y oro (GC) desde 2002"),
    Spec("sec_paises_moneda_fuerte", "Países fuertes con moneda fuerte (inventada)", 3, "Acciones de 33 países",
         "IBKR (ETFs de país)",
         "La rotación a los 4 países más fuertes, pero sin los que se devaluaron más de 10% en 6 meses: no entrar a una crisis de moneda (Turquía, Argentina).",
         "Mismos de la rotación de países; algunas monedas tienen datos con huecos.", 1000, "Mensual",
         s_paises_moneda_fuerte, {}, {"fx_drop": [0.05, 0.20], "top": [2, 6]}, datos="33 ETFs + monedas desde 2003"),
    Spec("sec_btc_eth_relativo", "BTC contra ETH (inventada)", 3, "Cripto",
         "Kraken / Binance (sólo spot)",
         "Todo en la que vaya ganando entre BTC y ETH en 60 días, sólo si está en tendencia: cuando ETH le gana a BTC suele ser una racha, y al revés.",
         "Concentrar todo en una sola moneda; rachas cortas que se revierten.", 100, "Semanal",
         s_btc_eth_relativo, {}, {"lookback": [30, 90], "sma": [50, 200]}, datos="BTC, ETH desde 2017"),
    Spec("sec_oro_carry", "Carry de oro tokenizado (inventada)", 1, "Oro (PAXG) spot + perpetuo",
         "Binance (spot y perpetuo en la misma cuenta; no disponible en Canadá)",
         "Compra oro tokenizado y vende su perpetuo por el mismo monto: el precio del oro se cancela y queda el funding, que en 2026 llegó a pagar más de 30% al año en los perpetuos de oro.",
         "Historial muy corto; el funding puede caer; riesgo del emisor de PAXG y del exchange.", 1000, "Semanal",
         s_oro_carry, {}, {"lookback": [3, 14]}, datos="PAXG spot + funding del perpetuo desde 2025"),
    Spec("sec_filtro_credito", "Freno por crédito (inventada)", 2, "Cripto y acciones EE.UU.",
         "Kraken (cripto) + IBKR (SPY)",
         "El portafolio chico, pero en efectivo cuando los bonos de alto riesgo sufren contra los del Tesoro: el crédito suele asustarse antes que las acciones.",
         "Señales falsas en sustos de crédito que no pasan a más.", 200, "Diaria",
         s_filtro_credito, {}, {"sma": [50, 200], "margin": [0.01, 0.04]}, datos="Portafolio chico + HYG/IEF desde 2017"),
    Spec("sec_filtro_curva", "Freno por curva invertida (inventada)", 2, "Acciones EE.UU.",
         "IBKR",
         "La tendencia del S&P 500, pero sin acciones mientras la curva de tasas esté invertida: antes de cada recesión de EE.UU. desde los 70 la curva se invirtió.",
         "La curva puede quedarse invertida mucho tiempo sin recesión (2022-2024) y dejarte fuera de la subida.", 500, "Diaria",
         s_filtro_curva, {}, {}, datos="Faber + curva 10 años - 3 meses desde 1995"),
    Spec("sec_filtro_stablecoins", "Freno por stablecoins (inventada)", 2, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La tendencia BTC+ETH, sólo cuando el total de stablecoins creció en el último mes: las stablecoins son el efectivo que entra a cripto.",
         "El crecimiento de stablecoins puede ir detrás del precio, no adelante.", 100, "Diaria",
         s_filtro_stablecoins, {}, {"days": [14, 60]}, datos="Tendencia BTC+ETH + stablecoins (DefiLlama) desde 2018"),
    Spec("sec_filtro_panico", "Freno por pánico en opciones (inventada)", 2, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La tendencia BTC+ETH, en efectivo mientras el mercado de opciones espere movimientos de más de 70% al año: es cuando vienen las liquidaciones en cascada.",
         "La volatilidad alta también llega en las subidas fuertes.", 100, "Diaria",
         s_filtro_panico, {}, {"limit": [60.0, 85.0]}, datos="Tendencia BTC+ETH + DVOL (Deribit) desde 2021"),
]

# Contra qué se mide cada idea: la versión sin la idea (fijado junto con los parámetros)
BASES = {
    "sec_freno_euforia": "trend_voltarget",
    "sec_capitulacion": "cash",
    "sec_peso_semaforo": "emergentes_iguales",
    "sec_cobre_oro": "etf_60_40",
    "sec_paises_moneda_fuerte": "paises_momentum",
    "sec_btc_eth_relativo": "trend_btc",
    "sec_oro_carry": "cash",
    "sec_filtro_credito": "combo_chico",
    "sec_filtro_curva": "faber_trend",
    "sec_filtro_stablecoins": "trend_voltarget",
    "sec_filtro_panico": "trend_voltarget",
}

for _s in SECRETAS:
    _s.inventada = True
    _s.base = BASES[_s.key]
