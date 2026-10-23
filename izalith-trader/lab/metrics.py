"""
Izalith Trading System - Métricas de desempeño

Todas las métricas se calculan sobre rendimientos netos (después de costos).
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd


def periods_per_year(index: pd.DatetimeIndex) -> float:
    """Periodos por año según la frecuencia real del índice."""
    if len(index) < 3:
        return 365.0
    span_days = (index[-1] - index[0]).total_seconds() / 86400
    return (len(index) - 1) / span_days * 365.25 if span_days > 0 else 365.0


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def longest_drawdown_days(equity: pd.Series) -> int:
    """Días más largos que pasó la curva por debajo de su máximo anterior."""
    underwater = equity < equity.cummax()
    longest, start = 0, None
    for ts, under in underwater.items():
        if under and start is None:
            start = ts
        elif not under and start is not None:
            longest = max(longest, (ts - start).days)
            start = None
    if start is not None:
        longest = max(longest, (equity.index[-1] - start).days)
    return longest


def summarize(returns: pd.Series, cash_returns: Optional[pd.Series] = None,
              turnover: Optional[pd.Series] = None, costs: Optional[pd.Series] = None,
              exposure: Optional[pd.Series] = None) -> Dict:
    """Resumen estándar de una serie de rendimientos por periodo."""
    r = returns.dropna()
    if len(r) < 2:
        return {"periods": len(r)}
    ppy = periods_per_year(r.index)
    equity = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    total = float(equity.iat[-1] - 1)
    cagr = float(equity.iat[-1] ** (1 / years) - 1) if years > 0 and equity.iat[-1] > 0 else -1.0
    vol = float(r.std() * np.sqrt(ppy))

    rf = cash_returns.reindex(r.index).fillna(0.0) if cash_returns is not None else 0.0
    excess = r - rf
    # Si la estrategia casi nunca opera, el Sharpe sale de dividir entre casi cero: no dice nada
    flat = r.std() < 1e-9 or (r.abs() > 1e-12).mean() < 0.01
    sharpe = float(excess.mean() / excess.std() * np.sqrt(ppy)) if excess.std() > 0 and not flat else 0.0
    downside = excess[excess < 0]
    sortino = (float(excess.mean() / np.sqrt((downside ** 2).mean()) * np.sqrt(ppy))
               if len(downside) and (downside ** 2).mean() > 0 and not flat else 0.0)
    mdd = max_drawdown(equity)

    monthly = (1 + r).resample("ME").prod() - 1
    yearly = (1 + r).resample("YE").prod() - 1

    out = {
        "n_obs": int(len(r)),
        "ppy": float(ppy),
        "skew": float(r.skew()) if len(r) > 3 else 0.0,
        "kurtosis": float(r.kurt() + 3) if len(r) > 4 else 3.0,   # no excedente: normal = 3
        "start": r.index[0].date().isoformat(),
        "end": r.index[-1].date().isoformat(),
        "years": round(years, 2),
        "total_return": total,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else 0.0,
        "longest_drawdown_days": longest_drawdown_days(equity),
        "pct_positive_months": float((monthly > 0).mean()) if len(monthly) else 0.0,
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "best_month": float(monthly.max()) if len(monthly) else 0.0,
        "worst_year": float(yearly.min()) if len(yearly) else 0.0,
        "yearly": {str(k.year): float(v) for k, v in yearly.items()},
    }
    if turnover is not None:
        out["turnover_per_year"] = float(turnover.sum() / years) if years > 0 else 0.0
    if costs is not None:
        out["cost_drag_per_year"] = float(costs.sum() / years) if years > 0 else 0.0
    if exposure is not None:
        out["time_in_market"] = float((exposure.abs() > 1e-6).mean())
    return out


def trade_stats(net_returns) -> Dict:
    """Estadística por operación (para estrategias de señales)."""
    r = np.asarray(list(net_returns), dtype=float)
    if len(r) == 0:
        return {"trades": 0}
    wins, losses = r[r > 0], r[r <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    return {
        "trades": int(len(r)),
        "win_rate": float(len(wins) / len(r)),
        "avg_trade": float(r.mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy_pct": float(r.mean() * 100),
    }


def deflated_sharpe(sharpe_ann: float, ppy: float, n_obs: int, skew: float, kurt: float,
                    n_trials: int) -> float:
    """
    Probabilidad de que un Sharpe (o ratio de información) sea real y no el mejor de n_trials
    intentos sin ventaja. Bailey y López de Prado (2014), "The Deflated Sharpe Ratio".

    Bajo la hipótesis de azar, cada Sharpe estimado con T observaciones tiene varianza ~1/(T-1);
    el mejor de n_trials se espera en E[max] de n_trials normales. Más de 0.95 = probablemente
    no fue suerte. Se corrige por asimetría y colas gordas de los rendimientos.
    """
    from statistics import NormalDist
    nd = NormalDist()
    if n_trials < 1 or n_obs < 30 or not np.isfinite(sharpe_ann):
        return float("nan")
    sr = sharpe_ann / np.sqrt(ppy)
    euler = 0.5772156649
    if n_trials == 1:
        e_max = 0.0
    else:
        e_max = (1 - euler) * nd.inv_cdf(1 - 1 / n_trials) + euler * nd.inv_cdf(1 - 1 / (n_trials * np.e))
    sr0 = e_max / np.sqrt(n_obs - 1)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return float("nan")
    z = (sr - sr0) * np.sqrt(n_obs - 1) / np.sqrt(denom)
    return float(nd.cdf(z))
