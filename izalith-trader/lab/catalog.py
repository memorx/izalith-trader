"""
Izalith Trading System - Catálogo de estrategias

Cada estrategia trae sus parámetros "de libro" (los que publica quien la propuso o
los que usa todo el mundo), no los que mejor salen en el backtest. Optimizar sobre
el mismo historial que se evalúa es la forma más fácil de engañarse.

Niveles de riesgo:
  1 Conservadora  - poca caída esperada, rendimiento cercano o algo arriba del efectivo
  2 Moderada      - caídas de 15-30% posibles
  3 Agresiva      - caídas de 40% o más posibles, apalancamiento o activos chicos
  4 Especulativa  - sin historial suficiente o mercados raros; sólo en investigación
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from lab import data as D
from lab.engine import COSTS, Costs, backtest_weights, backtest_signals, backtest_dca, signal_trades_equity
from lab.metrics import trade_stats

NIVELES = {1: "Conservadora", 2: "Moderada", 3: "Agresiva", 4: "Especulativa"}

CRYPTO_MAJORS = ["BTC/USDT", "ETH/USDT"]
CRYPTO_UNIVERSE = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
    "TRX/USDT", "LTC/USDT", "LINK/USDT", "BCH/USDT", "DOT/USDT", "AVAX/USDT", "XLM/USDT",
    "ETC/USDT", "ATOM/USDT", "UNI/USDT", "FIL/USDT", "NEAR/USDT", "AAVE/USDT",
]


def perp_of(symbol: str) -> str:
    return symbol.replace("/", "")


# ══════════ Contexto de datos ══════════

class Ctx:
    """Carga datos una vez y los reparte. Permite multiplicar costos para pruebas de robustez."""

    def __init__(self, cost_mult: float = 1.0, lag: int = 1, crypto_since: str = "2017-08-01",
                 etf_since: str = "1995-01-01", refresh: bool = True,
                 venue: Optional[Dict[str, Costs]] = None, capital_usd: Optional[float] = None,
                 min_fee_usd: float = 0.0, min_order_usd: float = 0.0, cash_zero: bool = False):
        self.cost_mult = cost_mult
        self.lag = lag
        self.crypto_since = crypto_since
        self.etf_since = etf_since
        self.refresh = refresh
        # Escenario de cuenta real: costos de un lugar concreto y tamaño de la cuenta
        self.venue = venue or {}
        self.capital_usd = capital_usd
        self.min_fee_usd = min_fee_usd
        self.min_order_usd = min_order_usd
        # En la cuenta real el efectivo ocioso no gana nada (Kraken 0%, IBKR 0% con menos de 10 mil)
        self.cash_zero = cash_zero
        self._mem: Dict = {}

    def child(self, **kw) -> "Ctx":
        base = {"cost_mult": self.cost_mult, "lag": self.lag, "crypto_since": self.crypto_since,
                "etf_since": self.etf_since, "refresh": False, "venue": self.venue,
                "capital_usd": self.capital_usd, "min_fee_usd": self.min_fee_usd,
                "min_order_usd": self.min_order_usd, "cash_zero": self.cash_zero}
        c = Ctx(**{**base, **kw})
        c._mem = self._mem
        return c

    def cost(self, key: str) -> Costs:
        base = self.venue.get(key, COSTS[key])
        return Costs(fee=base.fee * self.cost_mult, slippage=base.slippage * self.cost_mult)

    def account_kwargs(self) -> Dict:
        if not self.capital_usd:
            return {}
        return {"capital_usd": self.capital_usd, "min_fee_usd": self.min_fee_usd,
                "min_order_usd": self.min_order_usd}

    def _get(self, key, fn):
        if key not in self._mem:
            self._mem[key] = fn()
        return self._mem[key]

    def crypto_close(self, symbols: List[str]) -> pd.DataFrame:
        return self._get(("cc", tuple(symbols)),
                         lambda: D.load_crypto_closes(symbols, "1d", self.crypto_since, refresh=self.refresh))

    def crypto_df(self, symbol: str, timeframe: str, since: str) -> pd.DataFrame:
        return self._get(("cdf", symbol, timeframe, since),
                         lambda: D.load_crypto(symbol, timeframe, since, refresh=self.refresh))

    def etf_close(self, tickers: List[str]) -> pd.DataFrame:
        return self._get(("etf", tuple(tickers)), lambda: D.load_yf_closes(tickers, self.etf_since))

    def funding(self, perps: List[str]) -> pd.DataFrame:
        def load():
            out = {}
            for p in perps:
                try:
                    s = D.funding_daily(p)
                    if len(s):
                        out[p] = s
                except Exception as e:
                    D.logger.warning(f"Sin funding para {p}: {e}")
            return pd.DataFrame(out)
        return self._get(("fund", tuple(perps)), load)

    def cash_daily(self) -> pd.Series:
        return self._get("cash", lambda: D.tbill_daily_rate(self.etf_since))

    def cash_for(self, index: pd.DatetimeIndex) -> pd.Series:
        """Rendimiento del efectivo en USD para cada periodo del índice (de la barra anterior a ésta)."""
        if self.cash_zero:
            return pd.Series(0.0, index=index)
        daily = self.cash_daily()
        growth = (1 + daily).cumprod()
        g = growth.reindex(index, method="ffill").bfill()
        out = (g / g.shift(1) - 1).fillna(0.0)
        out.index = index
        return out


# ══════════ Ayudas ══════════

def on_change(targets: pd.DataFrame) -> pd.DataFrame:
    """Deja sólo las filas donde el objetivo cambia; las demás quedan NaN (no operar)."""
    t = targets.fillna(0.0)
    changed = (t.diff().abs().sum(axis=1) > 1e-12)
    changed.iloc[0] = True
    out = t.copy()
    out[~changed] = np.nan
    return out


def every(frame, rule: str) -> pd.Series:
    """
    Máscara de la primera barra de cada periodo `rule` (p. ej. 'W-MON' = cada lunes, 'MS' mensual).

    closed/label a la izquierda: con el default de pandas, 'W-MON' arma semanas de martes a lunes
    y la "primera barra" salía en martes.
    """
    idx = frame.index
    pos = (pd.Series(np.arange(len(idx)), index=idx)
           .resample(rule, closed="left", label="left").first().dropna().astype(int))
    mask = np.zeros(len(idx), dtype=bool)
    mask[pos.to_numpy()] = True
    return pd.Series(mask, index=idx)


def last_of_period(index: pd.DatetimeIndex, rule: str) -> pd.Series:
    """Máscara de la última barra de cada periodo (p. ej. cierre de mes)."""
    pos = pd.Series(np.arange(len(index)), index=index).resample(rule).last().dropna().astype(int)
    mask = np.zeros(len(index), dtype=bool)
    mask[pos.to_numpy()] = True
    return pd.Series(mask, index=index)


def rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


@dataclass
class Output:
    returns: pd.Series
    equity: pd.Series
    benchmark: Optional[pd.Series] = None      # curva de comprar y aguantar
    benchmark_name: str = ""
    cash: Optional[pd.Series] = None           # rendimiento del efectivo por periodo
    turnover: Optional[pd.Series] = None
    costs: Optional[pd.Series] = None
    exposure: Optional[pd.Series] = None
    extra: Dict = field(default_factory=dict)


def _weights_output(ctx: Ctx, prices, targets, costs, bench_col, bench_name="", **kw) -> Output:
    cash = kw.pop("cash", None)
    res = backtest_weights(prices, targets, costs, cash_rate=cash, lag=ctx.lag, **ctx.account_kwargs(), **kw)
    # Última decisión de la estrategia (lo que el allocator en vivo tiene que replicar)
    decided = targets.dropna(how="all")
    target_row = decided.iloc[-1].fillna(0.0) if len(decided) else pd.Series(dtype=float)
    bench = prices[bench_col].dropna()
    bench = bench / bench.iloc[0]
    return Output(returns=res.returns, equity=res.equity, benchmark=bench,
                  benchmark_name=bench_name or f"Comprar y aguantar {bench_col}",
                  cash=cash, turnover=res.turnover, costs=res.costs,
                  exposure=res.weights.abs().sum(axis=1),
                  extra={"rebalances": res.rebalances,
                         "funding_per_year": float(res.funding.sum() / max((prices.index[-1] - prices.index[0]).days / 365.25, 1e-9)),
                         "last_weights": {k: round(float(v), 4) for k, v in res.weights.iloc[-1].items() if abs(v) > 1e-6},
                         "target": {k: float(v) for k, v in target_row.items() if abs(v) > 1e-9},
                         "target_date": decided.index[-1].isoformat() if len(decided) else None,
                         "data_end": prices.index[-1].isoformat(),
                         # Para el allocator: con qué activos decidió y hasta qué día tenía datos de cada uno
                         "universe": [str(c) for c in prices.columns],
                         "asset_ends": {str(c): prices[c].last_valid_index().isoformat()
                                        for c in prices.columns if prices[c].last_valid_index() is not None},
                         "perp_cols": list(kw.get("perp_cols") or [])})


# ══════════ Estrategias: mercados tradicionales ══════════

def s_etf_60_40(ctx: Ctx, stock="SPY", bond="IEF", w_stock=0.6) -> Output:
    px = ctx.etf_close([stock, bond]).dropna()
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    m = every(t, "MS")
    t.loc[m, stock] = w_stock
    t.loc[m, bond] = 1 - w_stock
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), stock, cash=ctx.cash_for(px.index))


def s_faber_trend(ctx: Ctx, asset="SPY", months=10) -> Output:
    """Faber (2007): estar en el índice sólo si cerró el mes arriba de su promedio de 10 meses."""
    px = ctx.etf_close([asset]).dropna()
    close = px[asset]
    month_end = last_of_period(px.index, "ME")
    monthly = close[month_end]
    sma = monthly.rolling(months).mean()
    sig = (monthly > sma).astype(float).where(sma.notna())
    target = sig.reindex(px.index).ffill().fillna(0.0)
    t = on_change(target.to_frame(asset))
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), asset, cash=ctx.cash_for(px.index))


def s_dual_momentum(ctx: Ctx, us="SPY", intl="EFA", safe="AGG", lookback=252) -> Output:
    """Antonacci (2014), Global Equity Momentum: revisión mensual."""
    px = ctx.etf_close([us, intl, safe]).dropna()
    cash = ctx.cash_for(px.index)
    cash_12m = (1 + cash).rolling(lookback).apply(np.prod, raw=True) - 1
    mom = px / px.shift(lookback) - 1
    month_end = last_of_period(px.index, "ME")
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[month_end.to_numpy()]:
        if pd.isna(mom.at[ts, us]) or pd.isna(cash_12m.at[ts]):
            continue
        row = {us: 0.0, intl: 0.0, safe: 0.0}
        if mom.at[ts, us] > cash_12m.at[ts]:
            row[us if mom.at[ts, us] >= mom.at[ts, intl] else intl] = 1.0
        else:
            row[safe] = 1.0
        t.loc[ts] = pd.Series(row)
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), us, cash=cash)


def s_rsi2(ctx: Ctx, asset="SPY", entry=10, exit_sma=5, trend_sma=200) -> Output:
    """Connors: comprar caídas cortas dentro de una tendencia alcista; salir al rebotar."""
    px = ctx.etf_close([asset]).dropna()
    c = px[asset]
    r = rsi_wilder(c, 2)
    sma_t, sma_x = c.rolling(trend_sma).mean(), c.rolling(exit_sma).mean()
    state, out = 0.0, []
    for ts in px.index:
        if state == 0 and c[ts] > sma_t[ts] and r[ts] < entry:
            state = 1.0
        elif state == 1 and c[ts] > sma_x[ts]:
            state = 0.0
        out.append(state)
    t = on_change(pd.DataFrame({asset: out}, index=px.index))
    return _weights_output(ctx, px, t, ctx.cost("us_etf"), asset, cash=ctx.cash_for(px.index))


# ══════════ Estrategias: países (Latinoamérica, Asia, África, Europa...) ══════════
#
# Todos son ETFs de país que cotizan en EE.UU.: se compran desde IBKR viviendo en México o Canadá.

PAISES = {
    # Latinoamérica
    "EWW": "México", "EWZ": "Brasil", "ECH": "Chile", "EPU": "Perú", "ARGT": "Argentina",
    # Asia
    "EWJ": "Japón", "EWY": "Corea", "EWT": "Taiwán", "INDA": "India", "MCHI": "China", "EWH": "Hong Kong",
    "EWS": "Singapur", "EWM": "Malasia", "THD": "Tailandia", "EIDO": "Indonesia", "EPHE": "Filipinas", "VNM": "Vietnam",
    # África y Medio Oriente
    "EZA": "Sudáfrica", "AFK": "África", "KSA": "Arabia Saudita", "TUR": "Turquía", "UAE": "Emiratos", "QAT": "Qatar",
    # Europa
    "EWG": "Alemania", "EWU": "Reino Unido", "EWQ": "Francia", "EWP": "España", "EWI": "Italia", "EPOL": "Polonia",
    "GREK": "Grecia",
    # Otros desarrollados
    "SPY": "EE.UU.", "EWC": "Canadá", "EWA": "Australia",
}
LATAM = ["EWW", "EWZ", "ECH", "EPU", "ARGT"]
EMERGENTES = ["EWW", "EWZ", "ECH", "EPU", "ARGT", "EWY", "EWT", "INDA", "MCHI", "EWM", "THD", "EIDO",
              "EPHE", "VNM", "EZA", "AFK", "KSA", "TUR", "UAE", "QAT", "EPOL", "GREK"]
AVISO_PAISES = ("Sesgo de supervivencia: el universo son ETFs que hoy siguen vivos; no incluye Rusia (ERUS), "
                "que quedó congelado en 2022, ni Colombia (GXG), cuyo ETF cerró.")


def _country_prices(ctx: Ctx, tickers: List[str]) -> pd.DataFrame:
    px = ctx.etf_close(tickers)
    # Las fechas de Yahoo traen días festivos distintos por país: se rellena hacia adelante dentro de cada ETF
    return px.sort_index().ffill(limit=5)


def s_paises_momentum(ctx: Ctx, top=4, lookback=252, skip=21) -> Output:
    """
    Cada mes se queda con los 4 países que más subieron en 12 meses (sin contar el último mes),
    sólo si además le ganaron al efectivo; si ninguno, efectivo. Momentum entre países:
    documentado en Asness, Moskowitz y Pedersen (2013), "Value and Momentum Everywhere".
    """
    tickers = list(PAISES)
    px = _country_prices(ctx, tickers)
    cash = ctx.cash_for(px.index)
    cash_12m = (1 + cash).rolling(lookback).apply(np.prod, raw=True) - 1
    mom = px.shift(skip) / px.shift(lookback) - 1
    month_end = last_of_period(px.index, "ME")
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[month_end.to_numpy()]:
        row = pd.Series(0.0, index=px.columns)
        m = mom.loc[ts].dropna()
        m = m[m > (cash_12m.get(ts, 0.0) or 0.0)]
        for tk in m.nlargest(top).index:
            row[tk] = 1.0 / top
        t.loc[ts] = row
    out = _weights_output(ctx, px, t, ctx.cost("intl_etf"), "SPY", bench_name="Comprar y aguantar SPY", cash=cash)
    out.extra["aviso"] = AVISO_PAISES
    return out


def s_paises_tendencia(ctx: Ctx, months=10, universe="emergentes") -> Output:
    """Partes iguales en cada país emergente, pero cada uno sólo cuando está arriba de su promedio de 10 meses."""
    tickers = EMERGENTES if universe == "emergentes" else LATAM
    px = _country_prices(ctx, tickers)
    cash = ctx.cash_for(px.index)
    month_end = last_of_period(px.index, "ME")
    monthly = px[month_end.to_numpy()]
    above = (monthly > monthly.rolling(months).mean()) & monthly.notna()
    alive = monthly.notna().sum(axis=1).replace(0, np.nan)
    w_month = above.astype(float).div(alive, axis=0).fillna(0.0)
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    t.loc[w_month.index] = w_month
    bench = "EWW" if universe == "latam" else "EWZ"
    out = _weights_output(ctx, px, t, ctx.cost("intl_etf"), bench, cash=cash, min_trade=0.01)
    out.extra["aviso"] = AVISO_PAISES
    return out


def s_latam_tendencia(ctx: Ctx, months=10) -> Output:
    """Lo mismo, sólo con Latinoamérica: México, Brasil, Chile, Perú y Argentina (el ETF de Colombia cerró)."""
    return s_paises_tendencia(ctx, months=months, universe="latam")


def s_emergentes_iguales(ctx: Ctx) -> Output:
    """Comprar el mundo emergente en partes iguales y rebalancear cada trimestre: la referencia sin estrategia."""
    px = _country_prices(ctx, EMERGENTES)
    cash = ctx.cash_for(px.index)
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    q = every(t, "QS").to_numpy()
    alive = px.notna()
    w = alive.astype(float).div(alive.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    t[q] = w[q]
    out = _weights_output(ctx, px, t, ctx.cost("intl_etf"), "EWZ", cash=cash, min_trade=0.01)
    out.extra["aviso"] = AVISO_PAISES
    return out


# ══════════ Estrategias: cripto direccional ══════════

def s_cash_plus_btc(ctx: Ctx, btc_weight=0.10) -> Output:
    """90% en efectivo que paga tasa (T-bill o CETES) y 10% en BTC, rebalanceo trimestral."""
    px = ctx.crypto_close(["BTC/USDT"]).dropna()
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    t.loc[every(t, "QS"), "BTC/USDT"] = btc_weight
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", cash=ctx.cash_for(px.index))


def s_trend_btc(ctx: Ctx, sma=200, asset="BTC/USDT") -> Output:
    px = ctx.crypto_close([asset]).dropna()
    c = px[asset]
    ma = c.rolling(sma).mean()
    target = (c > ma).astype(float).where(ma.notna(), 0.0)
    t = on_change(target.to_frame(asset))
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), asset)


def s_trend_voltarget(ctx: Ctx, sma=100, target_vol=0.25, vol_window=30) -> Output:
    """BTC y ETH en tendencia, con tamaño ajustado para que el portafolio apunte a 25% de volatilidad anual."""
    px = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    n = len(px.columns)
    rv = px.pct_change().rolling(vol_window).std() * np.sqrt(365)
    trend = (px > px.rolling(sma).mean()).astype(float)
    # Suponiendo correlación alta entre BTC y ETH, la volatilidad del portafolio ~ suma de w_i * vol_i
    w = (trend * (target_vol / n / rv).clip(upper=1.0 / n)).fillna(0.0)
    flips = trend.diff().abs().sum(axis=1) > 0
    weekly = every(w, "W-MON")
    t = w.copy()
    t[~(flips | weekly)] = np.nan
    return _weights_output(ctx, px, t, ctx.cost("binance_spot"), "BTC/USDT", min_trade=0.02)


def s_xs_momentum(ctx: Ctx, lookback=28, top=3, regime_sma=100) -> Output:
    """Rotación semanal a las 3 monedas que más subieron en 4 semanas, sólo si BTC está en tendencia."""
    px = ctx.crypto_close(CRYPTO_UNIVERSE)
    btc = px["BTC/USDT"]
    regime = btc > btc.rolling(regime_sma).mean()
    mom = px / px.shift(lookback) - 1
    history_ok = px.notna().rolling(60).sum() >= 60
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    for ts in px.index[every(t, "W-MON").to_numpy()]:
        row = pd.Series(0.0, index=px.columns)
        if regime.get(ts, False):
            cand = mom.loc[ts][history_ok.loc[ts]].dropna()
            for sym in cand.nlargest(top).index:
                row[sym] = 1.0 / top
        t.loc[ts] = row
    out = _weights_output(ctx, px, t, ctx.cost("altcoin_spot"), "BTC/USDT")
    out.extra["aviso"] = "Sesgo de supervivencia: el universo son monedas que hoy siguen listadas."
    return out


def s_btc_leveraged_trend(ctx: Ctx, sma=200, leverage=2.0) -> Output:
    """BTC con apalancamiento 2x en perpetuo cuando está en tendencia; paga funding."""
    spot = ctx.crypto_close(["BTC/USDT"]).dropna()
    fund = ctx.funding(["BTCUSDT"])
    start = fund.index[0]
    px = spot[spot.index >= start].rename(columns={"BTC/USDT": "BTCUSDT"})
    c = px["BTCUSDT"]
    ma = spot["BTC/USDT"].rolling(sma).mean().reindex(px.index)
    target = (c > ma).astype(float) * leverage
    flips = target.diff().abs() > 0
    weekly = every(px, "W-MON")
    t = target.to_frame("BTCUSDT")
    t[~(flips | weekly)] = np.nan
    return _weights_output(ctx, px, t, ctx.cost("binance_perp"), "BTCUSDT",
                           bench_name="Comprar y aguantar BTC", funding=fund, perp_cols=["BTCUSDT"], min_trade=0.05)


def s_donchian_ls(ctx: Ctx, entry=55, exit=20, size=0.5) -> Output:
    """Tortugas sobre perpetuos de BTC y ETH: largo al romper máximos de 55 días, corto al romper mínimos."""
    spot = ctx.crypto_close(CRYPTO_MAJORS).dropna()
    perps = [perp_of(s) for s in CRYPTO_MAJORS]
    fund = ctx.funding(perps)
    px = spot[spot.index >= fund.index[0]].copy()
    px.columns = perps
    t = pd.DataFrame(0.0, index=px.index, columns=perps)
    for col in perps:
        c = px[col]
        hi_e, lo_e = c.shift(1).rolling(entry).max(), c.shift(1).rolling(entry).min()
        hi_x, lo_x = c.shift(1).rolling(exit).max(), c.shift(1).rolling(exit).min()
        state, vals = 0, []
        for ts in px.index:
            if state == 0:
                if c[ts] > hi_e[ts]:
                    state = 1
                elif c[ts] < lo_e[ts]:
                    state = -1
            elif state == 1 and c[ts] < lo_x[ts]:
                state = -1 if c[ts] < lo_e[ts] else 0
            elif state == -1 and c[ts] > hi_x[ts]:
                state = 1 if c[ts] > hi_e[ts] else 0
            vals.append(state * size)
        t[col] = vals
    return _weights_output(ctx, px, on_change(t), ctx.cost("binance_perp"), "BTCUSDT",
                           bench_name="Comprar y aguantar BTC", funding=fund, perp_cols=perps)


# ══════════ Estrategias: cripto neutral (carry) ══════════

def _carry_frame(ctx: Ctx, spot_syms: List[str]):
    perps = [perp_of(s) for s in spot_syms]
    fund = ctx.funding(perps)
    spot = ctx.crypto_close(spot_syms)
    start = fund.dropna(how="all").index[0]
    spot = spot[spot.index >= start]
    perp_px = spot.copy()
    perp_px.columns = perps                     # el perpetuo sigue al spot (se ignora la base)
    px = pd.concat([spot, perp_px], axis=1)
    return px, fund.reindex(px.index), perps


def s_carry_majors(ctx: Ctx, notional=0.5, lookback=7) -> Output:
    """
    Cash-and-carry en BTC y ETH: comprar el spot y vender el perpetuo por el mismo monto.
    El precio se cancela y queda el funding. Sólo se arma cuando el funding de la última
    semana fue positivo. 50% del capital en spot y 50% como margen del corto (apalancamiento 1x).
    """
    px, fund, perps = _carry_frame(ctx, CRYPTO_MAJORS)
    trail = fund.rolling(lookback, min_periods=lookback).mean()
    per = notional / len(CRYPTO_MAJORS)
    t = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    for s, p in zip(CRYPTO_MAJORS, perps):
        on = (trail[p] > 0).astype(float)
        t[s] = on * per
        t[p] = -on * per
    flips = t.diff().abs().sum(axis=1) > 0
    t[~(flips | every(t, "W-MON"))] = np.nan
    costs = {**{s: ctx.cost("binance_spot") for s in CRYPTO_MAJORS}, **{p: ctx.cost("binance_perp") for p in perps}}
    out = _weights_output(ctx, px, t, costs, "BTC/USDT", funding=fund, perp_cols=perps, min_trade=0.03)
    return out


def s_carry_rotation(ctx: Ctx, top=5, lookback=7, notional=0.5, min_apr=0.10) -> Output:
    """Lo mismo, pero cada semana rota a las 5 monedas con funding más alto (mínimo 10% anual)."""
    syms = [s for s in CRYPTO_UNIVERSE]
    px, fund, perps = _carry_frame(ctx, syms)
    trail = fund.rolling(lookback, min_periods=lookback).mean()
    t = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    per = notional / top
    held: List[str] = []
    for ts in px.index[every(t, "W-MON").to_numpy()]:
        f = trail.loc[ts].dropna() * 365
        # Se conserva lo que sigue pagando al menos la mitad del mínimo: rotar cuesta 4 comisiones
        held = [p for p in held if f.get(p, -1) >= min_apr / 2]
        for p in f[f >= min_apr].sort_values(ascending=False).index:
            if len(held) >= top:
                break
            if p not in held and not pd.isna(px.at[ts, syms[perps.index(p)]]):
                held.append(p)
        row = pd.Series(0.0, index=px.columns)
        for p in held:
            row[syms[perps.index(p)]] = per
            row[p] = -per
        t.loc[ts] = row
    costs = {**{s: ctx.cost("altcoin_spot") for s in syms}, **{p: ctx.cost("altcoin_perp") for p in perps}}
    out = _weights_output(ctx, px, t, costs, "BTC/USDT", funding=fund, perp_cols=perps, min_trade=0.03)
    out.extra["aviso"] = "Sesgo de supervivencia en el universo; se ignora la diferencia de precio spot-perpetuo."
    return out


# ══════════ Estrategias del bot actual (intradía) ══════════

def _scalper(ctx: Ctx, name: str, symbol: str, days: int = 730, allow_short: bool = False) -> Output:
    from modules.scalper.executor import ScalperExecutor
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    df = ctx.crypto_df(symbol, "1h", since)
    df = df[df.index >= since]
    strat = ScalperExecutor.STRATEGY_MAP[name](0.5, 0.3)
    trades = backtest_signals(strat, df, symbol, ctx.cost("binance_spot"), allow_short=allow_short)
    # Las métricas se miden en días, igual que las demás estrategias
    eq = signal_trades_equity(trades, df.index).resample("1D").last().ffill()
    rets = eq.pct_change().fillna(0.0)
    bench = df["close"].resample("1D").last().ffill()
    bench = bench / bench.iloc[0]
    stats = trade_stats([t.net for t in trades])
    stats["gross_expectancy_pct"] = float(np.mean([t.gross for t in trades]) * 100) if trades else 0.0
    return Output(returns=rets, equity=eq, benchmark=bench, benchmark_name=f"Comprar y aguantar {symbol}",
                  extra={"trade_stats": stats})


def s_scalper_momentum(ctx: Ctx, symbol="BTC/USDT") -> Output:
    return _scalper(ctx, "momentum", symbol)


def s_scalper_mean_reversion(ctx: Ctx, symbol="BTC/USDT") -> Output:
    return _scalper(ctx, "mean_reversion", symbol)


def s_scalper_breakout(ctx: Ctx, symbol="BTC/USDT") -> Output:
    return _scalper(ctx, "breakout", symbol)


# ══════════ DCA ══════════

def s_dca(ctx: Ctx, asset="SPY", amount=100.0, freq="W-MON") -> Output:
    if "/" in asset:
        px = ctx.crypto_close([asset])[asset].dropna()
        costs = ctx.cost("binance_spot")
    else:
        px = ctx.etf_close([asset])[asset].dropna()
        costs = ctx.cost("us_etf")
    res = backtest_dca(px, amount, freq, costs)
    # Rendimiento del valor de la cuenta descontando aportaciones (rendimiento ponderado por tiempo)
    flows = res.invested.diff().fillna(res.invested.iloc[0])
    prev = res.value.shift(1).fillna(0.0)
    rets = ((res.value - flows) / prev.replace(0, np.nan) - 1).fillna(0.0)
    return Output(returns=rets, equity=(1 + rets).cumprod(), benchmark=px / px.iloc[0],
                  benchmark_name=f"Comprar y aguantar {asset}",
                  extra={"irr": res.irr, "final_multiple": res.final_multiple,
                         "invested": float(res.invested.iloc[-1]), "final_value": float(res.value.iloc[-1])})


# ══════════ Ficha de cada estrategia ══════════

@dataclass
class Spec:
    key: str
    nombre: str
    nivel: int
    mercado: str
    donde: str                 # dónde se opera
    idea: str                  # cómo gana dinero, en una frase
    riesgos: str
    capital_min_usd: int
    frecuencia: str
    run: Optional[Callable] = None
    params: Dict = field(default_factory=dict)
    grid: Dict[str, List] = field(default_factory=dict)  # vecinos para la prueba de robustez
    tipo: str = "weights"      # weights | signals | dca | investigacion
    datos: str = ""            # qué datos usa el backtest
    inventada: bool = False    # inventada aquí: además tiene que pasar la prueba de "no fue suerte"
    ejecucion: bool = False    # misma señal que su base, distinta forma de operarla (ver lab/review.py)
    base: str = ""             # contra qué se mide la idea: clave del catálogo, "benchmark" o "cash"


CATALOG: List[Spec] = [
    # ── Nivel 1 ──
    Spec("etf_60_40", "Portafolio 60/40", 1, "Acciones y bonos EE.UU.",
         "IBKR, Questrade o Wealthsimple (en Canadá: VFV/XBB); GBM vía SIC en México",
         "60% índice S&P 500 y 40% bonos del Tesoro a 7-10 años, rebalanceo mensual: cobra el crecimiento de la economía con menos caída.",
         "Años como 2022 en que bajan acciones y bonos a la vez.", 500, "Mensual",
         s_etf_60_40, {}, {"w_stock": [0.4, 0.5, 0.7, 0.8]}, datos="SPY + IEF (Yahoo), desde 2002"),
    Spec("cash_plus_btc", "Efectivo con tasa + 10% BTC", 1, "Efectivo + cripto",
         "CETES Directo o cuenta con interés + Bitso/Binance/Kraken",
         "El 90% gana la tasa libre de riesgo y el 10% en BTC da la subida; si BTC se va a cero se pierde el 10%.",
         "BTC puede caer 75%: el portafolio cae ~8%.", 100, "Trimestral",
         s_cash_plus_btc, {}, {"btc_weight": [0.05, 0.15, 0.20]}, datos="BTC/USDT (Binance) + T-bill"),
    Spec("carry_majors", "Carry de funding BTC/ETH", 1, "Cripto neutral (spot + perpetuo)",
         "Binance, Bybit u OKX (spot y perpetuos en la misma cuenta); Hyperliquid para el corto",
         "Compra el spot y vende el perpetuo: el precio se cancela y cobra el funding que pagan los largos apalancados.",
         "Quiebra o congelamiento del exchange, funding negativo largo, liquidación del corto si no se rebalancea el margen.",
         1000, "Semanal", s_carry_majors, {}, {"lookback": [3, 14, 30]}, datos="BTC/ETH spot + funding Binance desde 2019"),
    Spec("dca_spy", "DCA semanal al S&P 500", 1, "Acciones EE.UU.",
         "GBM+/Hapi (México), Wealthsimple/Questrade (Canadá), IBKR",
         "Compra lo mismo cada semana sin intentar adivinar; no busca ganarle al mercado sino no quedarse fuera.",
         "Caídas de 50% del índice (2008) a mitad del camino.", 50, "Semanal",
         s_dca, {"asset": "SPY"}, {}, tipo="dca", datos="SPY (Yahoo)"),

    # ── Nivel 2 ──
    Spec("faber_trend", "Tendencia S&P 500 (Faber)", 2, "Acciones EE.UU.",
         "IBKR / Questrade (con API) o a mano en cualquier broker",
         "Está en el índice sólo cuando cerró el mes arriba de su promedio de 10 meses; si no, efectivo con tasa. Esquiva las caídas largas.",
         "Mercados de lado con señales falsas; se queda fuera en rebotes rápidos.", 500, "Mensual",
         s_faber_trend, {}, {"months": [6, 8, 12, 14]}, datos="SPY + T-bill desde 1995"),
    Spec("dual_momentum", "Momentum dual (Antonacci)", 2, "Acciones EE.UU./mundo y bonos",
         "IBKR / Questrade / Wealthsimple",
         "Cada mes se queda con lo que más subió en 12 meses entre EE.UU. y el resto del mundo, o se pasa a bonos si ninguno le gana al efectivo.",
         "Cambios de régimen bruscos (2020) lo agarran tarde.", 500, "Mensual",
         s_dual_momentum, {}, {"lookback": [126, 189, 315]}, datos="SPY, EFA, AGG desde 2004"),
    Spec("rsi2_spy", "Rebote corto en S&P 500 (RSI-2)", 2, "Acciones EE.UU.",
         "IBKR (API) o Alpaca", "Compra caídas de 2-3 días dentro de una tendencia alcista y vende al primer rebote.",
         "Caídas que no rebotan (crash); muchas operaciones, el costo importa.", 500, "Diaria",
         s_rsi2, {}, {"entry": [5, 15, 20], "exit_sma": [3, 10]}, datos="SPY desde 1995"),
    Spec("trend_btc", "Tendencia BTC (promedio de 200 días)", 2, "Cripto",
         "Bitso, Binance, Kraken o Coinbase", "Tiene BTC sólo cuando está arriba de su promedio de 200 días; sale en los mercados bajistas.",
         "Caídas de 30% antes de que salte la salida; muchas señales falsas en mercados de lado.", 100, "Diaria",
         s_trend_btc, {}, {"sma": [50, 100, 150, 250]}, datos="BTC/USDT Binance desde 2017"),
    Spec("trend_voltarget", "Tendencia BTC+ETH con volatilidad controlada", 2, "Cripto",
         "Binance / Kraken / Bybit", "Igual que la tendencia, pero invierte menos cuando el mercado está nervioso para apuntar a 25% de volatilidad anual.",
         "Rinde menos en las subidas fuertes; sigue cayendo en los desplomes de un día.", 300, "Semanal",
         s_trend_voltarget, {}, {"sma": [50, 150, 200], "target_vol": [0.15, 0.35]}, datos="BTC, ETH desde 2017"),
    Spec("carry_rotation", "Carry de funding en altcoins", 2, "Cripto neutral",
         "Binance / Bybit (perpetuos de altcoins)", "Igual que el carry de BTC/ETH, pero rota cada semana a las 5 monedas con funding más alto.",
         "Altcoins que suben 100% en un día (margen del corto), deslistados, la diferencia spot-perpetuo, menos liquidez.",
         2000, "Semanal", s_carry_rotation, {}, {"top": [3, 8], "min_apr": [0.0, 0.2]}, datos="20 altcoins + funding desde 2019"),

    # ── Nivel 3 ──
    Spec("xs_momentum", "Rotación a las cripto más fuertes", 3, "Cripto (altcoins)",
         "Binance / Kraken", "Cada lunes compra las 3 monedas que más subieron en 4 semanas, sólo si BTC está en tendencia.",
         "Caídas de 60% en altcoins, reversiones bruscas, sesgo de supervivencia en el backtest.", 500, "Semanal",
         s_xs_momentum, {}, {"lookback": [14, 56], "top": [2, 5]}, datos="20 monedas Binance desde 2017"),
    Spec("donchian_ls", "Tortugas en perpetuos BTC/ETH (largo y corto)", 3, "Cripto perpetuos",
         "Binance / Bybit / Hyperliquid", "Se sube a las rupturas de máximos o mínimos de 55 días, a favor de la tendencia, y también gana en las bajadas.",
         "Muchas pérdidas chicas seguidas; funding en contra; riesgo de liquidación si se apalanca.", 500, "Diaria",
         s_donchian_ls, {}, {"entry": [20, 100], "exit": [10, 40]}, datos="BTC, ETH + funding desde 2019"),
    Spec("btc_2x_trend", "BTC 2x en tendencia", 3, "Cripto perpetuos",
         "Binance / Bybit / Hyperliquid", "La tendencia de 200 días con el doble de exposición vía perpetuo.",
         "Caídas de 50%+ antes de salir; el funding se come parte de la ganancia; liquidación con 2x en un desplome de 50% en un día.",
         300, "Diaria", s_btc_leveraged_trend, {}, {"sma": [100, 150, 250], "leverage": [1.5, 3.0]}, datos="BTC perpetuo desde 2019"),
    Spec("scalper_momentum", "Scalper momentum (bot actual)", 3, "Cripto intradía",
         "Binance", "Entra en cruces de EMA con volumen y busca +0.5% en velas de 1 hora.",
         "Las comisiones se comen el margen; depende de la ejecución.", 500, "Horaria",
         s_scalper_momentum, {}, {}, tipo="signals", datos="BTC/USDT 1h, últimos 2 años"),
    Spec("scalper_mean_reversion", "Scalper reversión a la media (bot actual)", 3, "Cripto intradía",
         "Binance", "Compra cuando el precio rompe la banda baja de Bollinger con RSI bajo.",
         "Atrapa cuchillos cayendo.", 500, "Horaria",
         s_scalper_mean_reversion, {}, {}, tipo="signals", datos="BTC/USDT 1h, últimos 2 años"),
    Spec("scalper_breakout", "Scalper ruptura de 24h (bot actual)", 3, "Cripto intradía",
         "Binance", "Compra rupturas del máximo de 24 horas con volumen.",
         "Rupturas falsas.", 500, "Horaria",
         s_scalper_breakout, {}, {}, tipo="signals", datos="BTC/USDT 1h, últimos 2 años"),
    Spec("dca_btc", "DCA semanal a BTC", 3, "Cripto",
         "Bitso / Binance / Kraken / Wealthsimple", "Compra BTC cada semana sin vender nunca.",
         "Caídas de 75-80% (2018, 2022) a mitad del camino.", 20, "Semanal",
         s_dca, {"asset": "BTC/USDT"}, {}, tipo="dca", datos="BTC/USDT desde 2017"),

    # ── Países: Latinoamérica, Asia, África, Europa ──
    Spec("emergentes_iguales", "Mundo emergente en partes iguales", 3, "Acciones de 22 países emergentes",
         "IBKR (ETFs de país en EE.UU.): desde México y Canadá",
         "Compra a todos los emergentes por igual (México, Brasil, India, Vietnam, Sudáfrica, Turquía...) y rebalancea cada trimestre.",
         "Crisis emergentes (2008, 2015, 2022) con caídas de 50%+; devaluaciones.", 1000, "Trimestral",
         s_emergentes_iguales, {}, {}, datos="22 ETFs de país (Yahoo) desde 1996"),
    Spec("paises_tendencia", "Tendencia en cada país emergente", 2, "Acciones de 22 países emergentes",
         "IBKR (ETFs de país en EE.UU.)",
         "Partes iguales en cada país emergente, pero sólo mientras está arriba de su promedio de 10 meses: sale de los países en crisis.",
         "Mercados de lado con señales falsas; muchos ETFs, más órdenes.", 2000, "Mensual",
         s_paises_tendencia, {}, {"months": [6, 8, 12, 14]}, datos="22 ETFs de país desde 1996"),
    Spec("latam_tendencia", "Tendencia en Latinoamérica", 3, "Acciones de México, Brasil, Chile, Perú y Argentina",
         "IBKR (EWW, EWZ, ECH, EPU, ARGT)",
         "La misma regla de tendencia, sólo con 5 países de Latinoamérica.",
         "Región chica y correlacionada: cuando cae una, caen todas; riesgo político y de moneda.", 500, "Mensual",
         s_latam_tendencia, {}, {"months": [6, 8, 12, 14]}, datos="5 ETFs de Latinoamérica desde 1996"),
    Spec("paises_momentum", "Rotación a los países más fuertes", 3, "Acciones de 33 países",
         "IBKR (ETFs de país en EE.UU.)",
         "Cada mes se queda con los 4 países que más subieron en un año, si le ganan al efectivo: va a donde está la fuerza (Japón, India, Argentina...).",
         "Reversiones bruscas entre países; un país puede cerrar su mercado (Rusia 2022).", 1000, "Mensual",
         s_paises_momentum, {}, {"top": [2, 6], "lookback": [126, 189]}, datos="33 ETFs de país desde 1996"),

    # ── Nivel 4: mercados que casi nadie mira (en investigación) ──
    Spec("pred_markets", "Mercados de predicción", 4, "Polymarket / Kalshi",
         "Polymarket (internacional) o Kalshi; revisar acceso por país",
         "Precios mal puestos en eventos (sesgo hacia el favorito y el perdedor), y la misma pregunta con precio distinto en dos plataformas.",
         "Liquidez chica, reglas de resolución, acceso legal según el país.", 200, "Por evento",
         tipo="investigacion", datos="Pendiente: recolector de precios de Polymarket y Kalshi"),
    Spec("funding_spread", "Diferencia de funding entre exchanges", 4, "Cripto neutral",
         "Binance + Hyperliquid / Bybit", "Largo en el perpetuo donde el funding es bajo y corto donde es alto, en la misma moneda.",
         "Capital repartido en dos lugares, riesgo de los dos exchanges.", 2000, "Diaria",
         tipo="investigacion", datos="Pendiente: recolector de funding de varios exchanges"),
    Spec("stock_perps_weekend", "Acciones en perpetuo los fines de semana", 4, "Perpetuos de acciones 24/7",
         "Binance / Hyperliquid (perpetuos de acciones)", "El perpetuo de una acción sigue cotizando cuando la bolsa está cerrada; el hueco contra la apertura del lunes puede repetirse.",
         "Producto nuevo con poco historial y funding muy alto.", 500, "Semanal",
         tipo="investigacion", datos="Pendiente: historial de perpetuos de acciones"),
    Spec("dolar_cripto_paises", "Dólar cripto por país", 4, "Cripto en monedas locales",
         "Sólo como información: aprovecharlo exige vivir en ese país",
         "En países con control de cambios (Argentina, Nigeria, Turquía...) el dólar cripto cotiza arriba del oficial; en Corea BTC tiene la 'prima kimchi'. La prima mide el estrés local.",
         "No se puede arbitrar desde fuera: controles de capital, KYC local, cuentas bancarias del país.", 0, "Informativo",
         tipo="investigacion", datos="Recolector: Bitso, Mercado Bitcoin, Buda, Upbit, BtcTurk, VALR, Luno, CoinDCX, Binance P2P"),
    Spec("mxn_premium", "Prima del peso en Bitso", 4, "Cripto / MXN",
         "Bitso + Binance", "En pánicos del peso o de cripto, BTC en pesos se despega del precio global por tipo de cambio.",
         "Hoy la prima está en ±0.05% y las comisiones de Bitso son altas; sólo sirve en días raros.", 2000, "Por evento",
         tipo="investigacion", datos="Pendiente: recolector Bitso vs Binance"),
]


def get_spec(key: str) -> Spec:
    for s in CATALOG:
        if s.key == key:
            return s
    raise KeyError(key)


# Las estrategias inventadas viven en lab/secretas.py (usan las ayudas de este archivo)
from lab.secretas import SECRETAS  # noqa: E402

CATALOG.extend(SECRETAS)

# Las ideas de la sesión con Fable del 24 oct 2026 (lab/ideas_fable.py)
from lab.ideas_fable import IDEAS  # noqa: E402

CATALOG.extend(IDEAS)

# Ronda 2 (24 oct 2026, reglas revisadas por Fable antes de correr): lab/ideas_ronda2.py
from lab.ideas_ronda2 import IDEAS2  # noqa: E402

CATALOG.extend(IDEAS2)


# ══════════ Portafolios que combinan estrategias ══════════
#
# Lo más cercano a "más constante": varias estrategias que no se mueven igual. Cuando una cae,
# otra suele aguantar. Cada pieza pesa según su riesgo (inverso de su volatilidad del último año):
# la que se mueve menos recibe más capital. Rebalanceo mensual entre piezas.

COMBO_VERDES = ["etf_60_40", "cash_plus_btc", "faber_trend", "dual_momentum", "rsi2_spy", "trend_btc", "trend_voltarget"]
COMBO_CHICO = ["trend_voltarget", "faber_trend"]   # 2 piezas: cabe en una cuenta de 200 dólares


def s_combo(ctx: Ctx, componentes="verdes", metodo="inverso_vol", vol_window=252) -> Output:
    keys = COMBO_VERDES if componentes == "verdes" else COMBO_CHICO
    outs = {k: get_spec(k).run(ctx, **get_spec(k).params) for k in keys}
    start = max(o.returns.index[0] for o in outs.values())
    end = min(o.returns.index[-1] for o in outs.values())
    cal = pd.date_range(start, end, freq="D", tz="UTC")
    # Cada pieza se trata como un activo cuyo "precio" es su curva de capital
    curves = pd.DataFrame({k: (1 + o.returns.reindex(cal).fillna(0.0)).cumprod() for k, o in outs.items()})
    rets = curves.pct_change().fillna(0.0)
    vol = rets.rolling(vol_window, min_periods=60).std().replace(0, np.nan)
    t = pd.DataFrame(np.nan, index=cal, columns=keys)
    for ts in cal[every(t, "MS").to_numpy()]:
        if metodo == "iguales" or vol.loc[ts].isna().all():
            w = pd.Series(1.0 / len(keys), index=keys)
        else:
            inv = (1 / vol.loc[ts]).fillna(0.0)
            w = inv / inv.sum()
        t.loc[ts] = w
    # Mover capital entre piezas = vender en una y comprar en otra: se cobra 0.15% por lado
    res_out = _weights_output(ctx, curves, t, Costs(fee=0.001 * ctx.cost_mult, slippage=0.0005 * ctx.cost_mult),
                              keys[0], bench_name="", min_trade=0.02)
    spy = ctx.etf_close(["SPY"])["SPY"].reindex(cal).ffill()
    res_out.benchmark = (spy / spy.dropna().iloc[0]).dropna()
    res_out.benchmark_name = "Comprar y aguantar SPY"
    res_out.cash = ctx.cash_for(cal)

    # Objetivo por activo para el allocator: suma de (peso de la pieza x objetivo de la pieza)
    last_w = t.dropna(how="all").iloc[-1]
    target: Dict[str, float] = {}
    dates = [t.dropna(how="all").index[-1].isoformat()]
    for k, o in outs.items():
        for sym, w in (o.extra.get("target") or {}).items():
            target[sym] = target.get(sym, 0.0) + float(last_w[k]) * w
        if o.extra.get("target_date"):
            dates.append(o.extra["target_date"])
    universe, ends = set(), {}
    for o in outs.values():
        universe |= set(o.extra.get("universe") or [])
        for a, e in (o.extra.get("asset_ends") or {}).items():
            ends[a] = min(ends.get(a, e), e)
    res_out.extra.update({
        "target": {s: w for s, w in target.items() if abs(w) > 1e-9},
        "target_date": max(dates),
        "universe": sorted(universe),
        "asset_ends": ends,
        "pesos_piezas": {k: round(float(v), 4) for k, v in last_w.items()},
        "perp_cols": [],
        "aviso": "Las piezas se eligieron por haber salido verdes: el resultado combinado hereda ese optimismo.",
        "sesgo_seleccion": True,
    })
    return res_out


CATALOG.extend([
    Spec("combo_verdes", "Portafolio de las 7 verdes", 2, "Acciones, bonos y cripto",
         "IBKR (ETFs) + Kraken o Binance (cripto)",
         "Junta las 7 estrategias verdes que el bot puede operar, cada una con más o menos capital según su riesgo, y rebalancea cada mes: no depende de que un solo mercado vaya bien.",
         "Si todos los mercados caen a la vez (2022) caen juntas; más piezas = más órdenes y más comisiones.", 2000, "Mensual",
         s_combo, {"componentes": "verdes"}, {"metodo": ["iguales"], "vol_window": [126, 504]},
         datos="Las 7 estrategias del catálogo, desde 2017"),
    Spec("combo_chico", "Portafolio chico: cripto + S&P 500", 2, "Cripto y acciones EE.UU.",
         "Kraken (cripto) + IBKR (SPY)",
         "Dos piezas que casi no se parecen: la tendencia BTC+ETH con volatilidad controlada y la tendencia del S&P 500, con capital según su riesgo. Pensado para empezar con poco.",
         "Las dos pueden caer juntas en un pánico general; con 200 dólares la comisión mínima de IBKR pesa.", 200, "Mensual",
         s_combo, {"componentes": "chico"}, {"metodo": ["iguales"], "vol_window": [126, 504]},
         datos="Tendencia BTC+ETH y Faber, desde 2017"),
])
