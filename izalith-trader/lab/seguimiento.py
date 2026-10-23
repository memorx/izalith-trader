"""
Izalith Trading System - Papel (o real) contra laboratorio

Para cada estrategia que corre el allocator compara dos curvas desde el día en que arrancó:
- lo que de verdad pasó con su bolsa (fotos que guarda el allocator en cada revisión)
- lo que el backtest dice que debió pasar en esas mismas fechas, con el mismo código

Si se parecen, el bot se porta como el laboratorio. Si se separan, algo cambió: comisiones,
ejecución, horarios o un bug. Con pocos días la diferencia es ruido; a partir de un mes ya dice algo.
"""
from typing import Dict, List

import pandas as pd

from lab.catalog import Ctx, get_spec


def compare(key: str, snapshots: List[Dict], capital: float, ctx: Ctx = None) -> Dict:
    if not snapshots:
        return {"key": key, "estado": "sin fotos todavía"}
    live = pd.Series([s["equity"] for s in snapshots],
                     index=pd.to_datetime([s["timestamp"] for s in snapshots], utc=True))
    live = live[~live.index.duplicated(keep="last")]
    start = live.index[0]
    daily_live = live.resample("1D").last().ffill()

    ctx = ctx or Ctx(refresh=False)
    spec = get_spec(key)
    out = spec.run(ctx, **spec.params)
    r = out.returns
    r = r[r.index >= start.normalize()]
    lab_curve = (1 + r).cumprod() * capital if len(r) else pd.Series(dtype=float)

    days = (live.index[-1] - start).total_seconds() / 86400
    live_ret = float(live.iloc[-1] / live.iloc[0] - 1)
    lab_ret = float(lab_curve.iloc[-1] / capital - 1) if len(lab_curve) else None
    result = {
        "key": key, "nombre": spec.nombre, "desde": start.isoformat(), "dias": round(days, 1),
        "rend_papel": live_ret, "rend_laboratorio": lab_ret,
        "diferencia": None if lab_ret is None else live_ret - lab_ret,
        "curva_papel": [[ts.strftime("%Y-%m-%d"), round(float(v), 4)] for ts, v in daily_live.items()],
        "curva_laboratorio": [[ts.strftime("%Y-%m-%d"), round(float(v), 4)] for ts, v in lab_curve.items()],
        "estado": "muy pronto para juzgar" if days < 30 else "comparable",
    }
    if len(lab_curve) > 5 and len(daily_live) > 5:
        both = pd.concat([daily_live.pct_change(), lab_curve.pct_change()], axis=1, keys=["p", "l"]).dropna()
        if len(both) > 5:
            result["correlacion_diaria"] = float(both["p"].corr(both["l"]))
            result["desvio_diario"] = float((both["p"] - both["l"]).std())
    return result
