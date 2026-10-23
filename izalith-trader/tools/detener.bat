@echo off
rem Detiene la copia del bot que está corriendo (la que tiene el candado del puerto 47651).
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 47651 -State Listen -ErrorAction SilentlyContinue; if ($c) { Stop-Process -Id $c.OwningProcess -Force; 'Izalith detenido.' } else { 'No hay ninguna copia de Izalith corriendo.' }"
