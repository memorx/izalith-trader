"""
Izalith Trading System - Fuentes públicas de los recolectores

Funciones síncronas (se corren en un hilo). Todas usan APIs públicas, sin llaves.
El funding se anualiza con el intervalo real de cada contrato: Binance y Bybit cobran
cada 1, 4 u 8 horas según la moneda; Hyperliquid cada hora.
"""
import json
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

UA = {"User-Agent": "izalith-collectors", "Content-Type": "application/json"}


def _get(url: str, payload=None, timeout: int = 20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _apr(rate: float, interval_h: float) -> float:
    return rate * (24.0 / interval_h) * 365.0


# ══════════ Funding ══════════

def binance_funding() -> Dict[str, Dict]:
    idx = _get("https://fapi.binance.com/fapi/v1/premiumIndex")
    intervals = {r["symbol"]: float(r.get("fundingIntervalHours") or 8)
                 for r in _get("https://fapi.binance.com/fapi/v1/fundingInfo")}
    info = _get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    # Las acciones, metales e índices tradicionales vienen como TRADIFI_PERPETUAL
    kinds = {s["symbol"]: s.get("underlyingType", "COIN") for s in info["symbols"]
             if s.get("status") == "TRADING" and s.get("contractType") in ("PERPETUAL", "TRADIFI_PERPETUAL")}
    vols = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in _get("https://fapi.binance.com/fapi/v1/ticker/24hr")}
    out = {}
    for r in idx:
        sym = r["symbol"]
        if not sym.endswith("USDT") or sym not in kinds:
            continue
        coin = sym[:-4]
        h = intervals.get(sym, 8.0)
        rate = float(r["lastFundingRate"])
        out[coin] = {"venue": "binance", "symbol": sym, "kind": kinds[sym], "rate": rate, "interval_h": h,
                     "apr": _apr(rate, h), "mark": float(r["markPrice"]), "index": float(r["indexPrice"]),
                     "basis_pct": (float(r["markPrice"]) / float(r["indexPrice"]) - 1) * 100 if float(r["indexPrice"]) else 0.0,
                     "volume_24h": vols.get(sym, 0.0), "next_funding": int(r["nextFundingTime"])}
    return out


def bybit_funding() -> Dict[str, Dict]:
    rows = _get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]
    out = {}
    for r in rows:
        sym = r["symbol"]
        if not sym.endswith("USDT") or not r.get("fundingRate"):
            continue
        h = float(r.get("fundingIntervalHour") or 8)
        rate = float(r["fundingRate"])
        out[sym[:-4]] = {"venue": "bybit", "symbol": sym, "rate": rate, "interval_h": h, "apr": _apr(rate, h),
                         "mark": float(r.get("markPrice") or 0), "volume_24h": float(r.get("turnover24h") or 0)}
    return out


def hyperliquid_funding() -> Dict[str, Dict]:
    meta, ctxs = _get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    out = {}
    for asset, ctx in zip(meta["universe"], ctxs):
        if asset.get("isDelisted"):
            continue
        rate = float(ctx.get("funding") or 0)
        out[asset["name"]] = {"venue": "hyperliquid", "symbol": asset["name"], "rate": rate, "interval_h": 1.0,
                              "apr": _apr(rate, 1.0), "mark": float(ctx.get("markPx") or 0),
                              "volume_24h": float(ctx.get("dayNtlVlm") or 0),
                              "max_leverage": asset.get("maxLeverage")}
    return out


def funding_snapshot(min_volume: float = 5_000_000) -> Dict:
    """Funding de tres lugares y la diferencia entre ellos para la misma moneda."""
    venues, errors = {}, {}
    for name, fn in (("binance", binance_funding), ("bybit", bybit_funding), ("hyperliquid", hyperliquid_funding)):
        try:
            venues[name] = fn()
        except Exception as e:
            errors[name] = str(e)
            venues[name] = {}

    coins = set().union(*[set(v) for v in venues.values()]) if venues else set()
    spreads = []
    for coin in coins:
        quotes = [venues[v][coin] for v in venues if coin in venues[v] and venues[v][coin]["volume_24h"] >= min_volume]
        if len(quotes) < 2:
            continue
        hi = max(quotes, key=lambda q: q["apr"])
        lo = min(quotes, key=lambda q: q["apr"])
        spreads.append({"coin": coin, "short_en": hi["venue"], "apr_alto": hi["apr"],
                        "long_en": lo["venue"], "apr_bajo": lo["apr"], "diferencia_apr": hi["apr"] - lo["apr"],
                        "volumen_min": min(q["volume_24h"] for q in quotes)})
    spreads.sort(key=lambda x: x["diferencia_apr"], reverse=True)

    binance = venues.get("binance", {})
    liquid = [dict(coin=c, **q) for c, q in binance.items() if q["volume_24h"] >= min_volume]
    top = sorted(liquid, key=lambda q: q["apr"], reverse=True)
    majors = {c: {v: venues[v][c]["apr"] for v in venues if c in venues[v]} for c in ("BTC", "ETH", "SOL")}
    stocks = sorted([dict(coin=c, **q) for c, q in binance.items() if q.get("kind") not in ("COIN", None)],
                    key=lambda q: q["volume_24h"], reverse=True)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "errores": errors,
        "mayores": majors,
        "top_funding": top[:25],
        "bottom_funding": top[-10:][::-1],
        "diferencias": spreads[:25],
        "perpetuos_no_cripto": stocks[:40],
        "conteo": {v: len(q) for v, q in venues.items()},
    }


# ══════════ Bitso ══════════

def bitso_premium() -> Dict:
    """BTC y ETH en pesos (Bitso) contra el precio global (Binance) convertido con USDT/MXN de Bitso."""
    def ticker(book):
        return _get(f"https://api.bitso.com/v3/ticker/?book={book}")["payload"]
    usdt = ticker("usdt_mxn")
    usd = ticker("usd_mxn")
    usdt_mxn = float(usdt["last"])
    out = {"ts": datetime.now(timezone.utc).isoformat(), "usdt_mxn": usdt_mxn, "usd_mxn": float(usd["last"]), "pares": []}
    for book, sym in (("btc_mxn", "BTCUSDT"), ("eth_mxn", "ETHUSDT")):
        t = ticker(book)
        bn = _get(f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={sym}")
        global_mxn = float(bn["bidPrice"]) * usdt_mxn
        bid, ask = float(t["bid"]), float(t["ask"])
        out["pares"].append({
            "par": book, "bitso_bid": bid, "bitso_ask": ask, "global_mxn": global_mxn,
            "prima_pct": ((bid + ask) / 2 / global_mxn - 1) * 100,
            "spread_bitso_pct": (ask / bid - 1) * 100,
        })
    # USDT en pesos contra el dólar en pesos: el "dólar cripto" a veces cotiza con prima
    out["prima_usdt_vs_usd_pct"] = (usdt_mxn / float(usd["last"]) - 1) * 100
    return out


# ══════════ Mercados de predicción ══════════

def polymarket_top(limit: int = 30) -> Dict:
    rows = _get(f"https://gamma-api.polymarket.com/markets?closed=false&active=true&limit={limit}"
                f"&order=volume24hr&ascending=false")
    markets = []
    for m in rows:
        try:
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
            outcomes = json.loads(m.get("outcomes") or "[]")
        except Exception:
            prices, outcomes = [], []
        markets.append({
            "pregunta": m.get("question"), "slug": m.get("slug"), "cierra": m.get("endDate"),
            "resultados": outcomes, "precios": prices, "suma_precios": sum(prices) if prices else None,
            "volumen_24h": float(m.get("volume24hr") or 0), "liquidez": float(m.get("liquidityNum") or m.get("liquidity") or 0),
            "mejor_bid": m.get("bestBid"), "mejor_ask": m.get("bestAsk"), "spread": m.get("spread"),
        })
    return {"ts": datetime.now(timezone.utc).isoformat(), "mercados": markets}


def collect_all() -> Dict:
    out = {}
    for name, fn in (("funding", funding_snapshot), ("bitso", bitso_premium), ("polymarket", polymarket_top)):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e), "ts": datetime.now(timezone.utc).isoformat()}
    return out


if __name__ == "__main__":
    snap = collect_all()
    f = snap["funding"]
    print("Conteo:", f.get("conteo"), "errores:", f.get("errores"))
    print("Mayores (APR):", {c: {v: f"{a * 100:.1f}%" for v, a in q.items()} for c, q in f.get("mayores", {}).items()})
    for d in f.get("diferencias", [])[:8]:
        print(f"  {d['coin']:8} corto en {d['short_en']:11} {d['apr_alto'] * 100:7.1f}%  largo en {d['long_en']:11} "
              f"{d['apr_bajo'] * 100:7.1f}%  diferencia {d['diferencia_apr'] * 100:6.1f}%")
    b = snap["bitso"]
    print("Bitso:", b.get("pares"), "USDT vs USD:", b.get("prima_usdt_vs_usd_pct"))
    print("Polymarket:", [(m["pregunta"][:50], m["precios"]) for m in snap["polymarket"].get("mercados", [])[:3]])
