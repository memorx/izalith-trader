# Bitácora — Izalith Trader

**Qué es:** bot de trading personal de Memo. Tiene un laboratorio que prueba estrategias con datos reales y costos, y un bot en vivo (papel primero) que sólo corre lo que pasa el laboratorio. Más un panel local.
**Dónde vive:** repo `memorx/izalith-trader` (privado, rama `master`), clonado en `C:\Users\guill\dev\trader`; el código está en `izalith-trader/`. Panel local: `http://127.0.0.1:8765`.
**Stack:** Python 3.11 (venv en `izalith-trader/.venv`), asyncio, ccxt, alpaca-py, yfinance, pandas 3, SQLite (aiosqlite), FastAPI + HTML/JS sin librerías.

---

## 📸 ESTADO AL 22/10/2026

*(Esta sección se reescribe completa en cada actualización.)*

**Hecho y funcionando**
- Laboratorio (`lab/`): 26 estrategias en el catálogo, evaluadas con `python -m lab.review`. 9 en verde, 7 en amarillo (4 son de países, por sesgo de supervivencia), 5 en rojo y 5 en investigación.
  - Verde: 60/40, efectivo+10% BTC, DCA S&P, Faber, momentum dual, RSI-2, tendencia BTC, tendencia BTC+ETH con volatilidad controlada y DCA BTC.
  - Rojo: las 3 del scalper original, BTC 2x (caída de -93%) y carry en altcoins.
- Análisis de cuenta chica (`python -m lab.small_account`): las mismas estrategias con 200, 1,000 y 5,000 dólares y las comisiones reales de cada plataforma. Se ve en el panel.
- Bot en vivo arreglado y con pruebas: 79 pasan (`pytest tests/`).
  - Órdenes límite pasivas en cripto.
  - El papel cobra las comisiones reales del exchange y el dólar mínimo por orden de IBKR.
- Corrida en papel con el escenario de Memo, desde el 22/10 en la tarde:
  - `.env` local: `DRY_RUN=true`, `CRYPTO_EXCHANGE=kraken`, `PAPER_CASH_USD=200`, trend_voltarget y faber_trend con $100 cada una.
  - La corrida de prueba anterior (con $10,000) quedó guardada en `izalith-trader/data/izalith_prueba_2026-10-22.db`.
- Panel con 5 pestañas, revisado con capturas en tema claro y oscuro.
- Investigación de plataformas para México, Canadá y "medio año en cada país" en `izalith-trader/data/lab/venues.json`.
- En GitHub, rama `feat/laboratorio-y-refinamiento` (sin merge a `master`).

**A medias**
- **Corrida en papel:** depende de la sesión de Claude que la lanzó. Para que siga, Memo la tiene que correr con `run.py` desde su terminal o como tarea programada.
- **Merge a `master`:** pendiente de que Memo lo pida.
- **Adaptador de Interactive Brokers:** no existe. Es la cuenta de acciones recomendada para MX y CA; hoy acciones = Alpaca o Yahoo en papel.
- **Adaptador de perpetuos (Binance USD-M / Hyperliquid):** no existe. Sin él, el carry y las estrategias con cortos no corren en vivo.
- **Recolectores:** guardan historial por hora (tabla `kv`, llaves `collect:AAAA-MM-DDTHH`), pero falta juntar semanas antes de poder hacer backtest de los mercados en investigación.

**Bloqueado**
- **Residencia fiscal de Memo:** pasa unos seis meses en México y seis en Canadá. La residencia la decide el tratado y la confirma su contador; pendiente desde el 22/10/2026. Mientras tanto se eligieron plataformas que sirven en los dos países (Kraken Pro + IBKR).

**Siguiente paso**
- Dejar correr el papel un mes. Si Memo abre Kraken Pro, poner las llaves (sólo permiso de operar) con `DRY_RUN=true` primero.

---

## 22/10/2026 — Países de todo el mundo y cripto en monedas locales

**Qué cambió**
- Memo pidió ver mercados de Latinoamérica, Asia, África ("etc etc etc") y también cripto.
- **Laboratorio:** 4 estrategias con 33 ETFs de país, comprables desde IBKR en México o Canadá. Todas en amarillo por sesgo de supervivencia (no incluyen Rusia, congelada en 2022, ni Colombia, cuyo ETF cerró); pasan las otras pruebas.

  | Estrategia | Rinde al año | Peor caída | Referencia |
  |---|---|---|---|
  | Mundo emergente en partes iguales | 7.0% | -69% | — |
  | Tendencia en cada país emergente | 8.7% | -33% | EWZ: -77% |
  | Tendencia en Latinoamérica | 10.1% | -53% | EWW: 9.1%, -65% |
  | Rotación a los países más fuertes | 9.0% | -55% | SPY: 11.2% |

- **Recolector de dólar cripto y BTC en 16 países.** Foto del 22/10 contra el oficial (open.er-api):

  | Venezuela | Argentina | India | Egipto | Chile | Corea | Sudáfrica | México |
  |---|---|---|---|---|---|---|---|
  | +13.7% | +7.5% | +6.4% | +2.6% | +1.2% | +1.0% | +1.1% | +0.3% |

  Nigeria no regresa datos (Binance P2P vacío).

**Decisiones y descartes**
- **Las primas por país quedan como información, no como estrategia:** aprovecharlas pide residencia, KYC y banco local, y hay controles de capital.
- **Rotación entre países:** no le gana a sólo comprar el S&P en rendimiento. Lo que sirve es la regla de tendencia por país, que corta la peor caída a menos de la mitad.

---

## 22/10/2026 — Con $200 lo que manda es dónde y cómo se opera, no el capital

**Qué cambió**
- Memo respondió:
  - Pasa medio año en cada país: "en los dos lados, literalmente estoy 6 anios y 6 anios en caa pais".
  - Empezaría con unos 200 dólares.
  - Autorizó commit y push.
- **Motor con cuenta chica:** comisión mínima por orden y monto mínimo por orden. Nuevo `lab/small_account.py`.
- **Comisiones verificadas en la página oficial** (no en ccxt, que trae valores viejos): Kraken Pro, nivel $0+, 0.40% maker y 0.80% taker. Bitso en pares USDT, 0.30% / 0.36%.
- **Órdenes límite pasivas** (`postOnly`): se recolocan hasta 3 veces; lo que no se llene en 15 minutos va a mercado. El allocator las usa en cripto.
- **El papel cobra las comisiones reales de cada exchange** y el dólar mínimo de IBKR en acciones.

**Por qué**
- Con $200 en cripto, el tamaño casi no cambia los porcentajes, pero el lugar sí. Tendencia BTC+ETH con volatilidad controlada:

  | Binance | NDAX | Kraken límite | Kraken mercado |
  |---|---|---|---|
  | 21.2% | 20.3% | 18.9% | 15.3% |

- En acciones, el dólar mínimo de IBKR deja al RSI-2 en 2.6% (contra 7.0% en el laboratorio). Faber casi no cambia (9.5%) porque opera poco.

**Decisiones y descartes**
- **Kraken Pro + IBKR** porque sirven en los dos países. Se descartaron:
  - Binance: no opera en Canadá, y una cuenta mexicana usada desde Canadá se arriesga a un bloqueo por IP.
  - NDAX: sólo residentes de Canadá.
- **No se recomienda saltarse bloqueos por país con VPN o servidores** (Hyperliquid y otros lo prohíben en sus términos).

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
