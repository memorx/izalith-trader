# Izalith Trader

Bot de trading personal: un **laboratorio** que prueba estrategias con datos reales y
costos reales, y un **bot en vivo** que corre en papel (o con dinero real) sólo las que
pasan el laboratorio. Más un panel para verlo todo.

La regla del proyecto: **ninguna estrategia toca dinero sin pasar primero por el
laboratorio y luego por un mes en papel.**

## Arranque rápido

```bash
cd izalith-trader
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # Windows
cp .env.example .env                                        # y edítalo

# 1. Evaluar todas las estrategias (baja datos la primera vez; ~10 min)
.venv\Scripts\python -m lab.review

# 1b. ¿Qué queda con poco capital y las comisiones de cada plataforma?
.venv\Scripts\python -m lab.small_account

# 2. Correr en papel lo que pasó + recolectores + panel
.venv\Scripts\python run.py --modules allocator,collectors,signals,dashboard

# 3. Abrir el panel
#    http://127.0.0.1:8765
```

Sin ninguna llave ya funciona en papel: cripto con precios públicos del exchange y
acciones con precios de Yahoo. Para dinero real hacen falta cuentas (ver abajo).

## Qué hay

| Parte | Para qué |
|---|---|
| `lab/` | Backtests con comisión, deslizamiento y funding. Catálogo de 39 estrategias de seguras a arriesgadas (11 de ellas inventadas aquí), con sus parámetros de libro (sin optimizar) y 6 pruebas de robustez más candados (historial, número de operaciones, sesgos conocidos y, para las inventadas, la prueba de "no fue suerte"): acciones de EE.UU., cripto, carry de funding y **33 países** (Latinoamérica, Asia, África, Medio Oriente, Europa) vía ETFs de país. `python -m lab.review` escribe `data/lab/report.json` y `docs/ESTRATEGIAS.md`. |
| `lab/small_account.py` | Las mismas estrategias con cuentas de 100, 200, 1,000 y 5,000 dólares y las comisiones reales de Binance, NDAX, Bitso, Kraken (límite, suponiendo que 70% se llena esperando, y mercado) e IBKR (con su dólar mínimo por orden). |
| `lab/polymarket_study.py` | ¿Los precios de Polymarket dicen la verdad? Mercados ya resueltos, foto 7 días antes de la fecha programada de cierre. |
| `modules/allocator` | Corre en vivo las estrategias de portafolio del laboratorio con **el mismo código** del backtest; cada una con su propio capital. En cripto usa órdenes límite que esperan en el libro (mitad de comisión en Kraken). No rebalancea si a la decisión le falta un activo o tiene datos viejos, y en real no corre estrategias que mezclan cuentas de brokers distintos. |
| `modules/collectors` | Fotos de mercados poco vigilados cada 15 min: funding de Binance, Bybit y Hyperliquid y sus diferencias, perpetuos de acciones y oro 24/7, prima del peso en Bitso, **dólar cripto y BTC en 16 países** (Argentina, Venezuela, India, Corea, Turquía, Nigeria, Sudáfrica...), Polymarket. Guarda historial por hora. |
| `modules/reports` | Resumen diario por Telegram, alertas cuando algo requiere atención (un módulo se cayó, una orden sin registrar, una fuente sin conexión) y latido para healthchecks.io. |
| `modules/signals` | Alertas a Telegram/Discord. Nunca manda alertas calculadas con precios inventados. |
| `modules/dca` | Compras periódicas por monto, en la zona horaria configurada, sin repetir un turno tras un reinicio. |
| `modules/grid` | Rejilla de compras/ventas. Se niega a arrancar si cada vuelta pierde por comisiones. |
| `modules/scalper` | Las estrategias intradía originales. **En rojo en el laboratorio**: sólo papel. |
| `modules/dashboard` | Panel local: resumen, mapa de riesgo de las estrategias, lo que corre en vivo, mercados poco vigilados y dónde abrir cuenta. |

## El semáforo del laboratorio

Cada estrategia se califica con datos de años y después de costos:

1. le gana al efectivo (T-bill de EE.UU.)
2. también en el último 40% del historial, que no se usó para diseñarla
3. aguanta el doble de comisiones
4. aguanta operar un día tarde
5. funciona con parámetros vecinos (no es suerte de un número)
6. nunca cayó más de 85% (más que eso es quiebra o liquidación)

Además hay candados: al menos 3 años de historial, 30 operaciones en las de señales, y un sesgo
conocido (supervivencia en altcoins, o piezas elegidas por haber salido verdes en los combinados)
deja la estrategia en amarillo como máximo. Las inventadas además tienen que pasar "no fue suerte".

Verde = pasa todo. Amarillo = casi. Rojo = no sirve como está.

Resultados del 23 oct 2026: ver `docs/ESTRATEGIAS.md` o la pestaña Estrategias del panel.

## Seguridad del dinero

- `DRY_RUN=true` por defecto: órdenes de papel con precios reales y comisiones simuladas. Cada
  mercado tiene su propio interruptor (`CRYPTO_DRY_RUN`, `STOCKS_DRY_RUN`, `FOREX_DRY_RUN`).
- Para operar en real hacen falta dos llaves (`LIVE_CONFIRMED=true` y `REAL_CAPITAL_USD`); cada
  arranque en real avisa por Telegram y la historia real va en otra base (`izalith_real.db`).
- Con dinero real **nunca** se usan precios inventados; si una fuente falla, no hay dato y no se opera.
- Cada orden se anota antes de mandarla con un id propio (UUID). Si el bot se cae a media orden o
  la red falla, le pregunta al exchange qué pasó antes de mandar otra: si sigue viva espera, si se
  llenó la registra, y si no se puede saber no manda nada. Al arrancar (y cada 5 minutos con las
  que llevan 30 minutos sin moverse) cancela las que quedaron vivas y anota lo que sí se llenó.
- Una orden límite que no se puede confirmar como cancelada detiene todo lo demás de esa orden:
  nunca se manda "el resto a mercado" encima de una orden que podría seguir viva.
- Freno de emergencia: si lo que maneja el bot cae más de `MAX_DRAWDOWN_PCT` desde su máximo, sólo
  se permiten órdenes que reducen la exposición. Se quita a mano con `python -m tools.freno --quitar`.
- Las posiciones, stops y P&L viven en la base de datos: un reinicio no deja posiciones huérfanas.
- El panel sólo escucha en esta computadora y no tiene botones que operen.
- El papel cobra las comisiones reales de cada exchange (Kraken 0.40%/0.80%, Binance 0.10%...) y en
  acciones el dólar mínimo por orden de IBKR, para que el papel no salga más bonito que la realidad.
- Las llaves de API van en `.env` (nunca en git), y sólo con permiso de operar, no de retirar.

## Dónde abrir cuenta (resumen)

- **Si vives en México:** Binance (cripto) + Interactive Brokers (acciones, ETFs, BMV, forex).
  Bitso como entrada en pesos.
- **Si vives en Canadá:** Kraken (`CRYPTO_EXCHANGE=kraken`) + IBKR Canada. Sin perpetuos.
- **Si pasas medio año en cada uno:** Kraken Pro + IBKR, que funcionan igual desde los dos países.
  La cuenta va a nombre de la única residencia fiscal (la decide el tratado; confírmalo con tu contador).

Detalle, comisiones, impuestos y lo que falta confirmar: pestaña "Dónde abrir cuenta" del
panel (`data/lab/venues.json`).

## Pendiente

- Probar el adaptador de **Interactive Brokers** contra IB Gateway de verdad (hoy sólo está
  probado con un IBKR falso en las pruebas). Lo mismo las órdenes de Kraken: probadas contra un
  Kraken falso, todavía no contra la cuenta real.
- Adaptador de **perpetuos** (Binance USD-M / Hyperliquid) para correr en vivo el carry y las
  estrategias con cortos.
- Backtests de los mercados en investigación cuando los recolectores junten historial.

## Portafolios combinados

`combo_verdes` (las 7 verdes) y `combo_chico` (tendencia BTC+ETH + tendencia del S&P, para cuentas
chicas) reparten capital según el riesgo de cada pieza y rebalancean cada mes. En el laboratorio
caen algo menos que cada estrategia sola, pero las piezas se eligieron por haber salido verdes, así
que el resultado es optimista (quedan en amarillo como máximo). Mezclan cripto y acciones: en papel
se corren como cualquier otra (`ALLOCATOR_STRATEGIES=combo_chico`); en real no, porque cada cuenta
tiene su propio efectivo.

## Termómetros

En "Mercados poco vigilados" del panel: curva de tasas de EE.UU., miedo crediticio (HYG/IEF), VIX,
dólar, tasas del Banco de Canadá y de Banxico (con `BANXICO_TOKEN`), stablecoins en circulación
(DefiLlama), volatilidad esperada de BTC y ETH (Deribit) y apalancamiento en perpetuos (Binance).

## Interactive Brokers

`STOCKS_BROKER=ibkr` con TWS o IB Gateway abierto y la API habilitada (`IBKR_PORT`: 4002 papel,
4001 real). Compra por monto con fracciones de acción; si la cuenta no tiene ese permiso, usa
acciones enteras. La comisión se toma del reporte de IBKR; si no llega a tiempo, se anota la
estimación de Pro Fixed (0.005 USD por acción, mínimo 1 USD, máximo 1% del monto). El horario
respeta los feriados y cierres temprano de NYSE (`core/market_calendar.py`, hasta 2027).

## Para el contador

```bash
.venv\Scripts\python -m tools.exportar --anio 2026
```

O el botón "Descargar CSV para el contador" en "En vivo".

## Pruebas

```bash
.venv\Scripts\python -m pytest tests/ -q
```

128 pruebas. Cubren el motor de backtest (sin ver el futuro, costos), los adaptadores, el P&L de
largos y cortos, cierres parciales, efectivo tras reinicio, el día local, órdenes duplicadas,
órdenes que quedan vivas o se llenan tarde, errores de red a media orden (contra un Kraken y un
IBKR falsos), el freno, el modo real, el calendario de NYSE, los candados del allocator, el
horario del DCA y la validación del grid.

## Licencia

Uso personal.
