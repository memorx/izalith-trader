"""
Izalith Trading System - Revisión de todas las estrategias

Corre cada estrategia del catálogo con sus parámetros de libro y la somete a
cinco pruebas. Escribe data/lab/report.json (lo lee el dashboard) y
docs/ESTRATEGIAS.md (para leer).

Uso:
    python -m lab.review                    # todas
    python -m lab.review --only trend_btc   # una
    python -m lab.review --no-refresh       # sin bajar datos nuevos

Pruebas (semáforo):
  1. Le gana al efectivo (T-bill) en el periodo completo, después de costos
  2. También le gana en el último 40% del historial (fuera de muestra)
  3. Sigue ganándole con el doble de costos
  4. Aguanta ejecutar un periodo tarde (Sharpe >= 70% del original)
  5. Sus parámetros vecinos también funcionan (Sharpe mediano >= 70% del original)
  6. Su peor caída no pasa de 85% (más que eso es quiebra o liquidación)
  Además: un sesgo conocido del backtest (supervivencia) la deja en amarillo como máximo, y
  una estrategia de señales necesita al menos 30 operaciones para calificar.
  Verde = pasa todo. Amarillo = 1, 3 y 6 bien y falla a lo más una. Rojo = lo demás.
"""
import argparse
import json
import math
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from core.utils.logger import get_logger
from lab.catalog import CATALOG, NIVELES, Ctx, Spec
from lab.metrics import summarize, drawdown_series, deflated_sharpe

logger = get_logger("lab.review")

ROOT = Path(__file__).parent.parent
REPORT_PATH = ROOT / "data" / "lab" / "report.json"
DOC_PATH = ROOT / "docs" / "ESTRATEGIAS.md"
OOS_FRACTION = 0.4


def _cash_cagr(out, ctx: Ctx, index: pd.DatetimeIndex) -> float:
    cash = out.cash if out.cash is not None else ctx.cash_for(index)
    cash = cash.reindex(index).fillna(0.0)
    years = (index[-1] - index[0]).days / 365.25
    return float((1 + cash).prod() ** (1 / years) - 1) if years > 0 else 0.0


def _metrics(out, ctx: Ctx, start=None) -> dict:
    r = out.returns
    if start is not None:
        r = r[r.index >= start]
    cash = (out.cash if out.cash is not None else ctx.cash_for(out.returns.index)).reindex(r.index)
    m = summarize(r, cash_returns=cash,
                  turnover=out.turnover.reindex(r.index) if out.turnover is not None else None,
                  costs=out.costs.reindex(r.index) if out.costs is not None else None,
                  exposure=out.exposure.reindex(r.index) if out.exposure is not None else None)
    m["cash_cagr"] = _cash_cagr(out, ctx, r.index)
    if out.benchmark is not None:
        b = out.benchmark[out.benchmark.index >= r.index[0]]
        b = b[b.index <= r.index[-1]]
        if len(b) > 2:
            bm = summarize(b.pct_change().fillna(0.0), cash_returns=ctx.cash_for(b.index))
            m["benchmark"] = {k: bm[k] for k in ("cagr", "volatility", "sharpe", "max_drawdown", "total_return")}
    return m


def _curve(series: pd.Series, points: int = 400) -> list:
    """Curva reducida para el dashboard: [[fecha, valor], ...]."""
    s = series.dropna()
    if len(s) > points:
        step = math.ceil(len(s) / points)
        s = pd.concat([s.iloc[::step], s.iloc[[-1]]])
        s = s[~s.index.duplicated(keep="last")]
    return [[ts.strftime("%Y-%m-%d"), round(float(v), 5)] for ts, v in s.items()]


def _sharpe(out, ctx) -> float:
    return _metrics(out, ctx)["sharpe"]


def evaluate(spec: Spec, ctx: Ctx, robustness: bool = True) -> dict:
    t0 = time.time()
    entry = {
        "key": spec.key, "nombre": spec.nombre, "nivel": spec.nivel, "nivel_nombre": NIVELES[spec.nivel],
        "mercado": spec.mercado, "donde": spec.donde, "idea": spec.idea, "riesgos": spec.riesgos,
        "capital_min_usd": spec.capital_min_usd, "frecuencia": spec.frecuencia, "tipo": spec.tipo,
        "datos": spec.datos, "params": spec.params, "inventada": spec.inventada,
    }
    if spec.run is None:
        entry["semaforo"] = "investigacion"
        return entry

    out = spec.run(ctx, **spec.params)
    full = _metrics(out, ctx)
    entry["metricas"] = full
    entry["extra"] = out.extra
    if out.benchmark_name:
        entry["benchmark_name"] = out.benchmark_name

    idx = out.returns.index
    split = idx[int(len(idx) * (1 - OOS_FRACTION))]
    entry["oos_desde"] = split.date().isoformat()
    entry["metricas_oos"] = _metrics(out, ctx, start=split)

    eq = out.equity
    entry["curva"] = _curve(eq)
    entry["drawdown"] = _curve(drawdown_series(eq))
    if out.benchmark is not None:
        entry["curva_benchmark"] = _curve(out.benchmark.reindex(eq.index).ffill().dropna())

    checks = {}
    checks["gana_al_efectivo"] = full["cagr"] > full["cash_cagr"]
    checks["gana_fuera_de_muestra"] = entry["metricas_oos"]["cagr"] > entry["metricas_oos"]["cash_cagr"]

    if robustness and spec.tipo in ("weights", "signals"):
        base_sharpe = full["sharpe"]
        c2 = spec.run(ctx.child(cost_mult=2.0), **spec.params)
        m2 = _metrics(c2, ctx)
        entry["costos_x2"] = {"cagr": m2["cagr"], "sharpe": m2["sharpe"]}
        checks["aguanta_doble_costo"] = m2["cagr"] > m2["cash_cagr"]

        if spec.tipo == "weights":
            l2 = spec.run(ctx.child(lag=2), **spec.params)
            ml2 = _metrics(l2, ctx)
            entry["ejecucion_tarde"] = {"cagr": ml2["cagr"], "sharpe": ml2["sharpe"]}
            checks["aguanta_ejecutar_tarde"] = ml2["sharpe"] >= 0.7 * base_sharpe if base_sharpe > 0 else False

        neighbors = []
        for pname, values in spec.grid.items():
            for v in values:
                try:
                    o = spec.run(ctx, **{**spec.params, pname: v})
                    mm = _metrics(o, ctx)
                    neighbors.append({"param": pname, "valor": v, "cagr": mm["cagr"], "sharpe": mm["sharpe"],
                                      "max_drawdown": mm["max_drawdown"]})
                except Exception as e:
                    logger.warning(f"{spec.key} {pname}={v}: {e}")
        entry["vecinos"] = neighbors
        if neighbors:
            med = float(np.median([n["sharpe"] for n in neighbors]))
            checks["parametros_robustos"] = med >= 0.7 * base_sharpe if base_sharpe > 0 else False

    # Una caída de más de 85% es quiebra en la práctica (y con apalancamiento, liquidación)
    checks["sin_riesgo_de_ruina"] = full["max_drawdown"] > -0.85
    # Con menos de 3 años no hay ni un ciclo completo para juzgar
    checks["historial_suficiente"] = full["years"] >= 3
    if "supervivencia" in str(out.extra.get("aviso", "")):
        checks["sin_sesgo_conocido"] = False
    if spec.tipo == "signals":
        trades = out.extra.get("trade_stats", {}).get("trades", 0)
        checks["suficientes_operaciones"] = trades >= 30

    # Informativo, no cuenta para el semáforo: ¿mejor relación riesgo/rendimiento que sólo comprar?
    b = full.get("benchmark")
    if b:
        entry["vs_comprar_y_aguantar"] = {
            "mejor_sharpe": full["sharpe"] > b["sharpe"],
            "menor_caida": full["max_drawdown"] > b["max_drawdown"],
        }

    entry["pruebas"] = checks
    grade(entry)
    entry["segundos"] = round(time.time() - t0, 1)
    return entry


def grade(entry: dict) -> None:
    """Semáforo a partir de las pruebas (se vuelve a llamar al agregar la de 'no fue suerte')."""
    checks = entry.get("pruebas") or {}
    full = entry.get("metricas") or {}
    if not checks:
        return
    passed = sum(bool(v) for v in checks.values())
    core_ok = (checks.get("gana_al_efectivo") and checks.get("aguanta_doble_costo", True)
               and checks.get("sin_riesgo_de_ruina", True))
    if entry["tipo"] == "dca":
        # DCA no es para ganarle al mercado: se califica por su TIR contra el efectivo
        irr = (entry.get("extra") or {}).get("irr", 0) or 0
        entry["semaforo"] = "verde" if irr > full.get("cash_cagr", 0) and core_ok else "rojo"
    elif len(checks) >= 6 and passed == len(checks):
        entry["semaforo"] = "verde"
    elif core_ok and passed >= len(checks) - 1:
        entry["semaforo"] = "amarillo"
    else:
        entry["semaforo"] = "rojo"


def apply_luck_test(results: list, ctx: Ctx) -> None:
    """
    Prueba de "no fue suerte" para las estrategias inventadas.

    Se mide el ratio de información de la idea contra su versión base (lo que harías sin la
    idea) y se deflacta por el número de ideas que se probaron. Si la base es "cash", es el
    Sharpe normal contra el efectivo.
    """
    invented = [e for e in results if e.get("inventada") and e.get("metricas")]
    n_trials = max(1, len([s for s in CATALOG if s.inventada]))
    for e in invented:
        spec = next(s for s in CATALOG if s.key == e["key"])
        out = spec.run(ctx, **spec.params)
        r = out.returns
        if spec.base == "cash":
            base_r = ctx.cash_for(r.index)
            base_name = "el efectivo"
        elif spec.base == "benchmark" and out.benchmark is not None:
            base_r = out.benchmark.pct_change().reindex(r.index).fillna(0.0)
            base_name = out.benchmark_name
        else:
            bspec = next(s for s in CATALOG if s.key == spec.base)
            bout = bspec.run(ctx, **bspec.params)
            base_r = bout.returns
            base_name = bspec.nombre
        both = pd.concat([r, base_r], axis=1, keys=["s", "b"]).dropna()
        both = both[both.index >= max(r.index[0], base_r.index[0])]
        excess = both["s"] - both["b"]
        ppy = len(excess) / max((excess.index[-1] - excess.index[0]).days / 365.25, 1e-9)
        sd = excess.std()
        ir = float(excess.mean() / sd * np.sqrt(ppy)) if sd > 0 else 0.0
        skew = float(excess.skew()) if len(excess) > 3 else 0.0
        kurt = float(excess.kurt() + 3) if len(excess) > 4 else 3.0
        dsr = deflated_sharpe(ir, ppy, len(excess), skew, kurt, n_trials)
        e["contra_base"] = {"base": spec.base, "base_nombre": base_name, "ratio_informacion": ir,
                            "rend_extra_anual": float(excess.mean() * ppy), "ideas_probadas": n_trials}
        e["prob_no_suerte"] = None if dsr != dsr else dsr
        e.setdefault("pruebas", {})["no_es_suerte"] = bool(dsr == dsr and dsr >= 0.95)
        grade(e)


def _pct(x, digits=1):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{digits}f}%"


def write_markdown(report: dict) -> None:
    lines = [
        "# Estrategias: revisión con datos reales",
        "",
        f"Generado: {report['generado']}. Todos los números son **después de comisiones y deslizamiento**.",
        "Se usan los parámetros de libro, sin optimizar. 'Efectivo' es el T-bill de EE.UU. a 3 meses.",
        "",
        "Semáforo: 🟢 pasa las 5 pruebas · 🟡 pasa casi todas · 🔴 no sirve como está · 🔬 en investigación.",
        "",
        "| | Estrategia | Nivel | Rend. anual | Efectivo | Caída máx. | Sharpe | Fuera de muestra | Comprar y aguantar |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    icon = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴", "investigacion": "🔬", "error": "⚠️"}
    for e in report["estrategias"]:
        m = e.get("metricas")
        if not m:
            lines.append(f"| {icon[e['semaforo']]} | {e['nombre']} | {e['nivel_nombre']} | — | — | — | — | — | — |")
            continue
        o = e["metricas_oos"]
        b = m.get("benchmark", {})
        cagr = e["extra"].get("irr", m["cagr"]) if e["tipo"] == "dca" else m["cagr"]
        lines.append(
            f"| {icon[e['semaforo']]} | {e['nombre']} | {e['nivel_nombre']} | {_pct(cagr)} | {_pct(m['cash_cagr'])} "
            f"| {_pct(m['max_drawdown'], 0)} | {m['sharpe']:.2f} | {_pct(o['cagr'])} | "
            f"{_pct(b.get('cagr'))} / {_pct(b.get('max_drawdown'), 0)} |")
    lines += ["", "## Detalle", ""]
    for e in report["estrategias"]:
        lines.append(f"### {icon[e['semaforo']]} {e['nombre']} ({e['nivel_nombre']})")
        lines.append("")
        lines.append(f"- **Cómo gana:** {e['idea']}")
        lines.append(f"- **Riesgos:** {e['riesgos']}")
        lines.append(f"- **Dónde se opera:** {e['donde']}")
        lines.append(f"- **Capital mínimo sugerido:** {e['capital_min_usd']} USD · **Frecuencia:** {e['frecuencia']}")
        lines.append(f"- **Datos:** {e['datos']}")
        if e.get("metricas"):
            m = e["metricas"]
            lines.append(f"- **Periodo:** {m['start']} a {m['end']} ({m['years']} años)")
            if e["tipo"] == "dca":
                x = e["extra"]
                lines.append(f"- **Resultado:** aportó {x['invested']:,.0f}, terminó con {x['final_value']:,.0f} "
                             f"(x{x['final_multiple']:.2f}), TIR {_pct(x['irr'])} anual")
            else:
                lines.append(f"- **Rendimiento anual:** {_pct(m['cagr'])} · **caída máxima:** {_pct(m['max_drawdown'], 0)} "
                             f"· **peor año:** {_pct(m['worst_year'], 0)} · **meses positivos:** {m['pct_positive_months'] * 100:.0f}%")
            if "trade_stats" in e.get("extra", {}):
                ts = e["extra"]["trade_stats"]
                if ts.get("trades"):
                    lines.append(f"- **Operaciones:** {ts['trades']} · acierto {ts['win_rate'] * 100:.0f}% · "
                                 f"promedio neto {ts['expectancy_pct']:+.3f}% (bruto {ts['gross_expectancy_pct']:+.3f}%)")
                else:
                    lines.append("- **Operaciones:** 0 (la estrategia no disparó en todo el periodo)")
            if e.get("pruebas"):
                nombres = {"gana_al_efectivo": "le gana al efectivo", "gana_fuera_de_muestra": "fuera de muestra",
                           "aguanta_doble_costo": "doble costo", "aguanta_ejecutar_tarde": "ejecutar tarde",
                           "parametros_robustos": "parámetros vecinos", "sin_riesgo_de_ruina": "caída < 85%",
                           "sin_sesgo_conocido": "sin sesgo conocido",
                           "suficientes_operaciones": "30+ operaciones",
                           "historial_suficiente": "3+ años de historial",
                           "no_es_suerte": "no fue suerte"}
                lines.append("- **Pruebas:** " + " · ".join(
                    f"{'✅' if v else '❌'} {nombres.get(k, k)}" for k, v in e["pruebas"].items()))
            if e.get("extra", {}).get("aviso"):
                lines.append(f"- **Ojo:** {e['extra']['aviso']}")
        lines.append("")
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text("\n".join(lines), encoding="utf-8")


def _clean(o):
    """JSON no acepta NaN ni infinito."""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return _clean(float(o))
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="claves separadas por coma")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--fast", action="store_true", help="sin pruebas de robustez")
    args = ap.parse_args()

    ctx = Ctx(refresh=not args.no_refresh)
    specs = CATALOG
    if args.only:
        keys = set(args.only.split(","))
        specs = [s for s in CATALOG if s.key in keys]

    previous = {}
    if args.only and REPORT_PATH.exists():
        previous = {e["key"]: e for e in json.loads(REPORT_PATH.read_text(encoding="utf-8"))["estrategias"]}

    results = []
    for spec in specs:
        print(f"→ {spec.key} ...", flush=True)
        try:
            e = evaluate(spec, ctx, robustness=not args.fast)
            m = e.get("metricas") or {}
            print(f"   {e['semaforo']:13} anual {_pct(m.get('cagr'))}  caída {_pct(m.get('max_drawdown'), 0)}  "
                  f"sharpe {m.get('sharpe', 0):.2f}  ({e.get('segundos', 0)}s)", flush=True)
        except Exception as ex:
            traceback.print_exc()
            e = {"key": spec.key, "nombre": spec.nombre, "nivel": spec.nivel, "nivel_nombre": NIVELES[spec.nivel],
                 "semaforo": "error", "error": str(ex), "idea": spec.idea, "riesgos": spec.riesgos,
                 "donde": spec.donde, "capital_min_usd": spec.capital_min_usd, "frecuencia": spec.frecuencia,
                 "datos": spec.datos, "tipo": spec.tipo, "mercado": spec.mercado}
        results.append(e)

    if previous:
        merged = {**previous, **{e["key"]: e for e in results}}
        results = [merged[s.key] for s in CATALOG if s.key in merged]
    apply_luck_test(results, ctx)

    report = {"generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "estrategias": results}
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(_clean(report), ensure_ascii=False), encoding="utf-8")
    write_markdown(_clean(report))
    print(f"\nReporte: {REPORT_PATH}\nDocumento: {DOC_PATH}")


if __name__ == "__main__":
    main()
