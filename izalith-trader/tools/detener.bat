@echo off
rem Detiene la copia del bot que está corriendo (la que tiene el candado del puerto 47651).
rem Primero le pide que se apague en orden (archivo data\STOP): termina la orden que esté
rem mandando, guarda la foto final y cierra. Sólo si en 60 segundos no se apagó, se fuerza.
cd /d "%~dp0.."
powershell -NoProfile -Command ^
  "$c = Get-NetTCPConnection -LocalPort 47651 -State Listen -ErrorAction SilentlyContinue;" ^
  "if (-not $c) { 'No hay ninguna copia de Izalith corriendo.'; exit 0 }" ^
  "New-Item -ItemType File -Force -Path 'data\STOP' | Out-Null; 'Pidiendo a Izalith que se detenga...';" ^
  "for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 1; if (-not (Get-NetTCPConnection -LocalPort 47651 -State Listen -ErrorAction SilentlyContinue)) { 'Izalith detenido.'; exit 0 } }" ^
  "'No se detuvo en 60 segundos: se fuerza el cierre.'; Stop-Process -Id $c.OwningProcess -Force; Remove-Item 'data\STOP' -ErrorAction SilentlyContinue"
