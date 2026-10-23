# Llaves y cuentas: cómo crearlas sin riesgo

Todas las llaves van en el archivo `.env` (que git ignora) y en ningún otro lado: ni en el código,
ni en capturas de pantalla, ni en chats. Si alguna se expone, se borra y se crea otra.

Los nombres de los botones pueden cambiar un poco según la versión de cada sitio.

## Papel o real: un interruptor por mercado

Cada mercado tiene su propio interruptor, así prender uno no prende los demás:

```
DRY_RUN=true            # el valor por defecto de los tres de abajo
CRYPTO_DRY_RUN=         # vacío = lo que diga DRY_RUN
STOCKS_DRY_RUN=
FOREX_DRY_RUN=
```

`true` = el bot simula las órdenes por dentro (papel). `false` = las manda al broker. Para que
el bot opere de verdad en cualquier mercado hacen falta además dos líneas:

```
LIVE_CONFIRMED=true     # la segunda llave: sin ella el bot no arranca en real
REAL_CAPITAL_USD=200    # el dinero real que maneja el bot; el freno de emergencia se mide contra esto
```

Cada vez que arranca con algo en real, el bot avisa por Telegram. La historia en real se guarda
en `data/izalith_real.db`, aparte de la de papel (`data/izalith.db`).

## Kraken Pro (cripto)

1. Activa la verificación en dos pasos de la cuenta antes de crear la llave.
2. En Kraken Pro: menú de tu perfil → **Settings** → **API** → **Create API key**.
3. Dale un nombre que diga para qué es, por ejemplo `izalith-bot`.
4. Permisos — marca **sólo** estos:
   - Query Funds (consultar saldos)
   - Query Open Orders & Trades y Query Closed Orders & Trades
   - Create & Modify Orders
   - Cancel/Close Orders
5. **Nunca** marques **Withdraw Funds** (retirar). Sin ese permiso, aunque alguien robe la llave no
   puede sacar tu dinero de Kraken.
6. En **IP address restriction / allowlist** pon la IP pública de tu casa (la ves en
   https://ifconfig.me). Así la llave sólo funciona desde tu red. Si cambias de país o de internet,
   tienes que actualizarla.
7. Copia la llave y el secreto al `.env`:

   ```
   CRYPTO_EXCHANGE=kraken
   CRYPTO_API_KEY=...
   CRYPTO_SECRET=...
   CRYPTO_TESTNET=false
   CRYPTO_DRY_RUN=true
   ```

   Deja `CRYPTO_DRY_RUN=true` las primeras semanas: el bot lee tu cuenta real pero sigue operando
   en papel. Kraken no tiene cuenta de prueba (testnet): por eso `CRYPTO_TESTNET=false`.
   En Kraken el bot opera contra **USD** (BTC/USD, ETH/USD), así que deposita o convierte a USD;
   si prefieres otra moneda, cámbiala con `CRYPTO_QUOTE`.

## Interactive Brokers (acciones y ETFs)

1. En el portal de IBKR, crea también tu **cuenta de papel** (Settings → Paper Trading Account).
   Tiene su propio usuario.
2. Instala **IB Gateway** (más ligero que TWS) y entra con el usuario de **papel**.
3. En IB Gateway: Configure → Settings → API → Settings:
   - Enable ActiveX and Socket Clients: **sí**
   - Socket port: **4002** (papel). La cuenta real usa 4001.
   - Allow connections from localhost only: **sí**
   - Trusted IPs: `127.0.0.1`
   - Read-Only API: déjalo **marcado** hasta que quieras que el bot mande órdenes.
4. En el `.env`:

   ```
   STOCKS_BROKER=ibkr
   IBKR_PORT=4002
   STOCKS_DRY_RUN=false
   ```

   Con el puerto de papel y `STOCKS_DRY_RUN=false`, las órdenes van al motor de papel de IBKR: es
   la práctica más realista antes del dinero real. Usa `STOCKS_DRY_RUN`, **no** `DRY_RUN=false`:
   con `DRY_RUN=false` también se prendería en real cualquier otro mercado que tenga llaves
   (por ejemplo Kraken). Como el bot manda órdenes a IBKR, también pide `LIVE_CONFIRMED=true` y
   `REAL_CAPITAL_USD` (pon lo que tenga la cuenta de papel).
5. Para pasar a real: entra a IB Gateway con el usuario real, cambia a `IBKR_PORT=4001` y pon en
   `REAL_CAPITAL_USD` lo que de verdad depositaste.

## Telegram (resumen diario y alertas)

1. En Telegram busca **@BotFather**, escríbele `/newbot` y sigue los pasos. Te da un **token**.
2. Mándale cualquier mensaje a tu bot nuevo.
3. Abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` (con tu token). En la
   respuesta busca `"chat":{"id": ...}`: ese número es tu chat.
4. En el `.env`:

   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```

## healthchecks.io (aviso si el bot se muere)

1. Crea una cuenta gratis en https://healthchecks.io.
2. **Add Check**: periodo de 5 minutos, gracia de 10 minutos.
3. Copia la URL de ping (`https://hc-ping.com/...`) al `.env`:

   ```
   HEALTHCHECK_URL=https://hc-ping.com/...
   ```

   Si el bot deja de latir 15 minutos, healthchecks.io te manda un correo.

## Banxico (tasa de Banxico en los termómetros, opcional)

1. Pide tu token gratis en https://www.banxico.org.mx/SieAPIRest/service/v1/token
2. En el `.env`: `BANXICO_TOKEN=...`

## Si algo sale mal

- **Se expuso una llave:** bórrala en el exchange en ese momento y crea otra.
- **El bot hace algo raro:** ciérralo (Ctrl+C, o doble clic en `tools\detener.bat` si arrancó solo:
  le pide que se apague en orden y sólo lo fuerza si en un minuto no lo hizo).
  Las posiciones quedan en el exchange; nada se pierde por cerrar el bot. Si una orden quedó a
  medias, al volver a arrancar el bot la revisa, cancela lo que siga vivo y anota lo que se llenó.
- **Se activó el freno de emergencia:** revisa qué pasó en el panel y quítalo con
  `python -m tools.freno --quitar`.
