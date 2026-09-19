@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
call .venv\Scripts\activate.bat
python bot.py
pause
