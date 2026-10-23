"""
Izalith Trading System - Freno de emergencia

Si el valor total de la cuenta cae más de MAX_DRAWDOWN_PCT desde su máximo, el freno se activa:
- ninguna orden que AUMENTE la exposición se manda (compras, cortos nuevos, DCA, rebalanceos hacia arriba)
- las órdenes que la reducen (cerrar, vender lo que se tiene) sí pasan: el freno nunca te deja atrapado
- se avisa por el log, el panel y Telegram

El freno no se quita solo. Se quita a mano cuando ya revisaste qué pasó:
    python -m tools.freno --quitar

El estado vive en la base (tabla kv, llave "freno"), así que sobrevive a un reinicio.
Todas las órdenes pasan por core.execution.execute_order, que consulta este freno: ningún
módulo se lo puede saltar.
"""
from datetime import datetime, timezone
from typing import Dict, Optional

from core.utils.logger import get_logger

logger = get_logger("freno")

KEY = "freno"
PEAK_KEY = "freno:maximo"


class RiskGuard:
    def __init__(self, db, max_drawdown_pct: float = 25.0):
        self.db = db
        self.max_drawdown = abs(float(max_drawdown_pct)) / 100.0
        self.state: Optional[Dict] = None
        self.peak: float = 0.0

    async def load(self) -> None:
        self.state = await self.db.kv_get(KEY)
        self.peak = float(await self.db.kv_get(PEAK_KEY) or 0.0)
        if self.state:
            logger.error(f"El freno de emergencia sigue ACTIVO desde {self.state.get('desde')}: "
                         f"{self.state.get('motivo')}. Quítalo con: python -m tools.freno --quitar")

    @property
    def active(self) -> bool:
        return bool(self.state)

    async def update(self, total_value: float) -> bool:
        """Se llama con cada foto del portafolio. Regresa True si el freno se acaba de activar."""
        if total_value is None or total_value <= 0 or self.max_drawdown <= 0:
            return False
        if total_value > self.peak:
            self.peak = total_value
            await self.db.kv_set(PEAK_KEY, self.peak)
        if self.active or self.peak <= 0:
            return False
        dd = total_value / self.peak - 1
        if dd <= -self.max_drawdown:
            self.state = {
                "desde": datetime.now(timezone.utc).isoformat(),
                "motivo": f"la cuenta cayó {dd:.1%} desde su máximo ({self.peak:,.2f} -> {total_value:,.2f} USD); "
                          f"el límite es -{self.max_drawdown:.0%}",
                "maximo": self.peak, "valor": total_value, "caida": dd,
            }
            await self.db.kv_set(KEY, self.state)
            logger.error(f"FRENO DE EMERGENCIA ACTIVADO: {self.state['motivo']}. No se abren posiciones nuevas.")
            return True
        return False

    async def release(self, reset_peak: bool = True) -> None:
        """Quita el freno. Por defecto el máximo se reinicia al valor actual para no volver a dispararlo de inmediato."""
        value = (self.state or {}).get("valor")
        self.state = None
        await self.db.kv_set(KEY, None)
        if reset_peak and value:
            self.peak = float(value)
            await self.db.kv_set(PEAK_KEY, self.peak)
        logger.warning("Freno de emergencia quitado a mano")

    def allows(self, side: str, close_side: Optional[str], supports_short: bool) -> bool:
        """¿Se puede mandar esta orden con el freno puesto? Sólo si reduce la exposición."""
        if not self.active:
            return True
        if close_side:
            return True
        # En spot vender es reducir (no se puede vender lo que no se tiene); con cortos, vender abre exposición
        return side == "sell" and not supports_short


_guard: Optional[RiskGuard] = None


def set_guard(guard: Optional[RiskGuard]) -> None:
    global _guard
    _guard = guard


def get_guard() -> Optional[RiskGuard]:
    return _guard
