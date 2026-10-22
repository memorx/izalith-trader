# Estrategias: revisión con datos reales

Generado: 2026-10-22 20:05 UTC. Todos los números son **después de comisiones y deslizamiento**.
Se usan los parámetros de libro, sin optimizar. 'Efectivo' es el T-bill de EE.UU. a 3 meses.

Semáforo: 🟢 pasa las 5 pruebas · 🟡 pasa casi todas · 🔴 no sirve como está · 🔬 en investigación.

| | Estrategia | Nivel | Rend. anual | Efectivo | Caída máx. | Sharpe | Fuera de muestra | Comprar y aguantar |
|---|---|---|---|---|---|---|---|---|
| 🟢 | Portafolio 60/40 | Conservadora | +8.5% | +1.7% | -32% | 0.66 | +9.5% | +11.2% / -55% |
| 🟢 | Efectivo con tasa + 10% BTC | Conservadora | +9.9% | +2.6% | -17% | 0.88 | +9.1% | +38.3% / -83% |
| 🟡 | Carry de funding BTC/ETH | Conservadora | +5.4% | +2.8% | -1% | 3.39 | +2.7% | +34.7% / -77% |
| 🟢 | DCA semanal al S&P 500 | Conservadora | +10.8% | +2.4% | -55% | 0.53 | +14.1% | +11.2% / -55% |
| 🟢 | Tendencia S&P 500 (Faber) | Moderada | +9.6% | +2.4% | -24% | 0.59 | +8.4% | +11.2% / -55% |
| 🟢 | Momentum dual (Antonacci) | Moderada | +9.2% | +1.7% | -34% | 0.54 | +8.8% | +11.2% / -55% |
| 🟢 | Rebote corto en S&P 500 (RSI-2) | Moderada | +7.0% | +2.4% | -15% | 0.72 | +5.8% | +11.2% / -55% |
| 🟢 | Tendencia BTC (promedio de 200 días) | Moderada | +24.7% | +2.6% | -64% | 0.68 | +32.8% | +38.3% / -83% |
| 🟢 | Tendencia BTC+ETH con volatilidad controlada | Moderada | +21.2% | +2.6% | -25% | 1.00 | +12.6% | +38.3% / -83% |
| 🔴 | Carry de funding en altcoins | Moderada | +5.7% | +2.8% | -1% | 2.75 | +1.8% | +34.7% / -77% |
| 🟡 | Rotación a las cripto más fuertes | Agresiva | +78.3% | +2.6% | -72% | 1.14 | +38.4% | +38.3% / -83% |
| 🟡 | Tortugas en perpetuos BTC/ETH (largo y corto) | Agresiva | +22.1% | +2.8% | -60% | 0.60 | +11.4% | +34.7% / -77% |
| 🔴 | BTC 2x en tendencia | Agresiva | +13.0% | +2.8% | -93% | 0.53 | +18.6% | +34.7% / -77% |
| 🔴 | Scalper momentum (bot actual) | Agresiva | -33.4% | +3.9% | -56% | -7.83 | -33.8% | +10.1% / -53% |
| 🔴 | Scalper reversión a la media (bot actual) | Agresiva | +0.0% | +3.9% | +0% | 0.00 | +0.0% | +10.1% / -53% |
| 🔴 | Scalper ruptura de 24h (bot actual) | Agresiva | -19.3% | +3.9% | -36% | -3.68 | -26.9% | +10.1% / -53% |
| 🟢 | DCA semanal a BTC | Agresiva | +35.7% | +2.6% | -83% | 0.80 | +39.8% | +38.3% / -83% |
| 🟡 | Mundo emergente en partes iguales | Agresiva | +7.0% | +2.3% | -69% | 0.31 | +4.8% | +7.0% / -77% |
| 🟡 | Tendencia en cada país emergente | Moderada | +8.7% | +2.3% | -33% | 0.49 | +4.2% | +7.0% / -77% |
| 🟡 | Tendencia en Latinoamérica | Agresiva | +10.1% | +2.3% | -53% | 0.49 | +5.4% | +9.1% / -65% |
| 🟡 | Rotación a los países más fuertes | Agresiva | +9.0% | +2.4% | -55% | 0.42 | +5.7% | +11.2% / -55% |
| 🔬 | Mercados de predicción | Especulativa | — | — | — | — | — | — |
| 🔬 | Diferencia de funding entre exchanges | Especulativa | — | — | — | — | — | — |
| 🔬 | Acciones en perpetuo los fines de semana | Especulativa | — | — | — | — | — | — |
| 🔬 | Dólar cripto por país | Especulativa | — | — | — | — | — | — |
| 🔬 | Prima del peso en Bitso | Especulativa | — | — | — | — | — | — |

## Detalle

### 🟢 Portafolio 60/40 (Conservadora)

- **Cómo gana:** 60% índice S&P 500 y 40% bonos del Tesoro a 7-10 años, rebalanceo mensual: cobra el crecimiento de la economía con menos caída.
- **Riesgos:** Años como 2022 en que bajan acciones y bonos a la vez.
- **Dónde se opera:** IBKR, Questrade o Wealthsimple (en Canadá: VFV/XBB); GBM vía SIC en México
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Mensual
- **Datos:** SPY + IEF (Yahoo), desde 2002
- **Periodo:** 2002-07-30 a 2026-10-22 (24.23 años)
- **Rendimiento anual:** +8.5% · **caída máxima:** -32% · **peor año:** -18% · **meses positivos:** 67%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 Efectivo con tasa + 10% BTC (Conservadora)

- **Cómo gana:** El 90% gana la tasa libre de riesgo y el 10% en BTC da la subida; si BTC se va a cero se pierde el 10%.
- **Riesgos:** BTC puede caer 75%: el portafolio cae ~8%.
- **Dónde se opera:** CETES Directo o cuenta con interés + Bitso/Binance/Kraken
- **Capital mínimo sugerido:** 100 USD · **Frecuencia:** Trimestral
- **Datos:** BTC/USDT (Binance) + T-bill
- **Periodo:** 2017-08-17 a 2026-10-21 (9.18 años)
- **Rendimiento anual:** +9.9% · **caída máxima:** -17% · **peor año:** -8% · **meses positivos:** 56%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟡 Carry de funding BTC/ETH (Conservadora)

- **Cómo gana:** Compra el spot y vende el perpetuo: el precio se cancela y cobra el funding que pagan los largos apalancados.
- **Riesgos:** Quiebra o congelamiento del exchange, funding negativo largo, liquidación del corto si no se rebalancea el margen.
- **Dónde se opera:** Binance, Bybit u OKX (spot y perpetuos en la misma cuenta); Hyperliquid para el corto
- **Capital mínimo sugerido:** 1000 USD · **Frecuencia:** Semanal
- **Datos:** BTC/ETH spot + funding Binance desde 2019
- **Periodo:** 2019-09-10 a 2026-10-21 (7.11 años)
- **Rendimiento anual:** +5.4% · **caída máxima:** -1% · **peor año:** -1% · **meses positivos:** 74%
- **Pruebas:** ✅ le gana al efectivo · ❌ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 DCA semanal al S&P 500 (Conservadora)

- **Cómo gana:** Compra lo mismo cada semana sin intentar adivinar; no busca ganarle al mercado sino no quedarse fuera.
- **Riesgos:** Caídas de 50% del índice (2008) a mitad del camino.
- **Dónde se opera:** GBM+/Hapi (México), Wealthsimple/Questrade (Canadá), IBKR
- **Capital mínimo sugerido:** 50 USD · **Frecuencia:** Semanal
- **Datos:** SPY (Yahoo)
- **Periodo:** 1995-01-03 a 2026-10-22 (31.8 años)
- **Resultado:** aportó 165,900, terminó con 1,262,328 (x7.61), TIR +10.8% anual
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ caída < 85%

### 🟢 Tendencia S&P 500 (Faber) (Moderada)

- **Cómo gana:** Está en el índice sólo cuando cerró el mes arriba de su promedio de 10 meses; si no, efectivo con tasa. Esquiva las caídas largas.
- **Riesgos:** Mercados de lado con señales falsas; se queda fuera en rebotes rápidos.
- **Dónde se opera:** IBKR / Questrade (con API) o a mano en cualquier broker
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Mensual
- **Datos:** SPY + T-bill desde 1995
- **Periodo:** 1995-01-03 a 2026-10-22 (31.8 años)
- **Rendimiento anual:** +9.6% · **caída máxima:** -24% · **peor año:** -20% · **meses positivos:** 74%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 Momentum dual (Antonacci) (Moderada)

- **Cómo gana:** Cada mes se queda con lo que más subió en 12 meses entre EE.UU. y el resto del mundo, o se pasa a bonos si ninguno le gana al efectivo.
- **Riesgos:** Cambios de régimen bruscos (2020) lo agarran tarde.
- **Dónde se opera:** IBKR / Questrade / Wealthsimple
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Mensual
- **Datos:** SPY, EFA, AGG desde 2004
- **Periodo:** 2003-09-29 a 2026-10-22 (23.06 años)
- **Rendimiento anual:** +9.2% · **caída máxima:** -34% · **peor año:** -17% · **meses positivos:** 66%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 Rebote corto en S&P 500 (RSI-2) (Moderada)

- **Cómo gana:** Compra caídas de 2-3 días dentro de una tendencia alcista y vende al primer rebote.
- **Riesgos:** Caídas que no rebotan (crash); muchas operaciones, el costo importa.
- **Dónde se opera:** IBKR (API) o Alpaca
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Diaria
- **Datos:** SPY desde 1995
- **Periodo:** 1995-01-03 a 2026-10-22 (31.8 años)
- **Rendimiento anual:** +7.0% · **caída máxima:** -15% · **peor año:** -9% · **meses positivos:** 86%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 Tendencia BTC (promedio de 200 días) (Moderada)

- **Cómo gana:** Tiene BTC sólo cuando está arriba de su promedio de 200 días; sale en los mercados bajistas.
- **Riesgos:** Caídas de 30% antes de que salte la salida; muchas señales falsas en mercados de lado.
- **Dónde se opera:** Bitso, Binance, Kraken o Coinbase
- **Capital mínimo sugerido:** 100 USD · **Frecuencia:** Diaria
- **Datos:** BTC/USDT Binance desde 2017
- **Periodo:** 2017-08-17 a 2026-10-21 (9.18 años)
- **Rendimiento anual:** +24.7% · **caída máxima:** -64% · **peor año:** -35% · **meses positivos:** 32%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🟢 Tendencia BTC+ETH con volatilidad controlada (Moderada)

- **Cómo gana:** Igual que la tendencia, pero invierte menos cuando el mercado está nervioso para apuntar a 25% de volatilidad anual.
- **Riesgos:** Rinde menos en las subidas fuertes; sigue cayendo en los desplomes de un día.
- **Dónde se opera:** Binance / Kraken / Bybit
- **Capital mínimo sugerido:** 300 USD · **Frecuencia:** Semanal
- **Datos:** BTC, ETH desde 2017
- **Periodo:** 2017-08-17 a 2026-10-21 (9.18 años)
- **Rendimiento anual:** +21.2% · **caída máxima:** -25% · **peor año:** -16% · **meses positivos:** 39%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85%

### 🔴 Carry de funding en altcoins (Moderada)

- **Cómo gana:** Igual que el carry de BTC/ETH, pero rota cada semana a las 5 monedas con funding más alto.
- **Riesgos:** Altcoins que suben 100% en un día (margen del corto), deslistados, la diferencia spot-perpetuo, menos liquidez.
- **Dónde se opera:** Binance / Bybit (perpetuos de altcoins)
- **Capital mínimo sugerido:** 2000 USD · **Frecuencia:** Semanal
- **Datos:** 20 altcoins + funding desde 2019
- **Periodo:** 2019-09-10 a 2026-10-21 (7.11 años)
- **Rendimiento anual:** +5.7% · **caída máxima:** -1% · **peor año:** -1% · **meses positivos:** 56%
- **Pruebas:** ✅ le gana al efectivo · ❌ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia en el universo; se ignora la diferencia de precio spot-perpetuo.

### 🟡 Rotación a las cripto más fuertes (Agresiva)

- **Cómo gana:** Cada lunes compra las 3 monedas que más subieron en 4 semanas, sólo si BTC está en tendencia.
- **Riesgos:** Caídas de 60% en altcoins, reversiones bruscas, sesgo de supervivencia en el backtest.
- **Dónde se opera:** Binance / Kraken
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Semanal
- **Datos:** 20 monedas Binance desde 2017
- **Periodo:** 2017-08-17 a 2026-10-21 (9.18 años)
- **Rendimiento anual:** +78.3% · **caída máxima:** -72% · **peor año:** -45% · **meses positivos:** 34%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia: el universo son monedas que hoy siguen listadas.

### 🟡 Tortugas en perpetuos BTC/ETH (largo y corto) (Agresiva)

- **Cómo gana:** Se sube a las rupturas de máximos o mínimos de 55 días, a favor de la tendencia, y también gana en las bajadas.
- **Riesgos:** Muchas pérdidas chicas seguidas; funding en contra; riesgo de liquidación si se apalanca.
- **Dónde se opera:** Binance / Bybit / Hyperliquid
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Diaria
- **Datos:** BTC, ETH + funding desde 2019
- **Periodo:** 2019-09-10 a 2026-10-21 (7.11 años)
- **Rendimiento anual:** +22.1% · **caída máxima:** -60% · **peor año:** -17% · **meses positivos:** 43%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ❌ parámetros vecinos · ✅ caída < 85%

### 🔴 BTC 2x en tendencia (Agresiva)

- **Cómo gana:** La tendencia de 200 días con el doble de exposición vía perpetuo.
- **Riesgos:** Caídas de 50%+ antes de salir; el funding se come parte de la ganancia; liquidación con 2x en un desplome de 50% en un día.
- **Dónde se opera:** Binance / Bybit / Hyperliquid
- **Capital mínimo sugerido:** 300 USD · **Frecuencia:** Diaria
- **Datos:** BTC perpetuo desde 2019
- **Periodo:** 2019-09-10 a 2026-10-21 (7.11 años)
- **Rendimiento anual:** +13.0% · **caída máxima:** -93% · **peor año:** -77% · **meses positivos:** 35%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ❌ caída < 85%

### 🔴 Scalper momentum (bot actual) (Agresiva)

- **Cómo gana:** Entra en cruces de EMA con volumen y busca +0.5% en velas de 1 hora.
- **Riesgos:** Las comisiones se comen el margen; depende de la ejecución.
- **Dónde se opera:** Binance
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Horaria
- **Datos:** BTC/USDT 1h, últimos 2 años
- **Periodo:** 2024-10-22 a 2026-10-22 (2.0 años)
- **Rendimiento anual:** -33.4% · **caída máxima:** -56% · **peor año:** -34% · **meses positivos:** 4%
- **Operaciones:** 268 · acierto 37% · promedio neto -0.301% (bruto -0.001%)
- **Pruebas:** ❌ le gana al efectivo · ❌ fuera de muestra · ❌ doble costo · ✅ caída < 85% · ✅ 30+ operaciones

### 🔴 Scalper reversión a la media (bot actual) (Agresiva)

- **Cómo gana:** Compra cuando el precio rompe la banda baja de Bollinger con RSI bajo.
- **Riesgos:** Atrapa cuchillos cayendo.
- **Dónde se opera:** Binance
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Horaria
- **Datos:** BTC/USDT 1h, últimos 2 años
- **Periodo:** 2024-10-22 a 2026-10-22 (2.0 años)
- **Rendimiento anual:** +0.0% · **caída máxima:** +0% · **peor año:** +0% · **meses positivos:** 0%
- **Operaciones:** 0 (la estrategia no disparó en todo el periodo)
- **Pruebas:** ❌ le gana al efectivo · ❌ fuera de muestra · ❌ doble costo · ✅ caída < 85% · ❌ 30+ operaciones

### 🔴 Scalper ruptura de 24h (bot actual) (Agresiva)

- **Cómo gana:** Compra rupturas del máximo de 24 horas con volumen.
- **Riesgos:** Rupturas falsas.
- **Dónde se opera:** Binance
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Horaria
- **Datos:** BTC/USDT 1h, últimos 2 años
- **Periodo:** 2024-10-22 a 2026-10-22 (2.0 años)
- **Rendimiento anual:** -19.3% · **caída máxima:** -36% · **peor año:** -23% · **meses positivos:** 24%
- **Operaciones:** 165 · acierto 67% · promedio neto -0.257% (bruto +0.043%)
- **Pruebas:** ❌ le gana al efectivo · ❌ fuera de muestra · ❌ doble costo · ✅ caída < 85% · ✅ 30+ operaciones

### 🟢 DCA semanal a BTC (Agresiva)

- **Cómo gana:** Compra BTC cada semana sin vender nunca.
- **Riesgos:** Caídas de 75-80% (2018, 2022) a mitad del camino.
- **Dónde se opera:** Bitso / Binance / Kraken / Wealthsimple
- **Capital mínimo sugerido:** 20 USD · **Frecuencia:** Semanal
- **Datos:** BTC/USDT desde 2017
- **Periodo:** 2017-08-17 a 2026-10-21 (9.18 años)
- **Resultado:** aportó 47,900, terminó con 264,182 (x5.52), TIR +35.7% anual
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ caída < 85%

### 🟡 Mundo emergente en partes iguales (Agresiva)

- **Cómo gana:** Compra a todos los emergentes por igual (México, Brasil, India, Vietnam, Sudáfrica, Turquía...) y rebalancea cada trimestre.
- **Riesgos:** Crisis emergentes (2008, 2015, 2022) con caídas de 50%+; devaluaciones.
- **Dónde se opera:** IBKR (ETFs de país en EE.UU.): desde México y Canadá
- **Capital mínimo sugerido:** 1000 USD · **Frecuencia:** Trimestral
- **Datos:** 22 ETFs de país (Yahoo) desde 1996
- **Periodo:** 1996-03-18 a 2026-10-21 (30.59 años)
- **Rendimiento anual:** +7.0% · **caída máxima:** -69% · **peor año:** -45% · **meses positivos:** 58%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia: el universo son ETFs que hoy siguen vivos; no incluye Rusia (ERUS), que quedó congelado en 2022, ni Colombia (GXG), cuyo ETF cerró.

### 🟡 Tendencia en cada país emergente (Moderada)

- **Cómo gana:** Partes iguales en cada país emergente, pero sólo mientras está arriba de su promedio de 10 meses: sale de los países en crisis.
- **Riesgos:** Mercados de lado con señales falsas; muchos ETFs, más órdenes.
- **Dónde se opera:** IBKR (ETFs de país en EE.UU.)
- **Capital mínimo sugerido:** 2000 USD · **Frecuencia:** Mensual
- **Datos:** 22 ETFs de país desde 1996
- **Periodo:** 1996-03-18 a 2026-10-21 (30.59 años)
- **Rendimiento anual:** +8.7% · **caída máxima:** -33% · **peor año:** -14% · **meses positivos:** 63%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia: el universo son ETFs que hoy siguen vivos; no incluye Rusia (ERUS), que quedó congelado en 2022, ni Colombia (GXG), cuyo ETF cerró.

### 🟡 Tendencia en Latinoamérica (Agresiva)

- **Cómo gana:** La misma regla de tendencia, sólo con 5 países de Latinoamérica.
- **Riesgos:** Región chica y correlacionada: cuando cae una, caen todas; riesgo político y de moneda.
- **Dónde se opera:** IBKR (EWW, EWZ, ECH, EPU, ARGT)
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Mensual
- **Datos:** 5 ETFs de Latinoamérica desde 1996
- **Periodo:** 1996-03-18 a 2026-10-21 (30.59 años)
- **Rendimiento anual:** +10.1% · **caída máxima:** -53% · **peor año:** -23% · **meses positivos:** 66%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia: el universo son ETFs que hoy siguen vivos; no incluye Rusia (ERUS), que quedó congelado en 2022, ni Colombia (GXG), cuyo ETF cerró.

### 🟡 Rotación a los países más fuertes (Agresiva)

- **Cómo gana:** Cada mes se queda con los 4 países que más subieron en un año, si le ganan al efectivo: va a donde está la fuerza (Japón, India, Argentina...).
- **Riesgos:** Reversiones bruscas entre países; un país puede cerrar su mercado (Rusia 2022).
- **Dónde se opera:** IBKR (ETFs de país en EE.UU.)
- **Capital mínimo sugerido:** 1000 USD · **Frecuencia:** Mensual
- **Datos:** 33 ETFs de país desde 1996
- **Periodo:** 1995-01-03 a 2026-10-22 (31.8 años)
- **Rendimiento anual:** +9.0% · **caída máxima:** -55% · **peor año:** -24% · **meses positivos:** 65%
- **Pruebas:** ✅ le gana al efectivo · ✅ fuera de muestra · ✅ doble costo · ✅ ejecutar tarde · ✅ parámetros vecinos · ✅ caída < 85% · ❌ sin sesgo conocido
- **Ojo:** Sesgo de supervivencia: el universo son ETFs que hoy siguen vivos; no incluye Rusia (ERUS), que quedó congelado en 2022, ni Colombia (GXG), cuyo ETF cerró.

### 🔬 Mercados de predicción (Especulativa)

- **Cómo gana:** Precios mal puestos en eventos (sesgo hacia el favorito y el perdedor), y la misma pregunta con precio distinto en dos plataformas.
- **Riesgos:** Liquidez chica, reglas de resolución, acceso legal según el país.
- **Dónde se opera:** Polymarket (internacional) o Kalshi; revisar acceso por país
- **Capital mínimo sugerido:** 200 USD · **Frecuencia:** Por evento
- **Datos:** Pendiente: recolector de precios de Polymarket y Kalshi

### 🔬 Diferencia de funding entre exchanges (Especulativa)

- **Cómo gana:** Largo en el perpetuo donde el funding es bajo y corto donde es alto, en la misma moneda.
- **Riesgos:** Capital repartido en dos lugares, riesgo de los dos exchanges.
- **Dónde se opera:** Binance + Hyperliquid / Bybit
- **Capital mínimo sugerido:** 2000 USD · **Frecuencia:** Diaria
- **Datos:** Pendiente: recolector de funding de varios exchanges

### 🔬 Acciones en perpetuo los fines de semana (Especulativa)

- **Cómo gana:** El perpetuo de una acción sigue cotizando cuando la bolsa está cerrada; el hueco contra la apertura del lunes puede repetirse.
- **Riesgos:** Producto nuevo con poco historial y funding muy alto.
- **Dónde se opera:** Binance / Hyperliquid (perpetuos de acciones)
- **Capital mínimo sugerido:** 500 USD · **Frecuencia:** Semanal
- **Datos:** Pendiente: historial de perpetuos de acciones

### 🔬 Dólar cripto por país (Especulativa)

- **Cómo gana:** En países con control de cambios (Argentina, Nigeria, Turquía...) el dólar cripto cotiza arriba del oficial; en Corea BTC tiene la 'prima kimchi'. La prima mide el estrés local.
- **Riesgos:** No se puede arbitrar desde fuera: controles de capital, KYC local, cuentas bancarias del país.
- **Dónde se opera:** Sólo como información: aprovecharlo exige vivir en ese país
- **Capital mínimo sugerido:** 0 USD · **Frecuencia:** Informativo
- **Datos:** Recolector: Bitso, Mercado Bitcoin, Buda, Upbit, BtcTurk, VALR, Luno, CoinDCX, Binance P2P

### 🔬 Prima del peso en Bitso (Especulativa)

- **Cómo gana:** En pánicos del peso o de cripto, BTC en pesos se despega del precio global por tipo de cambio.
- **Riesgos:** Hoy la prima está en ±0.05% y las comisiones de Bitso son altas; sólo sirve en días raros.
- **Dónde se opera:** Bitso + Binance
- **Capital mínimo sugerido:** 2000 USD · **Frecuencia:** Por evento
- **Datos:** Pendiente: recolector Bitso vs Binance
