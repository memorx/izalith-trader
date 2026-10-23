"""
Izalith Trading System - Ejecución de órdenes

Toda orden de un módulo pasa por aquí:
1. Se anota en la tabla `orders` ANTES de mandarla, con un client_order_id propio y un
   `purpose` (p. ej. "dca:BTC/USDT:2026-10-27T15:00"). Si ya hay una orden viva con ese
   propósito, no se manda otra: así un reinicio a media orden no compra dos veces.
2. Se manda al adaptador.
3. Se anota el resultado (lo que realmente se llenó, a qué precio y con qué comisión).

Al arrancar, reconcile_pending() revisa las órdenes que quedaron "pending" (el proceso
murió entre mandar y anotar) y le pregunta al exchange qué pasó con cada una.
"""
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter, OrderResult, new_client_order_id
from core.risk_guard import get_guard
from core.utils.database import Database
from core.utils.logger import get_logger

logger = get_logger("execution")


async def execute_order(
    db: Database,
    adapter: BaseAdapter,
    module: str,
    symbol: str,
    side: str,
    quantity: Optional[float] = None,
    amount: Optional[float] = None,
    purpose: Optional[str] = None,
    close_side: Optional[str] = None,
    passive: bool = False,
) -> OrderResult:
    """
    Manda una orden a mercado con registro previo.

    Args:
        side: buy / sell
        quantity: cantidad del activo (o)
        amount: monto en moneda de cotización (compra por monto)
        purpose: llave de idempotencia; si ya existe una orden viva con ella, no se repite
        close_side: 'long' o 'short' si la orden cierra una posición (usa close_position)
        passive: orden límite que espera en el libro (más barata; el adaptador decide si la soporta)
    """
    guard = get_guard()
    if guard is not None and not guard.allows(side, close_side, adapter.supports_short):
        logger.warning(f"[{module}] Freno de emergencia activo: no se manda {side} {symbol}")
        return OrderResult(success=False, symbol=symbol, side=side, status="freno",
                           error="Freno de emergencia activo: sólo se permiten órdenes que reducen la exposición")

    if purpose:
        prior = await db.get_order_by_purpose(purpose)
        if prior:
            logger.warning(f"[{module}] Ya hay una orden '{prior['status']}' para {purpose}; no se repite")
            return OrderResult(success=False, symbol=symbol, side=side, status="duplicate",
                               error=f"Orden ya enviada para {purpose}",
                               client_order_id=prior["client_order_id"])

    cid = new_client_order_id(prefix=module[:3])
    await db.insert_order_intent(module, symbol, side, cid, quantity=quantity, amount=amount, purpose=purpose)

    try:
        if close_side and passive and not adapter.supports_short:
            # En spot, cerrar un largo es vender: se puede hacer con orden pasiva
            result = await adapter.place_passive_order(symbol, "sell" if close_side == "long" else "buy", quantity, cid)
        elif close_side:
            result = await adapter.close_position(symbol, close_side, quantity, cid)
        elif amount is not None:
            result = await (adapter.place_passive_notional(symbol, side, amount, cid) if passive
                            else adapter.place_notional_order(symbol, side, amount, cid))
        else:
            result = await (adapter.place_passive_order(symbol, side, quantity, cid) if passive
                            else adapter.place_market_order(symbol, side, quantity, cid))
    except Exception as e:
        logger.error(f"[{module}] Excepción al mandar {side} {symbol}: {e}")
        # No sabemos si entró: se deja 'pending' para que la reconciliación lo resuelva
        await db.update_order(cid, "pending", error=f"excepción: {e}")
        return OrderResult(success=False, symbol=symbol, side=side, status="unknown", error=str(e),
                           client_order_id=cid)

    if result.success and result.filled_quantity > 0:
        status = "filled"
    elif result.success:
        status = result.status or "accepted"
    else:
        status = "failed"
    await db.update_order(cid, status, result.order_id, result.price or None, result.filled_quantity or None,
                          result.fee or None, result.error)
    result.client_order_id = cid
    return result


async def reconcile_pending(db: Database, adapters: Dict[str, BaseAdapter], market_of) -> List[Dict]:
    """
    Resuelve las órdenes que quedaron 'pending'. Regresa las que sí se llenaron en el exchange
    pero el bot no alcanzó a anotar, para mostrarlas como alerta en el dashboard.
    """
    unresolved = []
    for o in await db.get_pending_orders():
        adapter = adapters.get(market_of(o["symbol"]))
        found = await adapter.get_order_by_client_id(o["symbol"], o["client_order_id"]) if adapter else None
        if found and found.filled_quantity > 0:
            await db.update_order(o["client_order_id"], "filled_unrecorded", found.order_id, found.price,
                                  found.filled_quantity, found.fee,
                                  "Se llenó en el exchange pero el bot no la registró: revisar a mano")
            logger.error(f"[{o['module']}] La orden {o['client_order_id']} ({o['side']} {o['symbol']}) SÍ se llenó "
                         f"y no quedó registrada. Revísala en el exchange.")
            unresolved.append(o)
        else:
            await db.update_order(o["client_order_id"], "failed",
                                  error="Quedó pendiente al reiniciar y el exchange no la tiene")
    return unresolved
