"""
Izalith Trading System - Exportar operaciones para el contador

    python -m tools.exportar                    # todas las operaciones cerradas
    python -m tools.exportar --anio 2026        # sólo las cerradas en 2026
    python -m tools.exportar --incluir-papel    # incluye las de papel (por defecto sólo reales)

Escribe data/exports/operaciones_<año>.csv con columnas en español: fechas de compra y venta,
activo, mercado, cantidad, precios, comisiones y ganancia o pérdida en dólares. Las fechas van
en UTC y en la zona horaria configurada. El contador decide el tratamiento fiscal; esto sólo
junta los datos.
"""
import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings  # noqa: E402
from core.utils.database import Database  # noqa: E402

COLUMNS = ["id", "modulo", "estrategia", "activo", "mercado", "lado", "cantidad", "precio_entrada", "precio_salida",
           "fecha_entrada_utc", "fecha_salida_utc", "fecha_salida_local", "comisiones_usd", "resultado_usd",
           "resultado_pct", "papel", "notas"]


def rows_for_csv(trades, tz: ZoneInfo):
    for t in trades:
        closed = t.get("closed_at")
        local = (datetime.fromisoformat(closed).replace(tzinfo=timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M")
                 if closed else "")
        yield {
            "id": t["id"], "modulo": t["module"], "estrategia": t.get("strategy") or "", "activo": t["symbol"],
            "mercado": t["market"], "lado": "largo" if t["side"] == "long" else "corto",
            "cantidad": t["quantity"], "precio_entrada": t["entry_price"], "precio_salida": t.get("exit_price"),
            "fecha_entrada_utc": t["timestamp"], "fecha_salida_utc": closed or "", "fecha_salida_local": local,
            "comisiones_usd": round(float(t.get("fees") or 0), 6), "resultado_usd": round(float(t.get("pnl") or 0), 6),
            "resultado_pct": round(float(t.get("pnl_pct") or 0), 4), "papel": "sí" if t.get("simulated") else "no",
            "notas": t.get("notes") or "",
        }


async def export(year=None, include_paper=False, out_dir: Path = None) -> Path:
    s = get_settings()
    db = Database(s.db_path, s.trading_timezone)   # la misma base que usa el bot (papel o real)
    print(f"Base: {s.db_path}")
    await db.connect()
    try:
        since = f"{year}-01-01" if year else None
        trades = await db.get_closed_trades(since_iso=since)
    finally:
        await db.disconnect()
    if year:
        trades = [t for t in trades if (t.get("closed_at") or "")[:4] == str(year)]
    if not include_paper:
        trades = [t for t in trades if not t.get("simulated")]
    out_dir = out_dir or (s.data_dir / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"operaciones_{year or 'todas'}{'_con_papel' if include_paper else ''}.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # utf-8-sig: Excel abre bien los acentos
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows_for_csv(trades, ZoneInfo(s.trading_timezone)):
            w.writerow(row)
    total = sum(float(t.get("pnl") or 0) for t in trades)
    print(f"{len(trades)} operaciones cerradas, resultado total {total:+,.2f} USD -> {path}")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anio", type=int)
    ap.add_argument("--incluir-papel", action="store_true")
    args = ap.parse_args()
    asyncio.run(export(args.anio, args.incluir_papel))


if __name__ == "__main__":
    main()
