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


# ══════════ Cripto en el mundo: dólar cripto y BTC en moneda local ══════════
#
# En cada país se compara el precio local de USDT y de BTC contra el tipo de cambio oficial
# y el precio global de BTC. Donde hay controles de cambio (Argentina, Nigeria, Turquía...)
# el dólar cripto cotiza con prima; en Corea BTC suele cotizar arriba ("prima kimchi").

def _median(xs):
    xs = sorted(x for x in xs if x)
    return xs[len(xs) // 2] if xs else None


def _bitso(book):
    t = _get(f"https://api.bitso.com/v3/ticker/?book={book}")["payload"]
    return (float(t["bid"]) + float(t["ask"])) / 2


def _buda(market):
    return float(_get(f"https://www.buda.com/api/v2/markets/{market}/ticker")["ticker"]["last_price"][0])


def _p2p(fiat):
    """Mediana de los 10 mejores anuncios de venta de USDT en Binance P2P (lo que cuesta comprar)."""
    data = _get("https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
                {"fiat": fiat, "page": 1, "rows": 10, "tradeType": "BUY", "asset": "USDT", "payTypes": []})["data"]
    return _median([float(a["adv"]["price"]) for a in data])


def _upbit(market):
    return float(_get(f"https://api.upbit.com/v1/ticker?markets={market}")[0]["trade_price"])


def _btcturk(pair):
    return float(_get(f"https://api.btcturk.com/api/v2/ticker?pairSymbol={pair}")["data"][0]["last"])


def _mb(symbol):
    t = _get(f"https://api.mercadobitcoin.net/api/v4/tickers?symbols={symbol}")[0]
    return (float(t["buy"]) + float(t["sell"])) / 2


def _coindcx(market):
    for t in _get("https://api.coindcx.com/exchange/ticker"):
        if t["market"] == market:
            return float(t["last_price"])
    return None


# (país, región, moneda, fuente del dólar cripto, fuente de BTC)
PAISES_CRIPTO = [
    ("México", "Latinoamérica", "MXN", ("Bitso", lambda: _bitso("usdt_mxn")), ("Bitso", lambda: _bitso("btc_mxn"))),
    ("Argentina", "Latinoamérica", "ARS", ("Binance P2P", lambda: _p2p("ARS")), ("Bitso", lambda: _bitso("btc_ars"))),
    ("Brasil", "Latinoamérica", "BRL", ("Mercado Bitcoin", lambda: _mb("USDT-BRL")), ("Mercado Bitcoin", lambda: _mb("BTC-BRL"))),
    ("Colombia", "Latinoamérica", "COP", ("Bitso", lambda: _bitso("usdt_cop")), ("Buda", lambda: _buda("btc-cop"))),
    ("Chile", "Latinoamérica", "CLP", ("Buda (USDC)", lambda: _buda("usdc-clp")), ("Buda", lambda: _buda("btc-clp"))),
    ("Perú", "Latinoamérica", "PEN", ("Binance P2P", lambda: _p2p("PEN")), ("Buda", lambda: _buda("btc-pen"))),
    ("Venezuela", "Latinoamérica", "VES", ("Binance P2P", lambda: _p2p("VES")), None),
    ("Corea", "Asia", "KRW", ("Upbit", lambda: _upbit("KRW-USDT")), ("Upbit", lambda: _upbit("KRW-BTC"))),
    ("India", "Asia", "INR", ("CoinDCX", lambda: _coindcx("USDTINR")), ("CoinDCX", lambda: _coindcx("BTCINR"))),
    ("Filipinas", "Asia", "PHP", ("Binance P2P", lambda: _p2p("PHP")), None),
    ("Vietnam", "Asia", "VND", ("Binance P2P", lambda: _p2p("VND")), None),
    ("Turquía", "Medio Oriente", "TRY", ("BtcTurk", lambda: _btcturk("USDTTRY")), ("BtcTurk", lambda: _btcturk("BTCTRY"))),
    ("Egipto", "África", "EGP", ("Binance P2P", lambda: _p2p("EGP")), None),
    ("Nigeria", "África", "NGN", ("Binance P2P", lambda: _p2p("NGN")), None),
    ("Kenia", "África", "KES", ("Binance P2P", lambda: _p2p("KES")), None),
    ("Sudáfrica", "África", "ZAR", ("VALR", lambda: float(_get("https://api.valr.com/v1/public/USDTZAR/marketsummary")["lastTradedPrice"])),
     ("Luno", lambda: float(_get("https://api.luno.com/api/1/ticker?pair=XBTZAR")["last_trade"]))),
]


def country_premiums() -> Dict:
    fx = _get("https://open.er-api.com/v6/latest/USD")
    rates = fx["rates"]
    bn = _get("https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT")
    btc_usd = (float(bn["bidPrice"]) + float(bn["askPrice"])) / 2
    rows = []
    for pais, region, cur, usdt_src, btc_src in PAISES_CRIPTO:
        row = {"pais": pais, "region": region, "moneda": cur, "oficial": rates.get(cur)}
        for kind, src in (("usdt", usdt_src), ("btc", btc_src)):
            if not src:
                continue
            name, fn = src
            try:
                val = fn()
            except Exception as e:
                row[f"{kind}_error"] = str(e)[:120]
                continue
            if not val or not row["oficial"]:
                continue
            row[f"{kind}_fuente"] = name
            row[f"{kind}_local"] = val
            ref = row["oficial"] if kind == "usdt" else btc_usd * row["oficial"]
            row[f"prima_{kind}_pct"] = (val / ref - 1) * 100
        rows.append(row)
    return {"ts": datetime.now(timezone.utc).isoformat(), "btc_usd": btc_usd,
            "fx_fecha": fx.get("time_last_update_utc"), "paises": rows}


# ══════════ Termómetros: macro y apalancamiento ══════════

def _last_change(s, days):
    s = s.dropna()
    if s.empty:
        return None, None
    last = float(s.iloc[-1])
    past = s[s.index <= s.index[-1] - __import__("pandas").Timedelta(days=days)]
    return last, (float(past.iloc[-1]) if len(past) else None)


def thermometers(banxico_token: str = "") -> Dict:
    """
    Lo que dice el mundo hoy, en una foto. Cada lectura trae su valor y cómo estaba hace 30 días.
    Las series largas salen de lab.data (con caché); el apalancamiento de cripto, de Binance.
    """
    from lab import data as D
    out = {"ts": datetime.now(timezone.utc).isoformat(), "lecturas": [], "errores": {}}

    def add(key, nombre, unidad, fn, days=30, lectura=None):
        try:
            last, prev = _last_change(fn(), days)
            out["lecturas"].append({"key": key, "nombre": nombre, "unidad": unidad, "valor": last,
                                    "hace_30d": prev, "lectura": lectura(last, prev) if lectura and last is not None else None})
        except Exception as e:
            out["errores"][key] = str(e)[:120]

    add("curva", "Curva de tasas EE.UU. (10 años - 3 meses)", "puntos", D.yield_curve,
        lectura=lambda v, p: "invertida: históricamente avisa recesión" if v < 0 else "normal")
    add("credito", "Bonos de alto riesgo contra Tesoro (HYG/IEF)", "índice", D.credit_stress,
        lectura=lambda v, p: None if p is None else ("se estresa" if v < p * 0.97 else "tranquilo"))
    add("vix", "VIX (miedo en acciones)", "puntos", lambda: D.load_yf("^VIX", "2000-01-01")["close"],
        lectura=lambda v, p: "miedo alto" if v > 25 else "calma" if v < 15 else "normal")
    add("dolar", "Índice del dólar (DXY)", "puntos", lambda: D.load_yf("DX-Y.NYB", "2000-01-01")["close"])
    add("boc", "Tasa del Banco de Canadá", "%", D.boc_series)
    if banxico_token:
        add("banxico", "Tasa objetivo de Banxico", "%", lambda: D.banxico_series("SF61745", banxico_token))
    add("stablecoins", "Stablecoins en circulación", "USD", D.stablecoin_supply,
        lectura=lambda v, p: None if p is None else ("entra dinero a cripto" if v > p * 1.01 else
                                                      "sale dinero de cripto" if v < p * 0.99 else "estable"))
    add("dvol_btc", "Volatilidad esperada de BTC (DVOL)", "%", lambda: D.deribit_dvol("BTC"),
        lectura=lambda v, p: "el mercado espera movimientos fuertes" if v > 60 else "calma" if v < 40 else "normal")
    add("dvol_eth", "Volatilidad esperada de ETH (DVOL)", "%", lambda: D.deribit_dvol("ETH"))

    # Apalancamiento en perpetuos: Binance sólo guarda 30 días de historia
    for sym in ("BTCUSDT", "ETHUSDT"):
        try:
            oi = _get(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={sym}&period=1d&limit=30")
            ls = _get(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1d&limit=2")
            v, p = float(oi[-1]["sumOpenInterestValue"]), float(oi[0]["sumOpenInterestValue"])
            out["lecturas"].append({"key": f"oi_{sym}", "nombre": f"Apalancamiento abierto en {sym[:-4]} (Binance)",
                                    "unidad": "USD", "valor": v, "hace_30d": p,
                                    "lectura": "sube el apalancamiento" if v > p * 1.15 else
                                    "se desapalanca" if v < p * 0.85 else "estable"})
            r = float(ls[-1]["longShortRatio"])
            out["lecturas"].append({"key": f"ls_{sym}", "nombre": f"Cuentas en largo por cada una en corto, {sym[:-4]}",
                                    "unidad": "x", "valor": r, "hace_30d": None,
                                    "lectura": "muy cargado al alza" if r > 2.5 else "cargado a la baja" if r < 0.8 else "parejo"})
        except Exception as e:
            out["errores"][f"oi_{sym}"] = str(e)[:120]
    return out


def thermometer_history(years: int = 3) -> Dict:
    """Series de los últimos años para las gráficas del panel (una muestra por semana)."""
    import pandas as pd
    from lab import data as D
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365 * years)
    series = {}
    for key, fn in (("curva", D.yield_curve), ("credito", D.credit_stress),
                    ("stablecoins", D.stablecoin_supply), ("dvol_btc", lambda: D.deribit_dvol("BTC"))):
        try:
            s = fn()
            s = s[s.index >= since].resample("W").last().dropna()
            series[key] = [[ts.strftime("%Y-%m-%d"), round(float(v), 4)] for ts, v in s.items()]
        except Exception as e:
            series[key] = {"error": str(e)[:120]}
    return series


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


def kraken_spreads(pairs=("XBTUSD", "ETHUSD", "PAXGUSD")) -> Dict:
    """
    Spread de Kraken (USD) en este momento. Idea N6 de Fable (24 oct 2026): juntar 4 semanas por
    hora para fijar la hora del rebalanceo semanal donde el spread es más chico.
    """
    data = _get("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(pairs))
    out = {"ts": datetime.now(timezone.utc).isoformat(), "pares": {}}
    for name, t in (data.get("result") or {}).items():
        bid, ask = float(t["b"][0]), float(t["a"][0])
        mid = (bid + ask) / 2
        out["pares"][name] = {"bid": bid, "ask": ask, "spread_bps": (ask - bid) / mid * 1e4 if mid else None}
    return out


def collect_all() -> Dict:
    out = {}
    for name, fn in (("funding", funding_snapshot), ("bitso", bitso_premium), ("paises", country_premiums),
                     ("polymarket", polymarket_top), ("kraken_spreads", kraken_spreads)):
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
    for r in snap["paises"].get("paises", []):
        print(f"  {r['pais']:10} {r['moneda']}  dólar cripto {r.get('prima_usdt_pct', float('nan')):+6.2f}%  "
              f"BTC {r.get('prima_btc_pct', float('nan')):+6.2f}%  {r.get('usdt_error', '')}{r.get('btc_error', '')}")
