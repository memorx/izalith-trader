# Arranque automático en Windows

Hay un archivo `Izalith Trader.vbs` en tu carpeta de Inicio de Windows
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). Cada vez que entras a tu sesión arranca
el bot oculto, sin ventana. Si ya hay una copia corriendo, la segunda se cierra sola (candado del
puerto 47651), así que nunca corren dos.

- Arrancarlo ahora sin reiniciar: doble clic en `tools\arrancar_oculto.vbs`
- Detenerlo: doble clic en `tools\detener.bat`
- Que deje de arrancar solo: borra `Izalith Trader.vbs` de la carpeta de Inicio
  (Win+R, escribe `shell:startup`, Enter)

Lo que imprime el bot queda en `logs\bot_consola.log`; el log normal sigue en `logs\`.

Se usa la carpeta de Inicio y no el Programador de tareas porque éste pide permisos de
administrador para tareas que arrancan al iniciar sesión.
