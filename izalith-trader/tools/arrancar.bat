@echo off
rem Arranca el bot desde su carpeta. Lo usa el arranque automático "Izalith Trader".
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
if not exist "logs" mkdir "logs"
rem Si el log de consola pasa de 10 MB, se guarda una copia (.1) y se empieza uno nuevo
if exist "logs\bot_consola.log" for %%F in ("logs\bot_consola.log") do if %%~zF GTR 10485760 move /y "logs\bot_consola.log" "logs\bot_consola.1.log" >nul
".venv\Scripts\python.exe" run.py >> "logs\bot_consola.log" 2>&1
