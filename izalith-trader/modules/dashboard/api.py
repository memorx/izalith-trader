"""
Izalith Trading System - Dashboard API

Seguridad:
- Por defecto escucha sólo en 127.0.0.1 (DASHBOARD_HOST). Abrirlo a la red es decisión explícita.
- Sin CORS: sólo la propia página puede leer la API. Antes cualquier sitio abierto en el
  navegador podía leer posiciones y operaciones del localhost.
- Si DASHBOARD_TOKEN está definido, cada llamada a /api lo pide en el header X-Token
  (?token= también se acepta, para abrir una URL a mano, pero el panel no lo usa).
- No hay endpoints que operen: el dashboard sólo lee.
"""
import asyncio
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from config.assets import ASSETS
from core.utils.logger import get_logger

logger = get_logger("dashboard")

ROOT = Path(__file__).parent.parent.parent
REPORT_PATH = ROOT / "data" / "lab" / "report.json"
VENUES_PATH = ROOT / "data" / "lab" / "venues.json"
SMALL_PATH = ROOT / "data" / "lab" / "small_account.json"
POLY_PATH = ROOT / "data" / "lab" / "polymarket.json"
STATIC_DIR = Path(__file__).parent / "static"


class _Cache:
    """Caché simple con tiempo de vida para no golpear exchanges ni recalcular en cada recarga."""

    def __init__(self):
        self._data: Dict[str, Any] = {}

    def get(self, key: str, ttl: float):
        hit = self._data.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any):
        self._data[key] = (time.time(), value)


def _load_report() -> Optional[Dict]:
    if not REPORT_PATH.exists():
        return None
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def create_app(system=None, token: str = "") -> FastAPI:
    """
    Args:
        system: IzalithSystem en marcha (feed, portfolio, db, módulos). Puede ser None para
            sólo mostrar el laboratorio y los recolectores.
        token: si no está vacío, la API lo exige.
    """
    app = FastAPI(title="Izalith Trading System", docs_url=None, redoc_url=None, openapi_url=None)
    cache = _Cache()

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if token and request.url.path.startswith("/api"):
            given = request.headers.get("x-token") or request.query_params.get("token") or ""
            if not hmac.compare_digest(given, token):
                return JSONResponse({"detail": "Token inválido"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # El panel cambia seguido: que el navegador pregunte antes de usar su copia
        response.headers["Cache-Control"] = "no-cache"
        return response

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    def need(attr: str):
        obj = getattr(system, attr, None) if system else None
        if obj is None:
            raise HTTPException(503, f"El bot no está corriendo ({attr} no disponible)")
        return obj

    # ── Estado ──

    @app.get("/api/status")
    async def status():
        if not system:
            return {"running": False}
        s = system.settings
        modules = {}
        for name in system.modules:
            inst = system.instances.get(name)
            modules[name] = {"activo": name in system.instances or name in ("signals", "dashboard"),
                             "error": system.module_errors.get(name)}
            if inst is not None and hasattr(inst, "_running"):
                modules[name]["corriendo"] = bool(inst._running)
        return {
            "running": True,
            "dry_run": s.all_paper,
            "mode": system.mode_label,
            "live_markets": s.live_markets,
            "capital_warnings": system.capital_warnings,
            "timezone": s.trading_timezone,
            "markets": system.feed.get_market_status() if system.feed else {},
            "modules": modules,
            "unrecorded_orders": system.unrecorded_orders,
            "freno": (system.guard.state if getattr(system, "guard", None) else None),
            "max_drawdown_pct": s.max_drawdown_pct,
        }

    @app.get("/api/portfolio")
    async def portfolio():
        p = need("portfolio")
        await p.update_prices()
        return await p.get_summary()

    @app.get("/api/snapshots")
    async def snapshots(hours: int = 24):
        db = need("db")
        hours = max(1, min(hours, 24 * 365))
        return {"snapshots": await db.get_portfolio_snapshots(hours)}

    @app.get("/api/trades")
    async def trades(limit: int = 50):
        db = need("db")
        return {"trades": await db.get_recent_trades(max(1, min(limit, 500)))}

    @app.get("/api/orders")
    async def orders(limit: int = 50):
        db = need("db")
        return {"orders": await db.get_recent_orders(max(1, min(limit, 500)))}

    @app.get("/api/export.csv")
    async def export_csv(anio: Optional[int] = None, papel: bool = True):
        """Operaciones cerradas en CSV para el contador (columnas en español)."""
        import csv
        import io
        from zoneinfo import ZoneInfo
        from tools.exportar import COLUMNS, rows_for_csv
        db = need("db")
        trades = await db.get_closed_trades(since_iso=f"{anio}-01-01" if anio else None)
        if anio:
            trades = [t for t in trades if (t.get("closed_at") or "")[:4] == str(anio)]
        if not papel:
            trades = [t for t in trades if not t.get("simulated")]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows_for_csv(trades, ZoneInfo(system.settings.trading_timezone)):
            w.writerow(row)
        name = f"operaciones_{anio or 'todas'}.csv"
        return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/signals")
    async def signals():
        db = need("db")
        return {"signals": await db.get_signals_today()}

    @app.get("/api/prices")
    async def prices():
        feed = need("feed")
        got = await feed.get_prices_cached(list(ASSETS.keys()), max_age_s=10)
        return {"prices": {k: v.to_dict() for k, v in got.items()}}

    @app.get("/api/modules/{name}")
    async def module_status(name: str):
        inst = (system.instances.get(name) if system else None)
        if inst is None or not hasattr(inst, "get_status"):
            raise HTTPException(404, f"El módulo {name} no está corriendo")
        st = inst.get_status()
        if asyncio.iscoroutine(st):
            st = await st
        return st

    # ── Laboratorio ──

    @app.get("/api/lab")
    async def lab():
        report = _load_report()
        if not report:
            raise HTTPException(404, "Todavía no hay reporte. Corre: python -m lab.review")
        light = []
        for e in report["estrategias"]:
            light.append({k: v for k, v in e.items() if k not in ("curva", "drawdown", "curva_benchmark")})
        return {"generado": report["generado"], "estrategias": light}

    @app.get("/api/lab-small")
    async def lab_small():
        if not SMALL_PATH.exists():
            raise HTTPException(404, "Corre: python -m lab.small_account")
        return json.loads(SMALL_PATH.read_text(encoding="utf-8"))

    @app.get("/api/polymarket-estudio")
    async def polymarket_estudio():
        if not POLY_PATH.exists():
            raise HTTPException(404, "Corre: python -m lab.polymarket_study")
        return json.loads(POLY_PATH.read_text(encoding="utf-8"))

    @app.get("/api/lab/{key}")
    async def lab_strategy(key: str):
        report = _load_report()
        if not report:
            raise HTTPException(404, "Sin reporte")
        for e in report["estrategias"]:
            if e["key"] == key:
                return e
        raise HTTPException(404, f"No existe la estrategia {key}")

    @app.get("/api/venues")
    async def venues():
        if not VENUES_PATH.exists():
            raise HTTPException(404, "Sin información de plataformas")
        return json.loads(VENUES_PATH.read_text(encoding="utf-8"))

    # ── Mercados poco vigilados ──

    @app.get("/api/collectors")
    async def collectors(refresh: bool = False):
        inst = system.instances.get("collectors") if system else None
        if inst is not None and inst.get_latest() and not refresh:
            return inst.get_latest()
        cached = cache.get("collectors", ttl=600)
        if cached and not refresh:
            return cached
        from modules.collectors import sources
        snap = await asyncio.to_thread(sources.collect_all)
        cache.put("collectors", snap)
        return snap

    @app.get("/api/seguimiento")
    async def seguimiento():
        """Cada estrategia del allocator contra su backtest en las mismas fechas."""
        alloc = system.instances.get("allocator") if system else None
        if alloc is None:
            raise HTTPException(404, "El allocator no está corriendo")
        cached = cache.get("seguimiento", ttl=1800)
        if cached:
            return cached
        from lab.seguimiento import compare
        out = []
        for key in alloc.keys:
            snaps = await system.db.get_sleeve_snapshots(alloc.module_name(key))
            try:
                out.append(await asyncio.to_thread(compare, key, snaps, alloc.capital))
            except Exception as e:
                out.append({"key": key, "estado": f"error: {e}"})
        result = {"estrategias": out}
        cache.put("seguimiento", result)
        return result

    @app.get("/api/termometros")
    async def termometros(refresh: bool = False):
        from modules.collectors import sources
        from config.settings import get_settings
        inst = system.instances.get("collectors") if system else None
        latest = (inst.get_latest() or {}).get("termometros") if inst is not None else None
        hist = cache.get("termo_hist", ttl=3600)
        if hist is None or refresh:
            hist = await asyncio.to_thread(sources.thermometer_history)
            cache.put("termo_hist", hist)
        if latest is None or refresh:
            latest = cache.get("termo_now", ttl=900)
            if latest is None or refresh:
                latest = await asyncio.to_thread(sources.thermometers, get_settings().banxico_token)
                cache.put("termo_now", latest)
        return {"actual": latest, "series": hist}

    return app


def main():
    """Dashboard sin el bot: sólo laboratorio, plataformas y recolectores."""
    import uvicorn
    from config.settings import get_settings
    s = get_settings()
    app = create_app(system=None, token=s.dashboard_token)
    print(f"Dashboard (sólo lectura del laboratorio) en http://{s.dashboard_host}:{s.dashboard_port}")
    uvicorn.run(app, host=s.dashboard_host, port=s.dashboard_port, log_level="warning")


if __name__ == "__main__":
    main()
