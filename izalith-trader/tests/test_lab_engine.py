"""
Pruebas del motor de backtest: que no vea el futuro y que cobre lo que debe.
"""
import numpy as np
import pandas as pd
import pytest

from lab.engine import Costs, backtest_weights, backtest_dca, _xirr
from lab.metrics import summarize, max_drawdown
from lab.catalog import on_change, every, rsi_wilder

IDX = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
FREE = Costs(fee=0.0, slippage=0.0)


def test_hold_one_asset_matches_price():
    px = pd.DataFrame({"A": [100, 110, 99, 120, 120, 132.0]}, index=IDX)
    t = pd.DataFrame({"A": [1.0] + [np.nan] * 5}, index=IDX)
    res = backtest_weights(px, t, FREE)
    assert res.equity.iloc[-1] == pytest.approx(1.32)


def test_no_lookahead_signal_on_jump_day_does_not_capture_jump():
    # El precio salta el día 3; una señal que se decide al cierre del día 3 no debe ganar ese salto
    px = pd.DataFrame({"A": [100, 100, 100, 150, 150, 150.0]}, index=IDX)
    t = pd.DataFrame({"A": [0, np.nan, np.nan, 1.0, np.nan, np.nan]}, index=IDX)
    res = backtest_weights(px, t, FREE)
    assert res.equity.iloc[-1] == pytest.approx(1.0)


def test_lag_two_executes_one_bar_later():
    px = pd.DataFrame({"A": [100, 100, 110, 121, 121, 121.0]}, index=IDX)
    t = pd.DataFrame({"A": [0, 1.0, np.nan, np.nan, np.nan, np.nan]}, index=IDX)
    lag1 = backtest_weights(px, t, FREE, lag=1).equity.iloc[-1]
    lag2 = backtest_weights(px, t, FREE, lag=2).equity.iloc[-1]
    assert lag1 == pytest.approx(1.21)
    assert lag2 == pytest.approx(1.10)


def test_costs_charged_on_turnover():
    px = pd.DataFrame({"A": [100.0] * 6}, index=IDX)
    t = pd.DataFrame({"A": [1.0, np.nan, 0.0, np.nan, 1.0, np.nan]}, index=IDX)
    res = backtest_weights(px, t, Costs(fee=0.001, slippage=0.0))
    # Tres operaciones de 100% del capital: entrada, salida, entrada
    assert res.equity.iloc[-1] == pytest.approx(0.999 ** 3)
    assert res.rebalances == 3


def test_carry_neutral_collects_funding_only():
    # Spot y perpetuo con el mismo precio: el precio se cancela, queda el funding
    px = pd.DataFrame({"S": [100, 120, 80, 100, 100, 100.0], "P": [100, 120, 80, 100, 100, 100.0]}, index=IDX)
    fund = pd.DataFrame({"P": [0, 0.001, 0.001, 0.001, 0.001, 0.001]}, index=IDX)
    t = pd.DataFrame({"S": [0.5] + [np.nan] * 5, "P": [-0.5] + [np.nan] * 5}, index=IDX)
    res = backtest_weights(px, t, FREE, funding=fund, perp_cols=["P"])
    # 5 días cobrando 0.1% sobre la mitad del capital, más o menos
    assert res.equity.iloc[-1] == pytest.approx(1.0025, abs=2e-4)


def test_perp_long_does_not_use_cash():
    px = pd.DataFrame({"P": [100, 110, 110, 110, 110, 110.0]}, index=IDX)
    cash = pd.Series(0.01, index=IDX)
    t = pd.DataFrame({"P": [1.0] + [np.nan] * 5}, index=IDX)
    res = backtest_weights(px, t, FREE, cash_rate=cash, perp_cols=["P"])
    # El efectivo completo sigue ganando tasa porque el perpetuo sólo pide margen
    assert res.equity.iloc[1] == pytest.approx(1.0 + 0.10 + 0.01)


def test_on_change_keeps_only_changes():
    t = pd.DataFrame({"A": [0, 0, 1, 1, 0, 0.0]}, index=IDX)
    out = on_change(t)
    assert out["A"].notna().tolist() == [True, False, True, False, True, False]


def test_every_month_first_bar():
    idx = pd.date_range("2024-01-30", periods=5, freq="D", tz="UTC")
    mask = every(pd.DataFrame(index=idx), "MS")
    assert mask.tolist() == [True, False, True, False, False]


def test_rsi_wilder_bounds():
    s = pd.Series(np.cumsum(np.random.default_rng(0).normal(size=300)) + 100)
    r = rsi_wilder(s, 14).dropna()
    assert r.between(0, 100).all()


def test_dca_irr_flat_price_is_zero():
    idx = pd.date_range("2020-01-01", periods=400, freq="D", tz="UTC")
    res = backtest_dca(pd.Series(50.0, index=idx), 100, "W-MON", Costs(0.0, 0.0))
    assert res.irr == pytest.approx(0.0, abs=1e-6)
    assert res.final_multiple == pytest.approx(1.0)


def test_xirr_simple_year():
    t0 = pd.Timestamp("2020-01-01")
    assert _xirr([(t0, -100.0), (t0 + pd.Timedelta(days=365.25 * 1), 110.0)]) == pytest.approx(0.10, abs=1e-6)


def test_summarize_basic():
    idx = pd.date_range("2020-01-01", periods=731, freq="D", tz="UTC")
    r = pd.Series(0.0, index=idx)
    r.iloc[1] = -0.5
    r.iloc[2] = 1.0
    m = summarize(r)
    assert m["max_drawdown"] == pytest.approx(-0.5)
    assert m["total_return"] == pytest.approx(0.0)
    assert max_drawdown((1 + r).cumprod()) == pytest.approx(-0.5)


def test_min_fee_per_order_on_small_account():
    """Con 200 dólares, 1 dólar de comisión mínima por orden es 0.5% de la cuenta."""
    px = pd.DataFrame({"A": [100.0] * 6}, index=IDX)
    t = pd.DataFrame({"A": [1.0] + [np.nan] * 5}, index=IDX)
    res = backtest_weights(px, t, Costs(fee=0.00001, slippage=0.0), capital_usd=200, min_fee_usd=1.0)
    assert res.equity.iloc[-1] == pytest.approx(1 - 1 / 200)


def test_trades_below_min_order_are_skipped():
    px = pd.DataFrame({"A": [100.0] * 6}, index=IDX)
    t = pd.DataFrame({"A": [0.01] + [np.nan] * 5}, index=IDX)   # 2 dólares de 200
    res = backtest_weights(px, t, FREE, capital_usd=200, min_order_usd=5.0)
    assert res.rebalances == 0


def test_deflated_sharpe_penalizes_many_trials():
    from lab.metrics import deflated_sharpe
    one = deflated_sharpe(0.5, 252, 2520, 0.0, 3.0, 1)
    many = deflated_sharpe(0.5, 252, 2520, 0.0, 3.0, 50)
    assert 0 <= many < one <= 1


def test_deflated_sharpe_needs_long_history():
    from lab.metrics import deflated_sharpe
    short = deflated_sharpe(1.0, 252, 252, 0.0, 3.0, 7)      # 1 año
    long = deflated_sharpe(1.0, 252, 252 * 20, 0.0, 3.0, 7)  # 20 años
    assert short < 0.95 < long
