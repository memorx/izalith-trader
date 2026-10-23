# Bitácora — Izalith Trader

**Qué es:** bot de trading personal de Memo. Tiene un laboratorio que prueba estrategias con datos reales y costos, y un bot en vivo (papel primero) que sólo corre lo que pasa el laboratorio. Más un panel local.
**Dónde vive:** repo `memorx/izalith-trader` (privado, rama `master`), clonado en `C:\Users\guill\dev\trader`; el código está en `izalith-trader/`. Panel local: `http://127.0.0.1:8765`.
**Stack:** Python 3.11 (venv en `izalith-trader/.venv`), asyncio, ccxt, alpaca-py, yfinance, pandas 3, SQLite (aiosqlite), FastAPI + HTML/JS sin librerías.

---

## 📸 ESTADO AL 23/10/2026

*(Esta sección se reescribe completa en cada actualización.)*

**Hecho y funcionando**
- Laboratorio (`lab/`): 39 estrategias, evaluadas con `python -m lab.review` (corrida del 23/10). 8 en verde: 60/40, efectivo + 10% BTC, DCA S&P, Faber, momentum dual, RSI-2, tendencia BTC y tendencia BTC+ETH con volatilidad controlada.
  - En amarillo por las reglas corregidas: los dos combinados (piezas elegidas por salir verdes) y el DCA de BTC (caída de -83%).
  - Las 11 inventadas: ninguna pasa "no fue suerte" (contando 82 variantes probadas).
- Cuenta chica (`python -m lab.small_account`): ahora con 100, 200, 1,000 y 5,000 dólares. Tendencia BTC+ETH con $200: 18.0% al año en Kraken con órdenes límite (70% maker) contra 21.3% en el laboratorio.
- Estudio de Polymarket (`python -m lab.polymarket_study`): comprar al favorito no tiene ventaja (entre -6.1% y +2.7% por apuesta, 95% de confianza).
- Bot en vivo con la revisión de Fable aplicada (ver la entrada del 23/10). 121 pruebas pasan (`pytest tests/`).
- Arranque automático al iniciar sesión (carpeta Inicio de Windows); `tools/detener.bat` lo apaga en orden.
- Corrida en papel de Memo: `.env` con `DRY_RUN=true`, Kraken, `PAPER_CASH_USD=200`, trend_voltarget y faber_trend con $100 cada una. La corre Memo en su terminal; base `data/izalith.db`.
- Rama `feat/laboratorio-y-refinamiento` en GitHub, con PR a `master`: https://github.com/memorx/izalith-trader/pull/1 (abierto el 23/10, sin merge).

**A medias**
- **Corrida en papel:** para que el mes cuente, Memo tiene que reiniciar el bot con el código del 23/10. Antes de eso, el papel no podía vender después de un reinicio.
- **Adaptador de perpetuos (Binance USD-M / Hyperliquid):** no existe. Sin él, el carry y las estrategias con cortos no corren en vivo.
- **IBKR y Kraken en real:** los dos adaptadores sólo están probados contra versiones falsas. Falta una prueba con la cuenta de papel de IBKR (puerto 4002) y una orden chica en Kraken.
- **Recolectores:** guardan historial por hora (tabla `kv`, llaves `collect:AAAA-MM-DDTHH`); faltan semanas de datos para hacer backtest de los mercados en investigación.
- **Pendientes de Memo:** crear el bot de Telegram (BotFather) y el check de healthchecks.io.

**Bloqueado**
- **Residencia fiscal:** (nota personal retirada)

**Siguiente paso**
- Que Memo revise y haga merge del PR, reinicie el bot y deje correr el papel un mes.

---

## 23/10/2026 — Fable revisó todo: órdenes que podían duplicarse y el freno midiendo el papel

**Qué cambió**
- Memo pidió que Fable revisara el proyecto y luego arreglar lo que dijera. Fable encontró 30 problemas y una lista de afirmaciones exageradas. Se arreglaron todos.
- **Lo más grave (órdenes):**
  - Ids de orden de 23 caracteres: Kraken acepta máximo 18 o un UUID. Ahora todos son UUID y las sub-órdenes usan UUID derivados (`uuid5`).
  - La orden pasiva de Kraken podía quedar huérfana o duplicarse si `create_order` fallaba después de entrar o si la cancelación no se confirmaba. Ahora se pregunta antes de reintentar y, si algo no se puede confirmar, no se manda nada más ("en_vuelo").
  - Una orden aceptada pero sin llenar (IBKR "Submitted", Alpaca "accepted") quedaba como "duplicate" para siempre. Ahora se vuelve a preguntar al exchange: si sigue viva espera, si se llenó la registra el módulo y si murió se manda otra.
  - La reconciliación marcaba como fallidas órdenes vivas sin cancelarlas. Ahora las cancela, anota lo que se llenó y corre también cada 5 minutos.
- **Lo más grave (dinero real):**
  - El freno se medía contra el capital de papel aun en real. Ahora real exige `REAL_CAPITAL_USD`.
  - `DRY_RUN=false` para usar IBKR prendía Kraken en real. Ahora hay un interruptor por mercado y real exige además `LIVE_CONFIRMED=true`.
  - La base de real va aparte (`izalith_real.db`).
  - Una falla de red al bajar un símbolo cambiaba en silencio los pesos de la estrategia. Ahora se usa el caché y el allocator no opera si le falta un activo o los datos están viejos.
- **El papel no podía vender después de un reinicio:** el saldo vivía en memoria. Ahora sale del portafolio.
- **Laboratorio:** "W-MON" marcaba martes; los combinados quedan en amarillo como máximo; el DCA con caídas de más de 60% también; la prueba de suerte cuenta 82 variantes en lugar de 11 ideas.
- **Polymarket:** la foto se tomaba 7 días antes del cierre real, que depende del resultado. Ahora se toma 7 días antes de la fecha programada y se excluyen los 16,031 mercados resueltos antes de tiempo. El resultado no cambió: no hay ventaja.

**Por qué**
- Con dinero real, una orden duplicada o un freno que mide otra cuenta cuestan dinero de verdad. Era lo que faltaba antes de poder pasar a real.

**Decisiones y descartes**
- Ante la duda, una orden queda "en vuelo" y no se repite: se prefiere una orden de menos a una doble.
- Si el exchange no conoce una orden de hace más de 12 h, queda para revisar a mano ("revisar") en vez de suponer que no entró.
- En real no se corren estrategias que mezclan cuentas (cripto en Kraken + ETFs en IBKR): cada cuenta tiene su efectivo y la bolsa de la estrategia no puede moverlo. En papel sí.
- Afirmaciones corregidas en el README: "caen mucho menos" pasó a "algo menos"; el número de estrategias, de pruebas y de candados; y qué hace la reconciliación.

---

## 22/10/2026 (noche) — Freno de emergencia, papel contra laboratorio, Polymarket y arranque automático

**Qué cambió**
- **Freno de emergencia** (`core/risk_guard.py`): si lo que maneja el bot cae más de `MAX_DRAWDOWN_PCT` desde su máximo, sólo deja cerrar. Sobrevive reinicios; se quita con `python -m tools.freno --quitar`.
- **Candado de una sola copia del bot** (puerto 47651).
- **Seguimiento** papel contra laboratorio por estrategia, en el panel.
- **Guía de llaves** (`izalith-trader/docs/LLAVES.md`): sólo permiso de operar, nunca de retirar, con lista de IPs permitidas.
- **4 inventadas más:** los termómetros como filtro (crédito, curva, stablecoins, pánico). Ninguna mejora a su base.
- **Estudio de Polymarket:** ¿los favoritos ganan más de lo que dice su precio? No.
- **Arranque automático** con la carpeta Inicio de Windows (`schtasks` dio "Access is denied").

**Decisiones y descartes**
- La primera muestra de Polymarket (los 3,000 de más volumen) estaba sesgada: las sorpresas juntan volumen. Se cambió por una muestra al azar.

---

## 22/10/2026 — Portafolios combinados, termómetros, alertas e IBKR

**Qué cambió**
- **Portafolios combinados** (`combo_verdes`, `combo_chico`): capital según el riesgo de cada pieza y rebalanceo mensual. Los dos salieron en verde, con datos de 2017 a 2026.

  | | Rinde al año | Peor caída | Peor año | Meses en positivo |
  |---|---|---|---|---|
  | Las 7 verdes | 11.0% | -15% | -9% | 69% |
  | Chico (cripto + S&P) | 15.2% | -22% | -20% (2022) | 63% |
  | SPY en el mismo periodo | 15.1% | -34% | | |

- **Termómetros** en los recolectores y en el panel: curva de tasas, HYG/IEF, VIX, DXY, Banco de Canadá, Banxico (con token), stablecoins (DefiLlama), DVOL de Deribit, apalancamiento y long/short de Binance. Las series históricas quedan en `data/cache`.
- **Módulo `reports`:**
  - Resumen diario por Telegram (`REPORT_HOUR`).
  - Alertas sin repetir.
  - Latido para healthchecks.io (`HEALTHCHECK_URL`).
- **`tools/exportar.py` y botón de CSV** para el contador.
- **Adaptador de IBKR** (`core/adapters/ibkr.py`, `ib_async`). Probado sólo con un IBKR falso.

**Decisiones y descartes**
- **FRED no contestó desde la red de Memo** (timeout de 90 s). Se usan equivalentes de Yahoo (^TNX - ^IRX, HYG/IEF) y FRED queda como primera opción si vuelve.
- **Coinglass descartado:** ya pide llave. Se usa lo público de Binance, que sólo guarda 30 días.
- **Combinaciones con optimismo heredado:** las piezas se eligieron por haber salido verdes. Se anota en el aviso de cada una.
- **Pendiente de Memo para las alertas:**
  - Crear un bot de Telegram (BotFather) y un check en healthchecks.io; Claude no crea cuentas.
  - Reiniciar el bot para que tome los módulos nuevos.

---

## 22/10/2026 — Siete estrategias inventadas, ninguna pasa la prueba de "no fue suerte"

**Qué cambió**
- Memo pidió "invéntate estrategias secretas". Se escribieron 7 en `lab/secretas.py` con su razón económica, sus parámetros y su versión base fijados ANTES de probarlas.
- Prueba nueva: Sharpe deflactado (Bailey y López de Prado, 2014) del rendimiento contra la versión base, contando las 7 ideas. Para pasar hace falta 95%. También una regla de 3 años mínimos de historial.

| Estrategia | Rinde al año | Contra su base | Probabilidad |
|---|---|---|---|
| BTC contra ETH | 41.3% (caída -71%) | +23.6% sobre la tendencia BTC | 54% |
| Capitulación | 14.0% | Últimos años: -2.2% | 49% |
| Cobre contra oro | 9.0% | +0.6% sobre el 60/40 | 13% |
| Freno de euforia | 14.7% | -4.3% contra la tendencia BTC+ETH | 0% |
| Países con moneda fuerte | 9.0% | Igual a la rotación normal | 6% |
| Peso como semáforo | 2.2% | -7.9% | 0% |
| Carry de oro | 0.2% | El funding de PAXG fue casi siempre bajo; el 32% de la foto era del perpetuo XAU | 0% |

**Por qué**
- Si se inventan varias ideas y se escoge la que mejor salió, lo que se encuentra es ruido. La prueba existe para no venderle a Memo una "estrategia secreta" que sólo fue suerte.

**Decisiones y descartes**
- La primera versión comparaba contra todas las estrategias del catálogo y contra el efectivo, y dio 0% para todas. Era un error: las del scalper (Sharpe de -7.8) y el carry (+3.4) inflaban la varianza. Se corrigió para comparar cada idea contra su versión base y con la varianza que tendría el azar.
- Ninguna inventada se pone a correr. BTC contra ETH queda en observación: rindió más, pero con -71% de caída y sin significancia.

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
