# Переводчик в Telegram

Пришли боту текст — он ответит переводом. Русский ↔ английский.

Локально: `start_bot.bat` (нужен `BOT_TOKEN` в `.env`).

## Чтобы работал при выключенном ПК

Нужен бесплатный сервер [Render](https://render.com). Компьютер после этого можно выключать.

1. Зайди на [render.com](https://render.com) через GitHub.
2. **New → Blueprint** (или Web Service) → выбери репозиторий `tg-translator`.
3. Plan: **Free**.
4. В Environment добавь `BOT_TOKEN` — тот же токен от BotFather.
5. Create. Подожди 2–3 минуты, статус станет Live.
6. Напиши боту `/start`.

На бесплатном тарифе сервер может засыпать через 15 минут тишины. Первое сообщение после сна приходит с задержкой около минуты. Чтобы не засыпал: в GitHub у репозитория **Settings → Secrets → Actions** добавь `BOT_URL` = адрес вида `https://tg-translator.onrender.com`.
