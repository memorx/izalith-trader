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
| `lab/` | Backtests con comisión, deslizamiento y funding. Catálogo de 26 estrategias de seguras a arriesgadas, con sus parámetros de libro (sin optimizar) y 6 pruebas de robustez: acciones de EE.UU., cripto, carry de funding y **33 países** (Latinoamérica, Asia, África, Medio Oriente, Europa) vía ETFs de país. `python -m lab.review` escribe `data/lab/report.json` y `docs/ESTRATEGIAS.md`. |
| `lab/small_account.py` | Las mismas estrategias con cuentas de 200, 1,000 y 5,000 dólares y las comisiones reales de Binance, NDAX, Bitso, Kraken (límite y mercado) e IBKR (con su dólar mínimo por orden). |
| `modules/allocator` | Corre en vivo las estrategias de portafolio del laboratorio con **el mismo código** del backtest; cada una con su propio capital. En cripto usa órdenes límite que esperan en el libro (mitad de comisión en Kraken). |
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

Verde = pasa todo. Amarillo = casi. Rojo = no sirve como está. Un sesgo conocido de la
prueba (supervivencia en altcoins) deja la estrategia en amarillo como máximo.

Resultados del 22 oct 2026: ver `docs/ESTRATEGIAS.md` o la pestaña Estrategias del panel.

## Seguridad del dinero

- `DRY_RUN=true` por defecto: órdenes de papel con precios reales y comisiones simuladas.
- Con dinero real **nunca** se usan precios inventados; si una fuente falla, no hay dato y no se opera.
- Cada orden se anota antes de mandarla con un id propio. Si el bot se cae a media orden,
  al volver le pregunta al exchange qué pasó en lugar de mandarla otra vez.
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
  probado con un IBKR falso en las pruebas).
- Adaptador de **perpetuos** (Binance USD-M / Hyperliquid) para correr en vivo el carry y las
  estrategias con cortos.
- Backtests de los mercados en investigación cuando los recolectores junten historial.

## Portafolios combinados

`combo_verdes` (las 7 verdes) y `combo_chico` (tendencia BTC+ETH + tendencia del S&P, para cuentas
chicas) reparten capital según el riesgo de cada pieza y rebalancean cada mes. En el laboratorio
caen mucho menos que cada estrategia sola. Se corren en vivo como cualquier otra:
`ALLOCATOR_STRATEGIES=combo_chico`.

## Termómetros

En "Mercados poco vigilados" del panel: curva de tasas de EE.UU., miedo crediticio (HYG/IEF), VIX,
dólar, tasas del Banco de Canadá y de Banxico (con `BANXICO_TOKEN`), stablecoins en circulación
(DefiLlama), volatilidad esperada de BTC y ETH (Deribit) y apalancamiento en perpetuos (Binance).

## Interactive Brokers

`STOCKS_BROKER=ibkr` con TWS o IB Gateway abierto y la API habilitada (`IBKR_PORT`: 4002 papel,
4001 real). Compra por monto con fracciones de acción; si la cuenta no tiene ese permiso, usa
acciones enteras.

## Para el contador

```bash
.venv\Scripts\python -m tools.exportar --anio 2026
```

O el botón "Descargar CSV para el contador" en "En vivo".

## Pruebas

```bash
.venv\Scripts\python -m pytest tests/ -q
```

Cubren el motor de backtest (sin ver el futuro, costos), los adaptadores, el P&L de largos y
cortos, cierres parciales, efectivo tras reinicio, el día local, órdenes duplicadas, el
horario del DCA y la validación del grid.

## Licencia

Uso personal.
