"""
Izalith Trading System - ¿Los precios de Polymarket dicen la verdad?

Pregunta fijada ANTES de ver datos (22 oct 2026):
  En mercados de apuestas está documentado el "sesgo del favorito contra el perdedor": los muy
  favoritos ganan más seguido de lo que dice su precio y los muy perdedores menos. ¿Pasa en
  Polymarket, y alcanza para ganar después de costos?

Reglas fijas:
- Mercados resueltos de dos resultados (sí/no), volumen > 100,000 USD, cerrados desde 2024;
  3,000 tomados al azar (semilla fija) de todos los que cumplen, NO los de más volumen: ordenar por
  volumen total llena la muestra de sorpresas, porque una sorpresa genera muchísimo volumen.
- La "foto" es el último precio diario 7 días antes del cierre.
- Estrategia: comprar el favorito si cuesta entre 90 y 98 centavos y esperar a la resolución.
- Costos: comisión de Polymarket para quien toma liquidez (tasa x p x (1 - p), con tasa 0.05)
  más medio centavo de spread.
- Prueba: ¿cuántas desviaciones estándar se separan las victorias reales de lo que el precio
  promete? Y lo mismo en las dos mitades del periodo, por separado.

Limitaciones: el historial diario es el último precio operado, no el que habrías pagado; los
mercados de un mismo evento se mueven juntos (no son independientes); y elegir por volumen
deja fuera mercados chicos.

Uso:
    python -m lab.polymarket_study
Escribe data/lab/polymarket.json (lo lee el dashboard).
"""
import json
import math
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "data" / "cache" / "polymarket"
OUT = ROOT / "data" / "lab" / "polymarket.json"

MIN_VOLUME = 100_000
SINCE = "2024-01-01"
HORIZON_DAYS = 7
FAV_LOW, FAV_HIGH = 0.90, 0.98
FEE_RATE = 0.05
SPREAD = 0.005
MAX_MARKETS = 3000
BUCKETS = [0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0001]


def _get(url: str, retries: int = 3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "izalith-lab"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _parse_market(m):
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
        tokens = json.loads(m.get("clobTokenIds") or "[]")
    except Exception:
        return None
    closed = m.get("closedTime") or m.get("endDate")
    if (len(outcomes) != 2 or len(tokens) != 2 or sorted(prices) != [0.0, 1.0]
            or m.get("umaResolutionStatus") not in ("resolved", None) or not closed):
        return None
    return {"id": m["id"], "pregunta": m.get("question"), "cerro": closed, "volumen": float(m.get("volumeNum") or 0),
            "token_si": tokens[0], "gano_si": prices[0] == 1.0}


def closed_markets(seed: int = 2026):
    """
    Todos los mercados resueltos con más de MIN_VOLUME, semana por semana, y luego una muestra al
    azar. Ordenar por volumen total sesga la muestra: las sorpresas generan volumen.
    """
    import random
    candidates = {}
    week = datetime.fromisoformat(SINCE).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    while week < now:
        nxt = week + timedelta(days=7)
        for offset in range(0, 1000, 100):
            try:
                page = _get(f"https://gamma-api.polymarket.com/markets?closed=true&limit=100&offset={offset}"
                            f"&order=volumeNum&ascending=false&end_date_min={week:%Y-%m-%d}&end_date_max={nxt:%Y-%m-%d}")
            except urllib.error.HTTPError as e:
                if e.code == 422:
                    break
                raise
            if not page:
                break
            low = False
            for m in page:
                if float(m.get("volumeNum") or 0) < MIN_VOLUME:
                    low = True
                    break
                parsed = _parse_market(m)
                if parsed:
                    candidates[parsed["id"]] = parsed
            if low or len(page) < 100:
                break
        week = nxt
    pool = sorted(candidates.values(), key=lambda m: m["id"])
    print(f"{len(pool)} mercados candidatos con más de {MIN_VOLUME:,} USD; se toman {min(MAX_MARKETS, len(pool))} al azar",
          flush=True)
    return random.Random(seed).sample(pool, min(MAX_MARKETS, len(pool)))


def history(token: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{token[:40]}.json"
    if path.exists():
        return json.loads(path.read_text())
    h = _get(f"https://clob.polymarket.com/prices-history?market={token}&interval=max&fidelity=1440").get("history", [])
    path.write_text(json.dumps(h))
    return h


def parse_time(s: str) -> float:
    s = s.replace("Z", "+00:00").replace(" ", "T")
    if s.endswith("+00"):
        s += ":00"
    return datetime.fromisoformat(s).timestamp()


def snapshot(m):
    try:
        h = history(m["token_si"])
    except Exception:
        return None
    if len(h) < HORIZON_DAYS + 1:
        return None
    t_close = parse_time(m["cerro"])
    t_snap = t_close - HORIZON_DAYS * 86400
    before = [p for p in h if p["t"] <= t_snap]
    if not before or h[0]["t"] > t_snap - 86400:
        return None
    return {**m, "precio_si": float(before[-1]["p"]), "t_foto": t_snap}


def bet_return(p: float, won: bool) -> float:
    """Rendimiento de comprar a precio p y esperar la resolución, después de comisión y spread."""
    cost = p + FEE_RATE * p * (1 - p) + SPREAD
    return (1 - cost) / cost if won else -1.0


def analyze(rows):
    # Calibración: precio prometido contra frecuencia real, por tramos de precio del "sí"
    calib = []
    for lo, hi in zip(BUCKETS[:-1], BUCKETS[1:]):
        sel = [r for r in rows if lo <= r["precio_si"] < hi]
        if not sel:
            continue
        implied = sum(r["precio_si"] for r in sel) / len(sel)
        actual = sum(r["gano_si"] for r in sel) / len(sel)
        calib.append({"tramo": f"{lo * 100:.0f}-{min(hi, 1) * 100:.0f}¢", "n": len(sel), "precio": implied,
                      "real": actual, "diferencia": actual - implied})

    def strategy(sel):
        bets = []
        for r in sel:
            p_si = r["precio_si"]
            if FAV_LOW <= p_si <= FAV_HIGH:
                bets.append((p_si, r["gano_si"], r))
            elif FAV_LOW <= 1 - p_si <= FAV_HIGH:
                bets.append((1 - p_si, not r["gano_si"], r))
        if not bets:
            return {"apuestas": 0}
        wins = sum(1 for _, w, _ in bets if w)
        expected = sum(p for p, _, _ in bets)
        var = sum(p * (1 - p) for p, _, _ in bets)
        rets = [bet_return(p, w) for p, w, _ in bets]
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1))
        return {
            "apuestas": len(bets), "ganadas": wins, "esperadas_por_precio": expected,
            "z_victorias": (wins - expected) / math.sqrt(var) if var > 0 else 0.0,
            "rend_promedio": mean, "rend_desv": sd,
            "z_rendimiento": mean / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0,
            "peor_racha_perdidas": _worst_streak([w for _, w, _ in sorted(bets, key=lambda b: b[2]["t_foto"])]),
        }

    rows_sorted = sorted(rows, key=lambda r: r["t_foto"])
    half = len(rows_sorted) // 2
    return {
        "calibracion": calib,
        "favoritos": strategy(rows_sorted),
        "favoritos_primera_mitad": strategy(rows_sorted[:half]),
        "favoritos_segunda_mitad": strategy(rows_sorted[half:]),
        "mitad_desde": datetime.fromtimestamp(rows_sorted[half]["t_foto"], tz=timezone.utc).date().isoformat() if rows_sorted else None,
    }


def _worst_streak(wins):
    worst = cur = 0
    for w in wins:
        cur = 0 if w else cur + 1
        worst = max(worst, cur)
    return worst


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    markets = closed_markets()
    rows = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(snapshot, m) for m in markets]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            if r:
                rows.append(r)
            if i % 250 == 0:
                print(f"  {i}/{len(markets)} revisados, {len(rows)} con foto a {HORIZON_DAYS} días", flush=True)
    result = analyze(rows)
    result.update({
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "reglas": {"volumen_minimo": MIN_VOLUME, "desde": SINCE, "dias_antes_del_cierre": HORIZON_DAYS,
                   "favorito_entre": [FAV_LOW, FAV_HIGH], "tasa_comision": FEE_RATE, "spread": SPREAD},
        "mercados_con_foto": len(rows),
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nCalibración (precio del sí 7 días antes, contra cuántas veces ganó el sí):")
    for c in result["calibracion"]:
        print(f"  {c['tramo']:>9}  n={c['n']:5}  precio {c['precio']*100:5.1f}%  real {c['real']*100:5.1f}%  "
              f"diferencia {c['diferencia']*100:+5.1f} pts")
    for k in ("favoritos", "favoritos_primera_mitad", "favoritos_segunda_mitad"):
        f = result[k]
        if f.get("apuestas"):
            print(f"{k}: {f['apuestas']} apuestas, ganó {f['ganadas']} (el precio prometía {f['esperadas_por_precio']:.0f}), "
                  f"z={f['z_victorias']:+.2f}; rendimiento promedio {f['rend_promedio']*100:+.2f}% por apuesta "
                  f"(z={f['z_rendimiento']:+.2f}); peor racha {f['peor_racha_perdidas']} pérdidas")
    print(f"\nEscrito: {OUT}")


if __name__ == "__main__":
    main()
