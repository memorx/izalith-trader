"""
Izalith Trading System - ¿Qué pesa más: la estrategia o lo que se le aporta?

Idea B4 de Fable (24 oct 2026). Con 200 dólares ninguna estrategia hace riqueza; lo que
cambia el resultado es cuánto se le suma cada mes. Aquí se simula una cuenta que empieza con
200 dólares y recibe una aportación el primer día de cada mes, con:
- los rendimientos de cada estrategia con los costos reales del lugar donde se opera
  (Kraken con órdenes límite 70% maker para cripto, IBKR para ETFs) y una cuenta de 1,000;
- el efectivo ocioso al 0%, como en la cuenta real;
- la comisión de meter la aportación (Kraken 0.52% sobre lo que entra a cripto; IBKR 1 dólar
  por aportación si la estrategia está dentro).

Periodo común: desde enero de 2018 (lo que permiten los datos de cripto). Es historia, no
pronóstico: 2018-2026 incluye un mercado alcista fuerte de acciones y dos ciclos de cripto.

Uso:
    python -m lab.aportaciones
Escribe data/lab/aportaciones.json.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from lab.catalog import Ctx, get_spec
from lab.engine import Costs, _xirr

OUT = Path(__file__).parent.parent / "data" / "lab" / "aportaciones.json"

START = "2018-01-01"
INITIAL = 200.0
MONTHLY = [0, 50, 100, 250]
KRAKEN = Costs(0.7 * 0.004 + 0.3 * 0.008, 0.0002)
IBKR = Costs(0.00002, 0.0003)

# (clave, lugar): las que Memo puede correr hoy con una cuenta
STRATEGIES = [
    ("trend_voltarget", "kraken"),
    ("fab_vt_banda", "kraken"),
    ("fab_btc_oro", "kraken"),
    ("cash_plus_btc", "kraken"),
    ("faber_trend", "ibkr"),
    ("fab_gtaa3", "ibkr"),
    ("etf_60_40", "ibkr"),
]


def strategy_returns(key: str, venue: str, base: Ctx):
    spec = get_spec(key)
    if venue == "kraken":
        ctx = base.child(venue={"binance_spot": KRAKEN, "altcoin_spot": Costs(KRAKEN.fee, KRAKEN.slippage + 0.001)},
                         capital_usd=1000, min_order_usd=5.0, cash_zero=True)
    else:
        ctx = base.child(venue={"us_etf": IBKR}, capital_usd=1000, min_fee_usd=1.0, min_order_usd=20.0,
                         cash_zero=True)
    out = spec.run(ctx, **spec.params)

    def naive(x: pd.Series) -> pd.Series:
        x = x.copy()
        if getattr(x.index, "tz", None) is not None:
            x.index = x.index.tz_localize(None)
        return x
    r = naive(out.returns)
    exposure = naive(out.exposure) if out.exposure is not None else pd.Series(1.0, index=r.index)
    exposure = exposure.reindex(r.index).ffill().fillna(0.0)
    keep = r.index >= pd.Timestamp(START)
    return spec.nombre, r[keep], exposure[keep]


def simulate(r: pd.Series, exposure: pd.Series, monthly: float, venue: str):
    first_of_month = pd.Series(np.arange(len(r)), index=r.index).resample("MS").first().dropna().astype(int)
    contrib_days = set(first_of_month.to_numpy()[1:])     # el primer mes ya empieza con los 200
    value = INITIAL
    flows = [(r.index[0], -INITIAL)]
    values = []
    for i, (ts, ret) in enumerate(r.items()):
        value *= 1 + ret
        if monthly and i in contrib_days:
            invested = float(exposure.iat[i]) if i < len(exposure) else 1.0
            if venue == "kraken":
                cost = monthly * min(invested, 1.0) * KRAKEN.per_side
            else:
                cost = 1.0 if invested > 0.01 else 0.0
            value += monthly - cost
            flows.append((ts, -monthly))
        values.append(value)
    flows.append((r.index[-1], value))
    contributed = INITIAL + monthly * len(contrib_days)
    v = pd.Series(values, index=r.index)
    return {"aportado": contributed, "valor_final": value, "tir": _xirr(flows),
            "multiplo": value / contributed, "peor_baja_en_dolares": float((v - v.cummax()).min())}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    base = Ctx(refresh=False)
    rows = []
    for key, venue in STRATEGIES:
        name, r, exposure = strategy_returns(key, venue, base)
        for m in MONTHLY:
            res = simulate(r, exposure, m, venue)
            rows.append({"estrategia": key, "nombre": name, "lugar": venue, "mensual": m, **res})
    # Referencia: todo en efectivo con la tasa del T-bill (lo que daría CETES/T-bills sin riesgo de mercado)
    idx = pd.date_range(START, datetime.now(timezone.utc).date(), freq="D", tz="UTC")
    cash = Ctx(refresh=False).cash_for(idx)
    cash.index = cash.index.tz_localize(None)
    for m in MONTHLY:
        rows.append({"estrategia": "efectivo_tbill", "nombre": "Sólo T-bills (sin estrategia)", "lugar": "-",
                     "mensual": m, **simulate(cash, pd.Series(0.0, index=cash.index), m, "ibkr")})

    def clean(o):
        if isinstance(o, float):
            return None if np.isnan(o) or np.isinf(o) else o
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [clean(v) for v in o]
        return o

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(clean({
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "desde": START, "inicial": INITIAL, "mensuales": MONTHLY, "filas": rows,
    }), ensure_ascii=False), encoding="utf-8")

    print(f"Cuenta que empieza con ${INITIAL:,.0f} el {START} y aporta cada mes (efectivo ocioso al 0%):\n")
    print(f"{'Estrategia':44} " + " ".join(f"{'+$' + str(m) + '/mes':>22}" for m in MONTHLY))
    for key in dict.fromkeys(r["estrategia"] for r in rows):
        sel = sorted([r for r in rows if r["estrategia"] == key], key=lambda x: x["mensual"])
        cells = " ".join(f"{r['valor_final']:>9,.0f} de {r['aportado']:>6,.0f} ({(r['tir'] or 0) * 100:+5.1f}%)"
                         .rjust(22) for r in sel)
        print(f"{sel[0]['nombre'][:44]:44} {cells}")
    print(f"\n(valor final de lo aportado, y entre paréntesis la TIR anual del dinero aportado)\nEscrito: {OUT}")


if __name__ == "__main__":
    main()
