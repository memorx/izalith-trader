@echo off
rem Arranca el bot desde su carpeta. Lo usa la tarea programada "Izalith Trader".
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" run.py >> "logs\bot_consola.log" 2>&1
