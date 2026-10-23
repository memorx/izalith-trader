@echo off
rem Arranca el bot desde su carpeta. Lo usa la tarea programada "Izalith Trader".
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
rem Si el log de consola pasa de 10 MB, se guarda una copia (.1) y se empieza uno nuevo
if exist "logsot_consola.log" for %%F in ("logsot_consola.log") do if %%~zF GTR 10485760 move /y "logsot_consola.log" "logsot_consola.1.log" >nul
".venv\Scripts\python.exe" run.py >> "logs\bot_consola.log" 2>&1
