"""
Izalith Trading System - Ver o quitar el freno de emergencia

    python -m tools.freno            # muestra si está activo y el máximo registrado
    python -m tools.freno --quitar   # lo quita (el máximo se reinicia al valor de cuando se activó)

Quítalo sólo después de revisar qué pasó. Si el bot está corriendo, reinícialo después de quitarlo.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings  # noqa: E402
from core.risk_guard import RiskGuard  # noqa: E402
from core.utils.database import Database  # noqa: E402


async def main(quitar: bool) -> None:
    s = get_settings()
    db = Database(s.db_path, s.trading_timezone)   # la misma base que usa el bot (papel o real)
    print(f"Base: {s.db_path}")
    await db.connect()
    try:
        g = RiskGuard(db, s.max_drawdown_pct)
        await g.load()
        if not g.active:
            print(f"El freno no está activo. Máximo registrado: {g.peak:,.2f} USD; límite: -{s.max_drawdown_pct:g}%.")
            return
        print(f"Freno ACTIVO desde {g.state['desde']}: {g.state['motivo']}")
        if quitar:
            await g.release()
            print("Freno quitado. Reinicia el bot para que lo tome.")
    finally:
        await db.disconnect()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quitar", action="store_true")
    asyncio.run(main(ap.parse_args().quitar))
