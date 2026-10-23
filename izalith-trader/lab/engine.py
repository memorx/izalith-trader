"""
Izalith Trading System - Motores de backtest

Hay tres motores, uno por tipo de estrategia:

- backtest_weights: estrategias de portafolio (tendencia, rotación, carry, asignación).
  La estrategia decide pesos objetivo con datos hasta el cierre t; esos pesos ganan
  el rendimiento de t a t+1. Nunca se usa información del futuro.
- backtest_signals: las estrategias intradía del bot (clases BaseStrategy del scalper),
  vela por vela, con entrada en la apertura de la vela siguiente y stops dentro de la vela.
- backtest_dca: compras periódicas, medidas contra el dinero aportado.

Todos cobran comisión y deslizamiento en cada operación.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


# ══════════ Costos ══════════

@dataclass
class Costs:
    """Costo de operar, en fracción del monto operado, por lado."""
    fee: float = 0.001        # comisión
    slippage: float = 0.0005  # deslizamiento + medio spread

    @property
    def per_side(self) -> float:
        return self.fee + self.slippage


# Costos de referencia por lugar (nivel de volumen más bajo, órdenes a mercado)
COSTS = {
    "binance_spot": Costs(fee=0.001, slippage=0.0005),
    "binance_perp": Costs(fee=0.0005, slippage=0.0005),
    "bitso": Costs(fee=0.0065, slippage=0.001),
    "us_etf": Costs(fee=0.0001, slippage=0.0003),     # IBKR: ~0.005 USD por acción
    "altcoin_spot": Costs(fee=0.001, slippage=0.0015),
    "altcoin_perp": Costs(fee=0.0005, slippage=0.0015),
    "intl_etf": Costs(fee=0.0001, slippage=0.0010),   # ETFs de país: spreads más anchos que SPY
}


@dataclass
class WeightsResult:
    equity: pd.Series           # valor del portafolio, empieza en 1.0
    returns: pd.Series          # rendimiento por periodo, neto
    weights: pd.DataFrame       # pesos reales al cierre de cada periodo
    turnover: pd.Series         # suma de |cambio de peso| operado en cada periodo
    costs: pd.Series            # costo pagado en cada periodo (fracción del capital)
    funding: pd.Series          # funding cobrado (+) o pagado (-)
    rebalances: int = 0
    meta: Dict = field(default_factory=dict)


def backtest_weights(
    prices: pd.DataFrame,
    targets: pd.DataFrame,
    costs: Dict[str, Costs] | Costs,
    funding: Optional[pd.DataFrame] = None,
    cash_rate: Optional[pd.Series] = None,
    lag: int = 1,
    min_trade: float = 0.0,
    perp_cols: Optional[List[str]] = None,
    borrow_spread: float = 0.0,
    capital_usd: Optional[float] = None,
    min_fee_usd: float = 0.0,
    min_order_usd: float = 0.0,
) -> WeightsResult:
    """
    Simula un portafolio que se rebalancea a pesos objetivo.

    Args:
        prices: cierres por activo (columnas). Pueden venir con NaN antes de que exista el activo.
        targets: pesos objetivo por activo y periodo. Una fila con NaN en todas las columnas
            significa "no decidir nada este periodo": se deja correr el portafolio.
            Pesos negativos = corto (perpetuos). Lo que no está invertido en largos queda en efectivo.
        costs: Costs único o dict {columna: Costs}.
        funding: tasa de funding por periodo y activo (el largo la paga, el corto la cobra).
        cash_rate: rendimiento del efectivo por periodo (fracción).
        lag: periodos entre la decisión y la ejecución. 1 = se ejecuta al cierre en que se decide.
        min_trade: no se opera si el cambio de peso de un activo es menor que esto.
        perp_cols: columnas que son perpetuos. No consumen efectivo (sólo margen) y pagan
            o cobran funding. Las demás columnas son spot: un peso positivo sale del efectivo.
        borrow_spread: costo extra por periodo sobre cash_rate cuando el efectivo queda negativo
            (apalancamiento en spot).
        capital_usd: tamaño real de la cuenta. Sólo hace falta para cuentas chicas, donde pesan
            la comisión mínima por orden y el monto mínimo por orden.
        min_fee_usd: comisión mínima por orden (IBKR: 1 dólar por orden de acciones).
        min_order_usd: monto mínimo por orden; los ajustes más chicos no se hacen.
    """
    prices = prices.sort_index()
    cols = list(prices.columns)
    targets = targets.reindex(index=prices.index, columns=cols)
    # Decidir en t y ejecutar en t + lag - 1 (lag=1: mismo cierre en que se decide)
    if lag > 1:
        targets = targets.shift(lag - 1)

    rets = prices.pct_change(fill_method=None).fillna(0.0).to_numpy()
    tgt = targets.to_numpy()
    tradable = prices.notna().to_numpy()

    if isinstance(costs, Costs):
        cost_vec = np.full(len(cols), costs.per_side)
    else:
        default = Costs()
        cost_vec = np.array([costs.get(c, default).per_side for c in cols])

    fund = (funding.reindex(index=prices.index, columns=cols).fillna(0.0).to_numpy()
            if funding is not None else np.zeros_like(rets))
    cash = (cash_rate.reindex(prices.index).fillna(0.0).to_numpy()
            if cash_rate is not None else np.zeros(len(prices)))

    perp_set = set(perp_cols or [])
    spot_mask = np.array([c not in perp_set for c in cols])

    n, m = rets.shape
    w = np.zeros(m)
    equity = np.empty(n)
    port_rets = np.zeros(n)
    w_hist = np.zeros((n, m))
    turnover = np.zeros(n)
    cost_hist = np.zeros(n)
    fund_hist = np.zeros(n)
    rebalances = 0
    eq = 1.0

    for t in range(n):
        if t > 0:
            # 1) Rendimiento del periodo sobre lo que se tenía
            cash_w = 1.0 - np.clip(w[spot_mask], 0, None).sum()
            price_pnl = float(np.dot(w, rets[t]))
            fund_pnl = float(-np.dot(w, fund[t]))
            cash_pnl = cash_w * (cash[t] if cash_w >= 0 else cash[t] + borrow_spread)
            r = price_pnl + fund_pnl + cash_pnl
            eq *= (1.0 + r)
            port_rets[t] = r
            fund_hist[t] = fund_pnl
            # 2) Los pesos se mueven con los precios
            if eq > 0:
                w = w * (1.0 + rets[t]) / (1.0 + r)
            else:
                w = np.zeros(m)

        # 3) Rebalanceo si hay decisión
        row = tgt[t]
        if not np.all(np.isnan(row)) and eq > 0:
            target = np.where(np.isnan(row), 0.0, row)
            target = np.where(tradable[t], target, 0.0)
            delta = target - w
            delta[np.abs(delta) < min_trade] = 0.0
            if capital_usd:
                acct = eq * capital_usd
                notional = np.abs(delta) * acct
                delta[notional < max(min_order_usd, 1e-9)] = 0.0
            if np.any(delta != 0):
                if capital_usd and min_fee_usd > 0:
                    # Cada activo que se mueve es una orden: paga el porcentaje o el mínimo, lo que sea mayor
                    per_order = np.maximum(np.abs(delta) * cost_vec, np.where(delta != 0, min_fee_usd / acct, 0.0))
                    c = float(per_order.sum())
                else:
                    c = float(np.dot(np.abs(delta), cost_vec))
                eq *= (1.0 - c)
                port_rets[t] = (1 + port_rets[t]) * (1 - c) - 1
                cost_hist[t] = c
                turnover[t] = float(np.abs(delta).sum())
                w = w + delta
                rebalances += 1

        equity[t] = eq
        w_hist[t] = w

    idx = prices.index
    return WeightsResult(
        equity=pd.Series(equity, index=idx, name="equity"),
        returns=pd.Series(port_rets, index=idx, name="returns"),
        weights=pd.DataFrame(w_hist, index=idx, columns=cols),
        turnover=pd.Series(turnover, index=idx),
        costs=pd.Series(cost_hist, index=idx),
        funding=pd.Series(fund_hist, index=idx),
        rebalances=rebalances,
    )


# ══════════ Motor de señales (estrategias del scalper) ══════════

@dataclass
class SignalTrade:
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    gross: float
    net: float
    reason: str


def backtest_signals(
    strategy,
    df: pd.DataFrame,
    symbol: str,
    costs: Costs,
    window: int = 100,
    allow_short: bool = True,
    min_confidence: float = 0.5,
) -> List[SignalTrade]:
    """
    Corre una estrategia del scalper (BaseStrategy) vela por vela.

    - La señal se evalúa al cierre de la vela t con las últimas `window` velas,
      igual que el executor en vivo.
    - La entrada se llena en la APERTURA de la vela t+1 (no al cierre que generó la señal).
    - El stop y el take profit se revisan contra el máximo y mínimo de cada vela.
      Si los dos caben en la misma vela se asume que primero tocó el stop.
    - Las salidas por indicador se llenan al cierre de la vela.
    """
    from core.adapters.base import PriceData
    from modules.scalper.strategies.base import Position

    trades: List[SignalTrade] = []
    pos = None
    pending = None
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = df.index
    loop = asyncio.new_event_loop()

    try:
        for i in range(window, len(df)):
            if pending is not None:
                sig = pending
                pending = None
                entry = o[i]
                # Se recalculan SL/TP sobre el precio real de entrada, manteniendo las distancias
                sl_dist = abs(sig.price - sig.stop_loss) / sig.price if sig.stop_loss else 0.003
                tp_dist = abs(sig.take_profit - sig.price) / sig.price if sig.take_profit else 0.005
                side = "long" if sig.action == "buy" else "short"
                sl = entry * (1 - sl_dist) if side == "long" else entry * (1 + sl_dist)
                tp = entry * (1 + tp_dist) if side == "long" else entry * (1 - tp_dist)
                pos = Position(symbol=symbol, side=side, entry_price=entry, quantity=1.0,
                               stop_loss=sl, take_profit=tp, entry_time=idx[i].to_pydatetime(),
                               strategy=strategy.name)
                pos_entry_time = idx[i]

            win = df.iloc[i - window + 1:i + 1]

            if pos is not None:
                exit_px, reason = None, ""
                if pos.side == "long":
                    if l[i] <= pos.stop_loss:
                        exit_px, reason = min(pos.stop_loss, o[i]) if o[i] < pos.stop_loss else pos.stop_loss, "stop"
                    elif h[i] >= pos.take_profit:
                        exit_px, reason = max(pos.take_profit, o[i]) if o[i] > pos.take_profit else pos.take_profit, "tp"
                else:
                    if h[i] >= pos.stop_loss:
                        exit_px, reason = max(pos.stop_loss, o[i]) if o[i] > pos.stop_loss else pos.stop_loss, "stop"
                    elif l[i] <= pos.take_profit:
                        exit_px, reason = min(pos.take_profit, o[i]) if o[i] < pos.take_profit else pos.take_profit, "tp"
                if exit_px is None:
                    ex = strategy.should_exit(pos, c[i], win)
                    if ex:
                        exit_px, reason = c[i], ex.reason
                if exit_px is not None:
                    sign = 1 if pos.side == "long" else -1
                    gross = sign * (exit_px / pos.entry_price - 1)
                    trades.append(SignalTrade(pos.side, pos_entry_time, idx[i], pos.entry_price,
                                              exit_px, gross, gross - 2 * costs.per_side, reason))
                    pos = None
                continue

            pdata = PriceData(symbol=symbol, market="crypto", timestamp=idx[i].to_pydatetime(),
                              bid=c[i], ask=c[i], last=c[i], volume=float(df["volume"].iat[i]))
            sig = loop.run_until_complete(strategy.analyze(symbol, pdata, win))
            if sig and sig.action in ("buy", "sell") and sig.confidence >= min_confidence:
                if sig.action == "sell" and not allow_short:
                    continue
                if i + 1 < len(df):
                    pending = sig
    finally:
        loop.close()

    return trades


def signal_trades_equity(trades: List[SignalTrade], index: pd.DatetimeIndex) -> pd.Series:
    """Curva de capital invirtiendo el 100% en cada operación, una a la vez."""
    eq = pd.Series(1.0, index=index)
    if not trades:
        return eq
    pnl = pd.Series([t.net for t in trades], index=[t.exit_time for t in trades])
    pnl = pnl.groupby(level=0).apply(lambda x: (1 + x).prod() - 1)
    growth = (1 + pnl).reindex(index).fillna(1.0).cumprod()
    return growth.rename("equity")


# ══════════ DCA ══════════

@dataclass
class DCAResult:
    value: pd.Series        # valor de la cuenta
    invested: pd.Series     # dinero aportado acumulado
    irr: float              # rendimiento anual equivalente del dinero aportado
    final_multiple: float   # valor final / aportado


def backtest_dca(prices: pd.Series, amount: float = 100.0, freq: str = "W-MON",
                 costs: Costs = Costs(fee=0.0, slippage=0.0005),
                 cash_rate: Optional[pd.Series] = None) -> DCAResult:
    """Compra `amount` cada periodo `freq` al cierre. Nunca vende."""
    prices = prices.dropna()
    buy_dates = prices.resample(freq).first().index
    buy_idx = prices.index.searchsorted(buy_dates)
    buy_idx = sorted(set(i for i in buy_idx if i < len(prices)))

    units = np.zeros(len(prices))
    invested = np.zeros(len(prices))
    flows = []
    for i in buy_idx:
        units[i] += amount * (1 - costs.per_side) / prices.iat[i]
        invested[i] += amount
        flows.append((prices.index[i], -amount))
    units = np.cumsum(units)
    invested = np.cumsum(invested)
    value = pd.Series(units * prices.to_numpy(), index=prices.index)
    flows.append((prices.index[-1], float(value.iat[-1])))

    return DCAResult(
        value=value,
        invested=pd.Series(invested, index=prices.index),
        irr=_xirr(flows),
        final_multiple=float(value.iat[-1] / invested[-1]) if invested[-1] else 0.0,
    )


def _xirr(flows) -> float:
    """TIR anual para flujos con fecha, por bisección."""
    t0 = flows[0][0]
    years = np.array([(d - t0).total_seconds() / 86400 / 365.25 for d, _ in flows])
    amounts = np.array([a for _, a in flows])

    def npv(rate):
        return float(np.sum(amounts / (1 + rate) ** years))

    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2
