@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUNBUFFERED=1

if not exist ".venv\Scripts\python.exe" (
  echo Создаю виртуальное окружение...
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt

echo.
echo Переводчик пишет от твоего аккаунта, без подписи бота.
echo Если попросит QR — отсканируй его в Telegram:
echo Настройки → Устройства → Подключить устройство
echo.
python userbot.py
echo.
pause
