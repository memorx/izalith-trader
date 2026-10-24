"""
Izalith Trading System - Collectors Bot

Cada N minutos toma una foto de los mercados poco vigilados, la deja en memoria para el
dashboard y guarda una versión compacta en la base (tabla kv con historial por día) para
analizarla después: una ventaja sólo se puede probar con historial propio.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional

from core.utils.database import Database
from core.utils.logger import get_logger
from . import sources

logger = get_logger("collectors")


class CollectorsBot:
    def __init__(self, db: Database, config: dict):
        self.db = db
        self.banxico_token = config.get("banxico_token", "")
        self.interval = max(5, int(config.get("collectors_interval_minutes", 15))) * 60
        self.latest: Dict = {}
        self._running = False

    async def collect_once(self) -> Dict:
        snap = await asyncio.to_thread(sources.collect_all)
        try:
            snap["termometros"] = await asyncio.to_thread(sources.thermometers, self.banxico_token)
        except Exception as e:
            snap["termometros"] = {"error": str(e)}
        self.latest = snap
        await self._store(snap)
        return snap

    async def _store(self, snap: Dict) -> None:
        """Historial compacto: una fila por hora con lo esencial de cada fuente."""
        now = datetime.now(timezone.utc)
        f = snap.get("funding", {})
        compact = {
            "ts": now.isoformat(),
            "mayores": f.get("mayores"),
            "top": [(r["coin"], round(r["apr"], 4), round(r["volume_24h"])) for r in f.get("top_funding", [])[:15]],
            "diferencias": [(d["coin"], d["short_en"], d["long_en"], round(d["diferencia_apr"], 4))
                            for d in f.get("diferencias", [])[:15]],
            "acciones": [(r["coin"], r["mark"], round(r["apr"], 4)) for r in f.get("perpetuos_no_cripto", [])[:40]],
            "bitso": [(p["par"], round(p["prima_pct"], 4)) for p in snap.get("bitso", {}).get("pares", [])],
            "usdt_vs_usd": snap.get("bitso", {}).get("prima_usdt_vs_usd_pct"),
            "termometros": {r["key"]: r["valor"] for r in snap.get("termometros", {}).get("lecturas", [])},
            "paises": [(r["pais"], None if r.get("prima_usdt_pct") is None else round(r["prima_usdt_pct"], 3),
                        None if r.get("prima_btc_pct") is None else round(r["prima_btc_pct"], 3))
                       for r in snap.get("paises", {}).get("paises", [])],
        }
        key = f"collect:{now:%Y-%m-%dT%H}"
        # Spread de Kraken (idea N6): se guardan TODAS las lecturas de la hora, no sólo la última,
        # para saber a qué hora conviene rebalancear (revisión de Fable: antes no se guardaba)
        spreads = {par: None if v.get("spread_bps") is None else round(v["spread_bps"], 2)
                   for par, v in (snap.get("kraken_spreads") or {}).get("pares", {}).items()}
        prev = (await self.db.kv_get(key)) or {}
        compact["kraken_spreads"] = (prev.get("kraken_spreads") or []) + ([{"min": now.minute, **spreads}] if spreads else [])
        await self.db.kv_set(key, compact)

    async def run(self) -> None:
        self._running = True
        logger.info(f"Recolectores cada {self.interval // 60} min")
        while self._running:
            try:
                snap = await self.collect_once()
                errs = {k: v["error"] for k, v in snap.items() if isinstance(v, dict) and v.get("error")}
                if errs:
                    logger.warning(f"Recolectores con error: {errs}")
            except Exception as e:
                logger.error(f"Recolectores: {e}")
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        self._running = False

    def get_latest(self) -> Optional[Dict]:
        return self.latest or None
