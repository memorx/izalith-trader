"""
Izalith Trading System - Ejecución de órdenes

Toda orden de un módulo pasa por aquí:
1. Se anota en la tabla `orders` ANTES de mandarla, con un client_order_id propio y un
   `purpose` (p. ej. "dca:BTC/USDT:2026-10-27T15:00"). Si ya hay una orden con ese propósito,
   no se manda otra: así un reinicio a media orden no compra dos veces.
2. Se manda al adaptador.
3. Se anota el resultado (lo que realmente se llenó, a qué precio y con qué comisión).

Una orden anterior con el mismo propósito puede estar:
- terminada (llenada y registrada) -> "duplicate", no se repite;
- todavía viva en el exchange -> "en_vuelo", el módulo vuelve a intentar después;
- llenada pero sin registrar (se llenó después de que el bot dejó de esperarla, o el bot se
  cayó) -> se le entrega al módulo como un llenado tardío para que la registre normalmente;
- muerta sin llenarse -> se marca fallida y se manda una nueva.

reconcile_pending() revisa al arrancar (y cada pocos minutos) las órdenes que quedaron a medias:
cancela las que siguen vivas y deja anotado lo que sí se llenó.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core.adapters.base import BaseAdapter, OrderLookupError, OrderResult, OPEN_STATES, new_client_order_id
from core.risk_guard import get_guard
from core.utils.database import Database
from core.utils.logger import get_logger

logger = get_logger("execution")

# Estados de la tabla orders en los que el resultado todavía no se sabe
IN_FLIGHT = set(OPEN_STATES) | {"en_vuelo", "unknown", "pendingsubmit", "pendingcancel"}
# Si el exchange no conoce una orden de hace más de esto, no se asume que no entró: se revisa a mano
FORGET_AFTER_H = 12


def _age_hours(row: Dict) -> float:
    try:
        ts = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except Exception:
        return 0.0


def _late_fill(row: Dict, symbol: str) -> OrderResult:
    qty = float(row.get("filled_quantity") or 0)
    return OrderResult(success=True, order_id=row.get("exchange_order_id") or "", client_order_id=row["client_order_id"],
                       symbol=symbol, side=row.get("side") or "", quantity=qty, price=float(row.get("price") or 0),
                       filled_quantity=qty, status="filled_late", fee=float(row.get("fee") or 0))


async def _resolve_prior(db: Database, adapter: BaseAdapter, module: str, symbol: str, side: str,
                         prior: Dict) -> Optional[OrderResult]:
    """Qué hacer con la orden anterior del mismo propósito. None = está muerta y se puede mandar otra."""
    cid = prior["client_order_id"]
    status = (prior.get("status") or "").lower()
    if status == "filled_unrecorded":
        logger.warning(f"[{module}] La orden {cid} ({side} {symbol}) se llenó sin registrarse; se registra ahora")
        await db.update_order(cid, "filled", error="Registrada tarde (se llenó sin que el bot la anotara)")
        return _late_fill(prior, symbol)
    if status not in IN_FLIGHT:
        logger.warning(f"[{module}] Ya hay una orden '{status}' para {prior.get('purpose')}; no se repite")
        return OrderResult(success=False, symbol=symbol, side=side, status="duplicate",
                           error=f"Orden ya enviada para {prior.get('purpose')}", client_order_id=cid)
    try:
        found = await adapter.get_order_by_client_id(symbol, cid)
    except OrderLookupError as e:
        return OrderResult(success=False, symbol=symbol, side=side, status="en_vuelo", client_order_id=cid,
                           error=f"No se pudo preguntar al exchange por la orden anterior ({e}); se reintenta después")
    if found and (found.status or "").lower() in IN_FLIGHT:
        await db.update_order(cid, "open", found.order_id or None, found.price or None,
                              found.filled_quantity or None, found.fee or None, None)
        return OrderResult(success=False, symbol=symbol, side=side, status="en_vuelo", client_order_id=cid,
                           error="La orden anterior sigue viva en el exchange")
    if found and found.filled_quantity > 0:
        await db.update_order(cid, "filled", found.order_id or None, found.price or None, found.filled_quantity,
                              found.fee or None, "Se llenó después de que el bot dejó de esperarla")
        logger.warning(f"[{module}] La orden {cid} ({side} {symbol}) se llenó tarde: {found.filled_quantity} "
                       f"@ {found.price}; se registra ahora")
        found.success, found.status, found.client_order_id = True, "filled_late", cid
        return found
    if found is None and not adapter.is_paper and _age_hours(prior) > FORGET_AFTER_H:
        await db.update_order(cid, "revisar", error=f"El exchange ya no la tiene a la vista después de "
                                                    f"{FORGET_AFTER_H} h: revisar a mano si se llenó")
        logger.error(f"[{module}] La orden {cid} ({side} {symbol}) quedó sin resolver; no se repite. Revísala a mano.")
        return OrderResult(success=False, symbol=symbol, side=side, status="duplicate", client_order_id=cid,
                           error="Orden anterior sin resolver: revisar a mano")
    await db.update_order(cid, "failed", error="Ya no estaba viva en el exchange y no se llenó")
    return None


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
    Manda una orden con registro previo.

    Args:
        side: buy / sell
        quantity: cantidad del activo (o)
        amount: monto en moneda de cotización (compra por monto)
        purpose: llave de idempotencia (ver el docstring del módulo)
        close_side: 'long' o 'short' si la orden cierra una posición (usa close_position)
        passive: orden límite que espera en el libro (más barata; el adaptador decide si la soporta)

    Status que regresa además de los del exchange: "freno", "duplicate", "en_vuelo",
    "filled_late" (llenado de una orden anterior: el módulo lo registra como cualquier llenado).
    """
    guard = get_guard()
    if guard is not None and not guard.allows(side, close_side, adapter.supports_short):
        logger.warning(f"[{module}] Freno de emergencia activo: no se manda {side} {symbol}")
        return OrderResult(success=False, symbol=symbol, side=side, status="freno",
                           error="Freno de emergencia activo: sólo se permiten órdenes que reducen la exposición")

    if purpose:
        prior = await db.get_order_by_purpose(purpose)
        if prior:
            resolved = await _resolve_prior(db, adapter, module, symbol, side, prior)
            if resolved is not None:
                return resolved

    cid = new_client_order_id()
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
        return OrderResult(success=False, symbol=symbol, side=side, status="en_vuelo", error=str(e),
                           client_order_id=cid)

    st = (result.status or "").lower()
    if result.success and (st in IN_FLIGHT or (not st and result.filled_quantity <= 0)):
        # Aceptada pero sin terminar (IBKR "Submitted", Alpaca "accepted", pasiva sin cancelación confirmada).
        # Lo que ya se llenó NO se le entrega al módulo: se entrega completo cuando termine.
        await db.update_order(cid, "open", result.order_id or None, result.price or None,
                              result.filled_quantity or None, result.fee or None, result.error)
        return OrderResult(success=False, symbol=symbol, side=side, status="en_vuelo", client_order_id=cid,
                           error="La orden quedó viva en el exchange; se revisa en el siguiente intento")
    # Terminada sin llenarse (rechazada, cancelada, expirada) cuenta como fallida: el propósito se puede reintentar
    status = "filled" if result.success and result.filled_quantity > 0 else "failed"
    await db.update_order(cid, status, result.order_id, result.price or None, result.filled_quantity or None,
                          result.fee or None, result.error)
    result.client_order_id = cid
    return result


PROBLEMS = {
    "filled_unrecorded": "se llenó en el exchange y el bot todavía no la registra (se registra sola si el módulo "
                         "la vuelve a intentar; si no, revísala a mano)",
    "revisar": "quedó sin resolver: revisa en el exchange si se llenó",
    "en_vuelo": "sigue viva en el exchange y no se pudo cancelar",
}


async def reconcile_pending(db: Database, adapters: Dict[str, BaseAdapter], market_of,
                            min_age_s: float = 0.0) -> List[Dict]:
    """
    Resuelve las órdenes que quedaron a medias (el proceso murió, hubo una excepción, o el
    exchange las dejó vivas). Las que siguen vivas se cancelan: el código que las esperaba
    ya no existe. Con min_age_s sólo toca las que llevan ese tiempo sin moverse (para correrla
    mientras el bot trabaja sin pisar una orden que se está esperando).

    Regresa las órdenes que necesitan atención (ver PROBLEMS), para el dashboard y Telegram.
    """
    now = datetime.now(timezone.utc)
    for o in await db.get_orders_by_status(sorted(IN_FLIGHT)):
        try:
            upd = datetime.fromisoformat(str(o.get("updated_at") or o["created_at"]).replace("Z", "+00:00"))
            upd = upd if upd.tzinfo else upd.replace(tzinfo=timezone.utc)
            if (now - upd).total_seconds() < min_age_s:
                continue
        except Exception:
            pass
        cid, symbol = o["client_order_id"], o["symbol"]
        adapter = adapters.get(market_of(symbol))
        if adapter is None:
            continue
        try:
            found = await adapter.get_order_by_client_id(symbol, cid)
            if found and (found.status or "").lower() in IN_FLIGHT:
                if not await adapter.cancel_open_by_client_id(symbol, cid):
                    await db.update_order(cid, "en_vuelo", error="No se pudo cancelar")
                    logger.error(f"[{o['module']}] La orden {cid} ({o['side']} {symbol}) sigue viva y no se pudo cancelar")
                    continue
                found = await adapter.get_order_by_client_id(symbol, cid)
        except OrderLookupError as e:
            logger.warning(f"[{o['module']}] No se pudo revisar la orden {cid}: {e}; se intenta más tarde")
            continue
        if found and found.filled_quantity > 0:
            await db.update_order(cid, "filled_unrecorded", found.order_id or None, found.price or None,
                                  found.filled_quantity, found.fee or None,
                                  "Se llenó en el exchange y el bot no la registró")
            logger.error(f"[{o['module']}] La orden {cid} ({o['side']} {symbol}) SÍ se llenó "
                         f"({found.filled_quantity} @ {found.price}) y no quedó registrada.")
        elif found is None and not adapter.is_paper and _age_hours(o) > FORGET_AFTER_H:
            await db.update_order(cid, "revisar", error="El exchange ya no la tiene a la vista: revisar a mano")
        else:
            await db.update_order(cid, "failed", error="Quedó a medias y el exchange no la llenó")
    return await order_problems(db)


async def order_problems(db: Database) -> List[Dict]:
    rows = await db.get_orders_by_status(list(PROBLEMS))
    return [{**r, "problema": PROBLEMS[r["status"]]} for r in rows]
