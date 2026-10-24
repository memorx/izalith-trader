"""
Izalith Trading System - Ideas, ronda 2 (24 oct 2026)

Ideas de Fable que quedaron de su lista (A4, A5, C1, C3), sus ideas nuevas de costo y ejecución
(N1, N2) y una mía (BTC + oro con banda). Las reglas se mandaron a Fable ANTES de correr ningún
backtest; aquí están con sus correcciones. No se ajustan después de ver resultados.

Clases:
- "inventada": busca una ventaja nueva; pasa el semáforo y además "no fue suerte".
- "ejecución": la MISMA señal que su base, con distinto tamaño, momento o costo. Hereda el
  semáforo de la base si rinde igual antes de costos, cuesta menos y no cae más (lab/review.py).
- de libro: se califica como cualquier estrategia del catálogo.
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from lab.catalog import (CRYPTO_MAJORS, Ctx, Output, Spec, _weights_output, every, get_spec, last_of_period,
                         on_change)
from lab.engine import Costs
from lab.ideas_fable import _gold_crypto


def banded(w: pd.DataFrame, flips: np.ndarray, check: np.ndarray, band: float, step: float = 1.0) -> pd.DataFrame:
    """
    Objetivos con banda: en un cambio de señal se va completo al peso nuevo; en los días de
    revisión, si algún peso se alejó más de `band` del último aplicado, se mueve `step` del camino
    (1 = completo, 0.5 = medio camino).
    """
    t = pd.DataFrame(np.nan, index=w.index, columns=w.columns)
    last = None
    for i in range(len(w)):
        row = w.iloc[i].to_numpy()
        if last is None or flips[i]:
            new = row
        elif check[i] and np.max(np.abs(row - last)) > band:
            new = last + step * (row - last)
        else:
            continue
        t.iloc[i] = new
        last = new
    return t


def _vt_weights(px: pd.DataFrame, sma: int, target_vol: float, vol_window: int = 30, ewma: float = 0.0):
    n = len(px.columns)
    r = px.pct_change()
    if ewma:
        rv = r.pow(2).ewm(alpha=1 - ewma, adjust=False, min_periods=vol_window).mean().pow(0.5) * np.sqrt(365)
    else:
        rv = r.rolling(vol_window).std() * np.sqrt(365)
    trend = (px > px.rolling(sma).mean()).astype(float)
    w = (trend * (target_vol / n / rv).clip(upper=1.0 / n)).fillna(0.0)
    flips = (trend.diff().abs().sum(axis=1) > 0).to_numpy()
    return w, flips


# ── N1. Banda con volatilidad EWMA (Fable) ──

def s_vt_ewma(ctx: Ctx, lam=0.94, band=0.10, sma=100, target_vol=0.25) -> Output:
    """
    La tendencia BTC+ETH con banda, pero la volatilidad se estima con un promedio exponencial
    (λ = `lam`): no salta cuando un día extremo entra o sale de la ventana de 30 días, y así el
    tamaño cambia menos sin cambiar la señal.
    """
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    w, flips = _vt_weights(px, sma, target_vol, ewma=lam)
    t = banded(w, flips, every(w, "W-MON").to_numpy(), band)
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── N2. Banda con ajuste a medio camino (Fable; Gârleanu y Pedersen) ──

def s_vt_medio(ctx: Ctx, step=0.5, band=0.10, sma=100, target_vol=0.25) -> Output:
    """
    La tendencia BTC+ETH con banda, pero cuando la banda dispara sólo se mueve `step` del camino
    hacia el peso objetivo (con costos, lo óptimo es acercarse, no saltar). En un cambio de
    tendencia sí se va completo.
    """
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    w, flips = _vt_weights(px, sma, target_vol)
    t = banded(w, flips, every(w, "W-MON").to_numpy(), band, step=step)
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── Mía. BTC + oro con banda ──

def s_btc_oro_banda(ctx: Ctx, band=0.10, sma=100, target_vol=0.25) -> Output:
    """
    BTC y oro en tendencia con volatilidad controlada (fab_btc_oro), con la banda de la tendencia
    BTC+ETH: BTC + oro rebalancea 38 veces al año y en Kraken eso le quita ~5 puntos.
    """
    btc = ctx.crypto_close(["BTC/USDT"]).dropna()
    px = btc.copy()
    px["PAXG/USDT"] = _gold_crypto(ctx, btc.index)
    px = px.dropna()
    w, flips = _vt_weights(px, sma, target_vol)
    t = banded(w, flips, every(w, "W-MON").to_numpy(), band)
    costs = {"BTC/USDT": ctx.cost("binance_spot"), "PAXG/USDT": ctx.cost("altcoin_spot")}
    out = _weights_output(ctx, px, t, costs, "BTC/USDT", min_trade=0.02)
    out.extra["aviso"] = "Antes de 2020 el oro es GLD (Yahoo) como sustituto de PAXG."
    return out


# ── A4. BTC por votación de plazos (corregida por Fable: sin objetivo de volatilidad) ──

def s_btc_votos(ctx: Ctx, horizons=(30, 91, 182, 365)) -> Output:
    """
    Cada lunes, la exposición a BTC es la fracción de plazos (1, 3, 6 y 12 meses) con rendimiento
    positivo: 0, 1/4, 1/2, 3/4 o todo. Sólo se opera cuando cambian los votos.
    """
    px = ctx.crypto_close(["BTC/USDT"]).dropna()
    c = px["BTC/USDT"]
    votes = sum((c / c.shift(d) - 1 > 0).astype(float) for d in horizons) / len(horizons)
    votes = votes.where(c.shift(max(horizons)).notna(), 0.0)
    mondays = every(px, "W-MON").to_numpy()
    weekly = votes.where(mondays).ffill().fillna(0.0)
    t = on_change(weekly.to_frame("BTC/USDT"))
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


# ── A5. Paridad de riesgo SPY / IEF / GLD (corregida por Fable; de libro, Qian 2005) ──

def s_paridad(ctx: Ctx, vol_window=60, band=0.10, max_months=12, assets=("SPY", "IEF", "GLD")) -> Output:
    """
    Cada activo aporta el mismo riesgo: pesos proporcionales a 1 / volatilidad de `vol_window`
    días, decididos al cierre del último día de cada mes. Se rebalancea si algún peso difiere más
    de `band` del último objetivo aplicado o si pasaron `max_months` meses sin rebalancear.
    """
    px = ctx.etf_close(list(assets)).dropna()
    rv = px.pct_change().rolling(vol_window).std()
    inv = 1.0 / rv
    w = inv.div(inv.sum(axis=1), axis=0)
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    last, months = None, 0
    for ts in px.index[last_of_period(px.index, "ME").to_numpy()]:
        row = w.loc[ts]
        if row.isna().any():
            continue
        months += 1
        if last is None or (row - last).abs().max() > band or months >= max_months:
            t.loc[ts] = row
            last, months = row, 0
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), "SPY", cash=ctx.cash_for(px.index))


# ── C3. Freno por caída de la bolsa (corregida por Fable: salida a la mitad del umbral) ──

def s_freno_bolsa(ctx: Ctx, threshold=0.25, salida="maximo", sma=200, asset="BTC/USDT") -> Output:
    """
    La tendencia BTC de libro, a media exposición mientras su propia curva SIN freno (una sombra,
    conocida al cierre de cada día) esté más de `threshold` abajo de su máximo. Vuelve a completa
    cuando la sombra hace un máximo nuevo (salida="maximo") o cuando su caída es menor que la
    mitad del umbral (salida="mitad").
    """
    px = ctx.crypto_close([asset]).dropna()
    c = px[asset]
    ma = c.rolling(sma).mean()
    target = (c > ma).astype(float).where(ma.notna(), 0.0)
    shadow = get_spec("trend_btc").run(ctx, sma=sma, asset=asset).equity.reindex(px.index).ffill()
    dd = (shadow / shadow.cummax() - 1).to_numpy()
    exit_dd = 0.0 if salida == "maximo" else -threshold / 2
    braked = np.zeros(len(px), dtype=bool)
    on = False
    for i, d in enumerate(dd):
        if np.isnan(d):
            continue
        if not on and d < -threshold:
            on = True
        elif on and d >= exit_dd:
            on = False
        braked[i] = on
    t = on_change((target * np.where(braked, 0.5, 1.0)).to_frame(asset))
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), asset)


# ── C1. Combinado sin sesgo de selección (corregida por Fable: pool completo, costo por pieza) ──

def wf_pool() -> List[str]:
    """
    Todas las estrategias de libro de tipo portafolio que se pueden operar sin perpetuos: sin
    inventadas, sin variantes de ejecución, sin combinados ni ideas nuevas. Incluye las que hoy
    salen en amarillo o rojo: el walk-forward decide, no la lista.
    """
    from lab.catalog import CATALOG
    skip = ("combo", "sec_", "fab_", "cla_", "eje_")
    return [s.key for s in CATALOG
            if s.tipo == "weights" and s.run is not None and not s.inventada and not s.ejecucion
            and not s.key.startswith(skip)]


def _piece_cost(ctx: Ctx, out: Output, key: str) -> Costs:
    if any("/" in str(u) for u in (out.extra.get("universe") or [])):
        return ctx.cost("binance_spot")
    if any(k in key for k in ("paises", "latam", "emergentes")):
        return ctx.cost("intl_etf")
    return ctx.cost("us_etf")


def _cagr(r: pd.Series) -> float:
    years = (r.index[-1] - r.index[0]).days / 365.25 if len(r) > 1 else 0
    return float((1 + r).prod() ** (1 / years) - 1) if years > 0 else float("nan")


def _passes(r: pd.Series, r2: pd.Series, cash: pd.Series, oos_fraction=0.4, min_years=3.0) -> bool:
    """Semáforo simplificado con datos hasta la fecha: 3+ años, le gana al efectivo, fuera de muestra y con costos x2."""
    if len(r) < 2 or (r.index[-1] - r.index[0]).days / 365.25 < min_years:
        return False
    cash = cash.reindex(r.index).fillna(0.0)
    split = r.index[int(len(r) * (1 - oos_fraction))]
    return bool(_cagr(r) > _cagr(cash) and _cagr(r[r.index >= split]) > _cagr(cash[cash.index >= split])
                and _cagr(r2.reindex(r.index).fillna(0.0)) > _cagr(cash))


def s_combo_wf(ctx: Ctx, vol_window=252, first_year=2019) -> Output:
    """
    Cada 1 de enero, entran las estrategias del pool que pasan el semáforo simplificado con datos
    SÓLO hasta esa fecha. Entre las que entran, capital según el inverso de su volatilidad de un
    año, rebalanceo mensual; mover capital entre piezas cuesta lo que cuesta en el lugar de cada
    pieza. Sin las pruebas de "ejecutar tarde" ni de vecinos, por costo de cómputo.
    """
    pool = wf_pool()
    outs, outs2 = {}, {}
    for k in pool:
        spec = get_spec(k)
        try:
            o = spec.run(ctx, **spec.params)
        except Exception:
            continue
        if o.extra.get("perp_cols"):
            continue
        outs[k] = o
        outs2[k] = spec.run(ctx.child(cost_mult=ctx.cost_mult * 2.0), **spec.params)
    keys = list(outs)
    end = min(o.returns.index[-1] for o in outs.values())
    start_all = min(o.returns.index[0] for o in outs.values())
    full_cal = pd.date_range(start_all.normalize(), end, freq="D", tz="UTC")
    cal = full_cal[full_cal >= pd.Timestamp(f"{first_year}-01-01", tz="UTC")]

    def daily(r: pd.Series) -> pd.Series:
        r = r.copy()
        r.index = pd.DatetimeIndex(r.index).normalize()
        return r.groupby(level=0).apply(lambda x: (1 + x).prod() - 1).reindex(full_cal).fillna(0.0)
    rets = pd.DataFrame({k: daily(o.returns) for k, o in outs.items()})
    vol = rets.rolling(vol_window, min_periods=60).std().replace(0, np.nan)

    chosen: Dict[int, List[str]] = {}
    for year in range(first_year, end.year + 1):
        cut = pd.Timestamp(f"{year}-01-01", tz="UTC")
        picks = []
        for k, o in outs.items():
            cash = o.cash if o.cash is not None else ctx.cash_for(o.returns.index)
            if _passes(o.returns[o.returns.index < cut], outs2[k].returns[outs2[k].returns.index < cut],
                       cash[cash.index < cut]):
                picks.append(k)
        chosen[year] = picks

    curves = (1 + rets.reindex(cal).fillna(0.0)).cumprod()
    t = pd.DataFrame(np.nan, index=cal, columns=keys)
    for ts in cal[every(t, "MS").to_numpy()]:
        picks = chosen.get(ts.year, [])
        w = pd.Series(0.0, index=keys)
        if picks:
            inv = (1 / vol.loc[ts, picks]).replace([np.inf, -np.inf], np.nan).dropna()
            if len(inv):
                w[inv.index] = inv / inv.sum()
        t.loc[ts] = w
    costs = {k: _piece_cost(ctx, outs[k], k) for k in keys}
    out = _weights_output(ctx, curves, t, costs, keys[0], bench_name="", min_trade=0.02, cash=ctx.cash_for(cal))
    spy = ctx.etf_close(["SPY"])["SPY"].reindex(cal).ffill()
    out.benchmark = (spy / spy.dropna().iloc[0]).dropna()
    out.benchmark_name = "Comprar y aguantar SPY"
    last_w = t.dropna(how="all").iloc[-1]
    target: Dict[str, float] = {}
    for k, o in outs.items():
        for sym, wv in (o.extra.get("target") or {}).items():
            target[sym] = target.get(sym, 0.0) + float(last_w[k]) * wv
    out.extra.update({
        "target": {s: v for s, v in target.items() if abs(v) > 1e-9},
        "piezas_por_anio": {str(y): v for y, v in chosen.items()}, "pool": keys, "perp_cols": [],
        "universe": sorted({u for o in outs.values() for u in (o.extra.get("universe") or [])}),
        "asset_ends": {}, "sesgo_seleccion": False,
        "aviso": "Piezas elegidas cada año con datos sólo hasta esa fecha, con un semáforo simplificado "
                 "(sin ejecutar tarde ni vecinos).",
    })
    return out


IDEAS2: List[Spec] = [
    Spec("eje_vol_ewma", "Tendencia BTC+ETH con banda y vol suavizada (idea de Fable)", 3, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La versión con banda, pero midiendo la volatilidad con un promedio exponencial: el tamaño salta menos y se opera menos.",
         "Reacciona un poco más lento a un cambio real de volatilidad.", 100, "Semanal",
         s_vt_ewma, {}, {"lam": [0.90, 0.97]}, datos="BTC y ETH (Binance) desde 2017"),
    Spec("eje_medio_camino", "Tendencia BTC+ETH con banda y ajuste a medio camino (idea de Fable)", 3, "Cripto",
         "Kraken / Binance (sólo spot)",
         "La versión con banda, pero cuando hay que ajustar el tamaño sólo se mueve la mitad del camino (en un cambio de tendencia, completo).",
         "Se queda más tiempo lejos del tamaño ideal.", 100, "Semanal",
         s_vt_medio, {}, {"step": [1 / 3, 2 / 3]}, datos="BTC y ETH (Binance) desde 2017"),
    Spec("cla_btc_oro_banda", "BTC y oro con banda (idea de Claude)", 3, "Cripto y oro tokenizado", "Kraken (BTC y PAXG)",
         "BTC y oro en tendencia, pero sólo se mueve si el peso cambia más de 10 puntos o cambia la tendencia: menos comisión.",
         "Se queda más tiempo con un tamaño que ya no es el ideal; el oro tokenizado depende de Paxos.", 100, "Semanal",
         s_btc_oro_banda, {}, {"band": [0.05, 0.15]}, datos="BTC (Binance) + PAXG desde 2020 y GLD antes"),
    Spec("fab_paridad", "Paridad de riesgo acciones, bonos y oro", 1, "Acciones, bonos y oro EE.UU.", "IBKR",
         "Cada activo aporta el mismo riesgo: el que se mueve menos recibe más capital (Qian, 2005). Para la parte tranquila del portafolio.",
         "Bonos y acciones pueden caer juntos (2022); rinde poco.", 500, "Mensual",
         s_paridad, {}, {"band": [0.05, 0.15], "vol_window": [40, 120]}, datos="SPY, IEF y GLD (Yahoo) desde 2004"),
    Spec("fab_combo_wf", "Combinado sin sesgo de selección (idea de Fable)", 2, "Acciones, bonos y cripto",
         "Kraken (cripto) + IBKR (ETFs): en real no, cada cuenta tiene su efectivo",
         "Cada año entran sólo las estrategias que pasaban el semáforo con los datos de ese momento, con capital según su riesgo.",
         "Una pieza puede entrar justo antes de su peor año.", 500, "Mensual",
         s_combo_wf, {}, {"vol_window": [126, 504]}, datos="Estrategias de libro sin perpetuos; piezas elegidas cada año desde 2019"),
    Spec("fab_btc_votos", "BTC por votación de plazos (idea de Fable)", 3, "Cripto", "Kraken / Binance (sólo spot)",
         "BTC con la fracción de plazos (1, 3, 6 y 12 meses) que van al alza: la misma tendencia sin depender de un solo promedio.",
         "Se queda a medias en cambios de tendencia rápidos.", 100, "Semanal",
         s_btc_votos, {}, {"horizons": [(21, 63, 126, 252)]}, datos="BTC (Binance) desde 2017"),
    Spec("fab_freno_bolsa", "Tendencia BTC con freno por caída (idea de Fable)", 3, "Cripto", "Kraken / Binance (sólo spot)",
         "La tendencia BTC de 200 días, pero a la mitad mientras la estrategia esté más de 25% abajo de su máximo.",
         "Puede quedarse a la mitad justo en el rebote.", 100, "Diaria",
         s_freno_bolsa, {}, {"threshold": [0.20, 0.35], "salida": ["mitad"]}, datos="BTC (Binance) desde 2017"),
]

INVENTADAS2 = {"fab_combo_wf": "trend_voltarget", "fab_btc_votos": "trend_btc", "fab_freno_bolsa": "trend_btc"}
EJECUCION2 = {"eje_vol_ewma": "fab_vt_banda", "eje_medio_camino": "fab_vt_banda", "cla_btc_oro_banda": "fab_btc_oro"}

for _s in IDEAS2:
    if _s.key in INVENTADAS2:
        _s.inventada, _s.base = True, INVENTADAS2[_s.key]
    elif _s.key in EJECUCION2:
        _s.ejecucion, _s.base = True, EJECUCION2[_s.key]
