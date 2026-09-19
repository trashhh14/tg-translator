from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, MessageNotModifiedError, SessionPasswordNeededError

from translator import LANG_ALIASES, LANGS, Translator, normalize_lang, smart_target

load_dotenv()

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger("userbot")

# Telegram Desktop public credentials — только для личного скрипта на своём ПК.
API_ID = int(os.getenv("TG_API_ID", "2040"))
API_HASH = os.getenv("TG_API_HASH", "b18441a1ff607e10a989891a5462e627")
NATIVE = os.getenv("NATIVE_LANG", "ru") or "ru"
FOREIGN = os.getenv("FOREIGN_LANG", "en") or "en"

DATA_DIR = Path("data")
SESSION = DATA_DIR / "user"

COMMANDS = {"t", "help", *LANGS.keys(), *LANG_ALIASES.keys()}
CMD_RE = re.compile(r"^\.([A-Za-z]{1,8})(?:\s+([\s\S]+))?$")

HELP = (
    "Переводчик пишет **от тебя**, без подписи бота.\n\n"
    "`.t мне нравится твой стиль` — сообщение само станет переводом\n"
    "`.en текст` / `.ru текст` / `.de текст` — на нужный язык\n\n"
    "Ответь `.t` на **своё** сообщение — его заменит перевод\n"
    "Ответь `.t` на **чужое** — перевод придёт в Избранное, чат не затронет"
)


def parse_command(text: str) -> tuple[str, str] | None:
    match = CMD_RE.match((text or "").strip())
    if not match:
        return None
    cmd = match.group(1).lower()
    if cmd not in COMMANDS:
        return None
    return cmd, (match.group(2) or "").strip()


def show_qr(url: str) -> Path | None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"login_qr_{int(time.time())}.png"
    try:
        import qrcode

        img = qrcode.make(url)
        img.save(path)
        print(f"QR сохранён: {path.resolve()}")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            pass
        try:
            qr = qrcode.QRCode()
            qr.add_data(url)
            qr.print_ascii(invert=True)
        except Exception:
            print(url)
        return path
    except Exception as exc:  # noqa: BLE001
        print("Открой ссылку в Telegram или отсканируй QR:")
        print(url)
        logger.warning("QR render failed: %s", exc)
        return None


def can_prompt() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


async def login_by_phone(client: TelegramClient) -> None:
    if not can_prompt():
        raise SystemExit(
            "QR не отсканировали. Запусти start.bat двойным щелчком и войди по номеру."
        )
    print("\nВход по номеру.")
    phone = input("Номер телефона (+7...): ").strip()
    await client.send_code_request(phone)
    code = input("Код из Telegram: ").strip()
    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = input("Облачный пароль 2FA: ").strip()
        await client.sign_in(password=password)


async def ensure_login(client: TelegramClient) -> None:
    await client.connect()
    if await client.is_user_authorized():
        return

    print("\nНужен вход в аккаунт.")
    print("Telegram → Настройки → Устройства → Подключить устройство")
    print("Отсканируй QR сразу, он живёт около минуты.\n")

    while True:
        if not client.is_connected():
            await client.connect()
        try:
            qr = await client.qr_login()
            show_qr(qr.url)
            await qr.wait(timeout=55)
            break
        except asyncio.TimeoutError:
            print("QR устарел, рисую новый…")
            continue
        except SessionPasswordNeededError:
            if not can_prompt():
                raise SystemExit(
                    "Нужен облачный пароль 2FA. Запусти start.bat и введи его там."
                )
            password = input("Облачный пароль 2FA: ").strip()
            await client.sign_in(password=password)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("QR login failed: %s", exc)
            print(f"QR не сработал: {exc}")
            await asyncio.sleep(2)
            if can_prompt():
                await login_by_phone(client)
                break
            print("Пробую QR ещё раз…")
            continue

    me = await client.get_me()
    print(f"\nГотово, вошёл как {me.first_name} (@{me.username or 'без username'}).")


async def replace_message(event, text: str) -> None:
    try:
        await event.edit(text)
        return
    except MessageNotModifiedError:
        return
    except Exception:
        logger.info("edit failed, resend")
    chat = event.chat_id
    reply_to = event.reply_to_msg_id
    await event.delete()
    await event.client.send_message(chat, text, reply_to=reply_to)


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    translator = Translator()
    client = TelegramClient(str(SESSION), API_ID, API_HASH)

    @client.on(events.NewMessage(outgoing=True))
    async def on_out(event: events.NewMessage.Event) -> None:
        raw = (event.raw_text or "").strip()
        parsed = parse_command(raw)
        if not parsed:
            return
        cmd, rest = parsed
        if cmd == "help":
            await replace_message(event, HELP)
            return

        target = None if cmd == "t" else normalize_lang(cmd)
        text = rest
        reply = await event.get_reply_message() if event.is_reply else None

        if not text and reply:
            text = (reply.raw_text or "").strip()
        if not text:
            await replace_message(
                event,
                "Напиши `.t текст` или ответь `.t` на сообщение.\n`.help` — справка",
            )
            await asyncio.sleep(4)
            try:
                await event.delete()
            except Exception:
                pass
            return

        if target is None:
            target = smart_target(text, NATIVE, FOREIGN)

        try:
            result = await translator.translate(text, target)
        except Exception as exc:  # noqa: BLE001
            logger.exception("translate failed")
            await replace_message(event, f"Не перевелось: {exc}")
            return

        # Чужое сообщение: перевод только себе, в чат ничего не пишем.
        if reply and not reply.out and not rest:
            await event.delete()
            from_name = ""
            try:
                sender = await reply.get_sender()
                from_name = getattr(sender, "first_name", "") or ""
            except Exception:
                pass
            body = (
                f"Перевод"
                + (f" ({from_name})" if from_name else "")
                + f"\n\n{text}\n\n→ {result.text}"
            )
            await client.send_message("me", body)
            return

        # Своё исходное сообщение: заменить его, команду удалить.
        if reply and reply.out and not rest:
            try:
                await reply.edit(result.text)
                await event.delete()
                return
            except Exception:
                logger.info("could not edit original, replacing command")

        await replace_message(event, result.text)

    try:
        await ensure_login(client)
        me = await client.get_me()
        print(f"Переводчик запущен для {me.first_name}. Окно не закрывай.")
        print("В любом чате: .t текст")
        await client.run_until_disconnected()
    finally:
        await translator.close()
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except FloodWaitError as exc:
        print(f"Telegram просит подождать {exc.seconds} сек.")
        raise
    except KeyboardInterrupt:
        print("Остановлен.")
