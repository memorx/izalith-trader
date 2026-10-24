"""
Izalith Trading System - ¿Qué queda con una cuenta chica?

Corre las estrategias que el bot puede operar en vivo (portafolio, sin perpetuos) con
los costos reales de cada plataforma y con cuentas de 100, 200, 1,000 y 5,000 dólares.

Con poco capital pesan dos cosas que el backtest normal no ve:
- la comisión mínima por orden (IBKR cobra al menos 1 dólar por orden de acciones)
- el monto mínimo por orden (ajustes chicos no se pueden hacer)

Comisiones del nivel de entrada, verificadas el 22 oct 2026:
- Kraken Pro: 0.40% si la orden espera (límite) y 0.80% si toma precio (mercado)
- Binance: 0.10% (sólo para residentes de México; no opera en Canadá)
- Bitso, pares USD/USDT: 0.30% / 0.36%
- "Efectivo al 0%": en la cuenta real el efectivo ocioso no gana nada (Kraken 0%, IBKR 0% con
  menos de 10 mil dólares), así que también se reporta el rendimiento sin el T-bill que el
  laboratorio le paga al efectivo.
- NDAX: 0.20% fija (sólo Canadá)
- IBKR Pro Fixed: 0.005 USD por acción, mínimo 1 USD por orden (el tope de 1% del monto no se
  modela: en órdenes de menos de 100 USD el costo real es algo menor que el de aquí)

Órdenes límite en Kraken: no todas se llenan esperando. Se supone que 70% se llena como maker
(0.40%) y el 30% restante termina a mercado (0.80%): 0.52% por lado.

Uso:
    python -m lab.small_account
Escribe data/lab/small_account.json (lo lee el dashboard).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lab.catalog import Ctx, get_spec
from lab.engine import Costs
from lab.metrics import summarize

OUT = Path(__file__).parent.parent / "data" / "lab" / "small_account.json"

CRYPTO = ["cash_plus_btc", "trend_btc", "trend_voltarget", "xs_momentum", "fab_vt_banda", "fab_btc_oro", "fab_xs_pit",
          "eje_vol_ewma", "eje_medio_camino", "cla_btc_oro_banda", "fab_btc_votos", "fab_freno_bolsa"]
ETF = ["etf_60_40", "faber_trend", "dual_momentum", "rsi2_spy", "fab_faber_bil", "fab_gtaa3", "fab_faber_vol",
       "fab_paridad"]
CAPITALS = [100, 200, 1000, 5000]
KRAKEN_LIMIT_BLEND = 0.7 * 0.004 + 0.3 * 0.008

# Comisión por lado + deslizamiento. Las altcoins tienen libros más delgados.
CRYPTO_VENUES = {
    "kraken_limite": ("Kraken Pro, órdenes límite (70% maker)", "MX y CA", Costs(KRAKEN_LIMIT_BLEND, 0.0002), 5.0),
    "kraken_mercado": ("Kraken Pro, órdenes a mercado", "MX y CA", Costs(0.008, 0.0005), 5.0),
    "bitso_usdt": ("Bitso, pares en USDT", "MX", Costs(0.0036, 0.0005), 5.0),
    "ndax": ("NDAX", "CA", Costs(0.002, 0.0005), 5.0),
    "binance": ("Binance", "MX", Costs(0.001, 0.0005), 5.0),
}
# IBKR: 0.005 USD por acción es ~0.001% en un ETF de 400-700 USD; lo que pesa es el mínimo de 1 USD.
IBKR = ("Interactive Brokers (Pro Fixed)", "MX y CA", Costs(0.00002, 0.0003), 1.0, 20.0)


def run(key: str, ctx: Ctx) -> dict:
    spec = get_spec(key)
    out = spec.run(ctx, **spec.params)
    m = summarize(out.returns, cash_returns=out.cash if out.cash is not None else ctx.cash_for(out.returns.index),
                  turnover=out.turnover, costs=out.costs)
    years = m["years"] or 1
    return {
        "cagr": m["cagr"], "max_drawdown": m["max_drawdown"], "sharpe": m["sharpe"],
        "cost_drag_per_year": m.get("cost_drag_per_year", 0.0),
        "rebalances_per_year": out.extra.get("rebalances", 0) / years,
        "start": m["start"],
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    base = Ctx(refresh=False)
    rows = []

    for key in CRYPTO:
        spec = get_spec(key)
        lab = run(key, base)
        for vkey, (vname, where, cost, min_order) in CRYPTO_VENUES.items():
            alt = Costs(cost.fee, cost.slippage + 0.001)
            for cap in CAPITALS:
                ctx = base.child(venue={"binance_spot": cost, "altcoin_spot": alt}, capital_usd=cap,
                                 min_order_usd=min_order)
                r = run(key, ctx)
                r0 = run(key, ctx.child(cash_zero=True))
                rows.append({"estrategia": key, "nombre": spec.nombre, "nivel": spec.nivel, "lugar": vkey,
                             "lugar_nombre": vname, "pais": where, "capital": cap, **r,
                             "cagr_efectivo_0": r0["cagr"], "cagr_laboratorio": lab["cagr"]})
        print(f"{spec.nombre}: listo", flush=True)

    vname, where, cost, min_fee, min_order = IBKR
    for key in ETF:
        spec = get_spec(key)
        lab = run(key, base)
        for cap in CAPITALS:
            ctx = base.child(venue={"us_etf": cost}, capital_usd=cap, min_fee_usd=min_fee, min_order_usd=min_order)
            r = run(key, ctx)
            r0 = run(key, ctx.child(cash_zero=True))
            rows.append({"estrategia": key, "nombre": spec.nombre, "nivel": spec.nivel, "lugar": "ibkr",
                         "lugar_nombre": vname, "pais": where, "capital": cap, **r,
                         "cagr_efectivo_0": r0["cagr"], "cagr_laboratorio": lab["cagr"]})
        print(f"{spec.nombre}: listo", flush=True)

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
        "capitales": CAPITALS,
        "lugares": {k: {"nombre": v[0], "pais": v[1], "comision": v[2].fee} for k, v in CRYPTO_VENUES.items()}
        | {"ibkr": {"nombre": IBKR[0], "pais": IBKR[1], "comision": IBKR[2].fee, "minimo_por_orden": IBKR[3]}},
        "filas": rows,
    }), ensure_ascii=False), encoding="utf-8")

    print(f"\n{'Estrategia':44} {'Lugar':32} " + " ".join(f"{'$' + str(c):>9}" for c in CAPITALS) + "   laboratorio")
    seen = set()
    for r in rows:
        k = (r["estrategia"], r["lugar"])
        if k in seen:
            continue
        seen.add(k)
        vals = [x for x in rows if (x["estrategia"], x["lugar"]) == k]
        vals.sort(key=lambda x: x["capital"])
        cells = " ".join(f"{v['cagr'] * 100:+8.1f}%" for v in vals)
        zero = next((v["cagr_efectivo_0"] for v in vals if v["capital"] == 1000), None)
        print(f"{r['nombre'][:44]:44} {r['lugar_nombre'][:32]:32} {cells}   {r['cagr_laboratorio'] * 100:+.1f}%"
              f"   efectivo al 0%: {zero * 100:+.1f}%")
    print(f"\nEscrito: {OUT}")


if __name__ == "__main__":
    main()
