# Bitácora — Izalith Trader

**Qué es:** bot de trading personal de Memo. Tiene un laboratorio que prueba estrategias con datos reales y costos, y un bot en vivo (papel primero) que sólo corre lo que pasa el laboratorio. Más un panel local.
**Dónde vive:** repo `memorx/izalith-trader` (privado, rama `master`), clonado en `C:\Users\guill\dev\trader`; el código está en `izalith-trader/`. Panel local: `http://127.0.0.1:8765`.
**Stack:** Python 3.11 (venv en `izalith-trader/.venv`), asyncio, ccxt, alpaca-py, yfinance, pandas 3, SQLite (aiosqlite), FastAPI + HTML/JS sin librerías.

---

## 📸 ESTADO AL 22/10/2026

*(Esta sección se reescribe completa en cada actualización.)*

**Hecho y funcionando**
- Laboratorio (`lab/`): 21 estrategias en el catálogo, evaluadas con `python -m lab.review` (reporte 2026-10-22 17:18 UTC). 9 en verde, 3 en amarillo, 5 en rojo y 4 en investigación.
  - Verde: 60/40, efectivo+10% BTC, DCA S&P, Faber, momentum dual, RSI-2, tendencia BTC, tendencia BTC+ETH con volatilidad controlada y DCA BTC.
  - Rojo: las 3 del scalper original, BTC 2x (caída de -93%) y carry en altcoins.
- Bot en vivo arreglado y con pruebas: 75 pasan (`pytest tests/`).
- Corrida en papel desde el 22/10 ~12:00 (allocator con trend_voltarget, faber_trend y cash_plus_btc, $1,000 cada una; además recolectores, señales y panel). Tiene 4 posiciones de papel. Tras matar el proceso se recuperaron completas, con el mismo efectivo.
- Panel con 5 pestañas, revisado con capturas en tema claro y oscuro: resumen, mapa de riesgo de las estrategias, en vivo, mercados poco vigilados y dónde abrir cuenta.
- Investigación de plataformas para México y Canadá en `izalith-trader/data/lab/venues.json` (se ve en el panel).

**A medias**
- **Nada commiteado todavía:** todo está en el árbol de trabajo sobre el commit inicial `3da8d1d`. Falta que Memo diga si se commitea y se sube.
- **Corrida en papel:** depende de la sesión de Claude que la lanzó. Para que siga, Memo la tiene que correr con `run.py` desde su terminal o como tarea programada.
- **Adaptador de Interactive Brokers:** no existe. Es la cuenta de acciones recomendada para MX y CA; hoy acciones = Alpaca o Yahoo en papel.
- **Adaptador de perpetuos (Binance USD-M / Hyperliquid):** no existe. Sin él, el carry y las estrategias con cortos no corren en vivo.
- **Recolectores:** guardan historial por hora (tabla `kv`, llaves `collect:AAAA-MM-DDTHH`), pero falta juntar semanas antes de poder hacer backtest de los mercados en investigación.

**Bloqueado**
- **Dónde abrir cuenta:** depende de la residencia fiscal real de Memo (México o Canadá); pendiente desde el 22/10/2026. Al consultar Bybit, la conexión salió como Canadá.
- **Cuánto capital:** sin respuesta de Memo desde el 22/10/2026.

**Siguiente paso**
- Que Memo diga dónde vive de verdad (MX o CA), con cuánto empezaría y si se commitea. Con eso se escribe el adaptador del broker que toque.

---

## 22/10/2026 — Las estrategias del bot perdían dinero; se armó el laboratorio y se rehízo el núcleo

**Qué cambió**
- **Backtest de 1 año de las estrategias originales:** momentum de -27% a -37% neto, breakout con 0 operaciones y mean reversion casi sin operaciones.
- **Laboratorio nuevo** (`lab/`): datos con caché (Binance, funding, Yahoo), motor de pesos con funding y efectivo, motor de señales que reutiliza las clases del scalper, DCA con TIR, semáforo con 6 pruebas, reporte en JSON y en markdown (`docs/ESTRATEGIAS.md`).
- **Auditoría del bot en vivo:** 24 hallazgos. Se arreglaron los que tocan dinero:
  - P&L de los largos con el signo al revés: `buy` se trataba como corto.
  - Límite diario que filtraba por la fecha de apertura y no por la de cierre, y que usaba el día UTC.
  - DCA que caía a compras diarias si la frecuencia venía mal escrita.
  - Grid que perdía en cada vuelta y vendía inventario que nunca compró.
  - Órdenes que se podían duplicar tras un reinicio: ahora se anota la intención antes de mandarla, con `client_order_id` y `purpose`.
  - Posiciones del scalper que vivían sólo en memoria.
  - Precios aleatorios en silencio cuando fallaba la red.
  - Dashboard con CORS abierto y escuchando en 0.0.0.0.
  - Markdown de Telegram que se rompía con `EUR_USD`.
- **Módulos nuevos:**
  - `allocator`: corre en vivo las estrategias del laboratorio con el mismo código.
  - `collectors`: funding de 3 exchanges, perpetuos de acciones y oro, prima de Bitso y Polymarket.
  - `core/execution.py`.
- **Exchange de cripto configurable** (`CRYPTO_EXCHANGE`: binance, kraken o bitso) y precios de Yahoo para acciones en papel sin llaves.

**Por qué**
- Memo quiere un "mega trader" con ganancias constantes y mercados poco vigilados. Sin un laboratorio honesto no hay forma de saber qué estrategia merece dinero.

**Decisiones y descartes**
- **Parámetros de libro, sin optimizar**, más pruebas de robustez: optimizar sobre el mismo historial que se evalúa es engañarse.
- **Semáforo de riesgo de ruina (caída de más de 85% = rojo):** BTC 2x había salido verde con -93%.
- **Tope de amarillo para la rotación de altcoins**, por sesgo de supervivencia.
- **Carry de funding en BTC/ETH en amarillo:** pagó 5.4% anual, pero en el último 40% del historial (2.7%) quedó debajo del efectivo (4.3%). No es la "ganancia constante" que parece.
- **Carry en altcoins:** el primer diseño rotaba cada semana y las comisiones (6.4% al año) se comían el funding (7.4%). Se le puso histéresis; aun así sale en rojo.
- **Grid en USDC/USDT con 10 niveles:** el escalón (0.11%) es menor que dos comisiones; ahora el módulo se niega a arrancar. El par por defecto estaba mal: `USDT/USDC` no existe en Binance, es `USDC/USDT`.
- **Panel en el puerto 8765:** el 8080 lo ocupa otra app de Memo. Antes, que el panel no arrancara tumbaba todo el bot (uvicorn lanza `SystemExit`); ahora el trading sigue.
- **Perpetuos de acciones:** Binance los marca como `TRADIFI_PERPETUAL`, no `PERPETUAL`; por eso salían vacíos al principio.
- **Funding anualizado por intervalo real** (Binance y Bybit cobran cada 1, 4 u 8 h; Hyperliquid cada hora). La primera foto, del primer turno, suponía 8 h para todo.

**Datos de la sesión**
- Tasas (investigación del 22/10/2026): CETES 28 días 6.23% (subasta del 20/10), CETES 364 días 7.11% (13/10), Banxico 6.50%.
- Funding de BTC ese día: Binance 4.6–5.2% anual, Hyperliquid 11%. El perpetuo de oro (XAU) en Binance pagaba 32.6% anual, con unos US$3.3 mil millones de volumen. Son fotos, no historial.
- Desde México, OANDA no da su API v20 (sólo MT5): el adaptador de forex sólo sirve desde Canadá.
