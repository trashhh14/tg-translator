from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from html import escape

from dotenv import load_dotenv
import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from settings import SettingsStore
from translator import LANGS, Translator, normalize_lang, smart_target

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_NATIVE = os.getenv("NATIVE_LANG", "ru").strip() or "ru"
DEFAULT_FOREIGN = os.getenv("FOREIGN_LANG", "en").strip() or "en"

QUICK_LANGS = ["ru", "en", "uk", "de", "es", "fr", "tr", "zh-CN", "ja", "pl"]
LANG_PREFIX_RE = re.compile(
    r"^(?:(?:to|на)\s+)?([a-z]{2}(?:-[a-z]{2})?)\s*[:\-–>]+\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
MAX_TEXT = 3500


def user_prefs(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict[str, str]:
    store: SettingsStore = context.bot_data["settings"]
    return store.get(user_id, DEFAULT_NATIVE, DEFAULT_FOREIGN)


def translator(context: ContextTypes.DEFAULT_TYPE) -> Translator:
    return context.bot_data["translator"]


def lang_label(code: str) -> str:
    return LANGS.get(code, code)


def flag_for(code: str) -> str:
    flags = {
        "ru": "🇷🇺",
        "en": "🇬🇧",
        "uk": "🇺🇦",
        "de": "🇩🇪",
        "es": "🇪🇸",
        "fr": "🇫🇷",
        "it": "🇮🇹",
        "pl": "🇵🇱",
        "tr": "🇹🇷",
        "zh-CN": "🇨🇳",
        "ja": "🇯🇵",
        "ko": "🇰🇷",
        "ar": "🇸🇦",
        "pt": "🇵🇹",
        "nl": "🇳🇱",
        "cs": "🇨🇿",
        "sv": "🇸🇪",
        "fi": "🇫🇮",
        "el": "🇬🇷",
        "he": "🇮🇱",
        "hi": "🇮🇳",
        "id": "🇮🇩",
        "vi": "🇻🇳",
        "th": "🇹🇭",
        "kk": "🇰🇿",
        "be": "🇧🇾",
    }
    return flags.get(code, "🌐")


def parse_text_and_lang(raw: str) -> tuple[str, str | None]:
    text = (raw or "").strip()
    if not text:
        return "", None
    match = LANG_PREFIX_RE.match(text)
    if match:
        lang = normalize_lang(match.group(1))
        rest = match.group(2).strip()
        if lang and rest:
            return rest, lang
    parts = text.split(maxsplit=1)
    if len(parts) == 2:
        lang = normalize_lang(parts[0])
        if lang:
            return parts[1].strip(), lang
    return text, None


def message_text(message) -> str:
    if not message:
        return ""
    return (message.text or message.caption or "").strip()


def source_text(update: Update, extra: str) -> str:
    extra = (extra or "").strip()
    message = update.effective_message
    if extra:
        return extra
    if message and message.reply_to_message:
        return message_text(message.reply_to_message)
    return message_text(message)


def format_translation(original: str, translated: str, source: str, target: str) -> str:
    return translated


def result_keyboard(target: str, origin: str = "msg", seed: str = "") -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for code in QUICK_LANGS:
        mark = "• " if code == target else ""
        row.append(
            InlineKeyboardButton(
                f"{mark}{flag_for(code)}",
                callback_data=f"tr:{origin}:{code}",
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def help_text(username: str | None) -> str:
    return (
        "Пришли любой текст — переведу.\n"
        "Русский сам уйдёт в английский, остальное — в русский.\n\n"
        "Можно сразу язык: <code>en привет</code> или <code>de спасибо</code>\n"
        "В группе ответь на сообщение командой /t"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    username = context.bot.username
    await update.message.reply_text(
        "Привет. Просто пришли текст.\n\n" + help_text(username),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        help_text(context.bot.username),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    store: SettingsStore = context.bot_data["settings"]
    args = [normalize_lang(a) for a in context.args]
    args = [a for a in args if a]
    if len(args) >= 2:
        prefs = await store.set(update.effective_user.id, native=args[0], foreign=args[1])
        await update.message.reply_text(
            f"Готово: {flag_for(prefs['native'])} {lang_label(prefs['native'])} ↔ "
            f"{flag_for(prefs['foreign'])} {lang_label(prefs['foreign'])}"
        )
        return
    if len(args) == 1:
        prefs = await store.set(update.effective_user.id, native=args[0])
        await update.message.reply_text(
            f"Родной язык: {flag_for(prefs['native'])} {lang_label(prefs['native'])}"
        )
        return
    prefs = user_prefs(context, update.effective_user.id)
    await update.message.reply_text(
        "Сейчас так:\n"
        f"• родной: {flag_for(prefs['native'])} {lang_label(prefs['native'])}\n"
        f"• второй: {flag_for(prefs['foreign'])} {lang_label(prefs['foreign'])}\n\n"
        "Сменить: <code>/lang ru en</code>\n"
        "Другие коды: /langs",
        parse_mode=ParseMode.HTML,
    )


async def cmd_langs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    lines = [f"{flag_for(code)} <code>{code}</code> — {name}" for code, name in LANGS.items()]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


def remember_text(context: ContextTypes.DEFAULT_TYPE, message, text: str) -> None:
    pending: dict[str, str] = context.bot_data.setdefault("pending", {})
    pending[f"{message.chat_id}:{message.message_id}"] = text
    context.user_data["last_text"] = text


async def translate_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_text: str = "",
    target_override: str | None = None,
) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    text, parsed_target = parse_text_and_lang(raw_text)
    if text and normalize_lang(text) == parsed_target:
        text = ""
    text = source_text(update, text)

    if not text:
        await message.reply_text(
            "Ответь этой командой на сообщение или напиши:\n"
            "<code>/t привет</code>\n"
            "<code>/t en доброе утро</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT]

    prefs = user_prefs(context, user.id)
    target = target_override or parsed_target or smart_target(
        text, prefs["native"], prefs["foreign"]
    )

    wait = await message.reply_text("Перевожу…")
    remember_text(context, wait, text)
    try:
        result = await translator(context).translate(text, target)
        await wait.edit_text(
            result.text,
            reply_markup=result_keyboard(result.target, seed=text),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("translate failed")
        await wait.edit_text(f"Не получилось перевести: {exc}")


async def cmd_t(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    target = None
    extra = ""
    if args:
        maybe = normalize_lang(args[0])
        if maybe:
            target = maybe
            extra = " ".join(args[1:])
        else:
            extra = " ".join(args)
    await translate_and_reply(update, context, extra, target_override=target)


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    text = (update.message.text or update.message.caption or "").strip()
    if not text or text.startswith("/"):
        return
    await translate_and_reply(update, context, text)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    await query.answer()

    if query.data == "menu:langs":
        lines = [f"{flag_for(code)} <code>{code}</code> — {name}" for code, name in LANGS.items()]
        await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    if not query.data.startswith("tr:"):
        return

    _, _origin, target = query.data.split(":", 2)
    target = normalize_lang(target)
    pending: dict[str, str] = context.bot_data.setdefault("pending", {})
    text = ""
    if query.message:
        text = pending.get(f"{query.message.chat_id}:{query.message.message_id}", "")
    text = text or context.user_data.get("last_text") or ""
    if not text and query.message and query.message.reply_to_message:
        text = message_text(query.message.reply_to_message)
    if not text or not target:
        await query.answer("Нет текста для перевода", show_alert=True)
        return

    try:
        result = await translator(context).translate(text, target)
        await query.edit_message_text(
            result.text,
            reply_markup=result_keyboard(result.target, seed=text),
        )
    except TelegramError:
        await query.answer("Не удалось обновить сообщение", show_alert=True)
    except Exception as exc:  # noqa: BLE001
        await query.answer(str(exc), show_alert=True)


def inline_articles(
    original: str,
    items: list[tuple[str, str, str]],
) -> list[InlineQueryResultArticle]:
    results: list[InlineQueryResultArticle] = []
    for title, body, description in items:
        results.append(
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=title[:64],
                description=description[:120],
                input_message_content=InputTextMessageContent(
                    body[:4090],
                    parse_mode=ParseMode.HTML,
                ),
            )
        )
    return results


async def on_inline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if not query:
        return
    user = query.from_user
    raw = (query.query or "").strip()
    if not raw:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Напиши текст после имени бота",
                    description="Например: привет  ·  en: доброе утро  ·  de спасибо",
                    input_message_content=InputTextMessageContent(
                        "Напиши текст после имени бота, чтобы получить перевод."
                    ),
                )
            ],
            cache_time=5,
            is_personal=True,
        )
        return

    text, forced_target = parse_text_and_lang(raw)
    if not text:
        await query.answer([], cache_time=1, is_personal=True)
        return
    if len(text) > 1000:
        text = text[:1000]

    prefs = user_prefs(context, user.id)
    auto_target = forced_target or smart_target(text, prefs["native"], prefs["foreign"])

    articles: list[tuple[str, str, str]] = []
    try:
        result = await translator(context).translate(text, auto_target)
        articles.append(
            (
                f"Отправить · {flag_for(result.target)} {lang_label(result.target)}",
                escape(result.text),
                result.text,
            )
        )
        bilingual = (
            f"{escape(text)}\n\n"
            f"{flag_for(result.target)} {escape(result.text)}"
        )
        articles.append(("Оригинал + перевод", bilingual, result.text))
    except Exception as exc:  # noqa: BLE001
        logger.exception("inline translate failed")
        await query.answer(
            [
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="Ошибка перевода",
                    description=str(exc)[:120],
                    input_message_content=InputTextMessageContent(
                        f"Не получилось перевести: {exc}"
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    await query.answer(
        inline_articles(text, articles),
        cache_time=10,
        is_personal=True,
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Update error: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("Что-то сломалось, попробуй ещё раз.")
        except TelegramError:
            pass


async def post_init(app: Application) -> None:
    app.bot_data["translator"] = Translator()
    app.bot_data["settings"] = SettingsStore("data/settings.json")
    username = (await app.bot.get_me()).username
    await app.bot.set_my_commands(
        [
            ("start", "Как пользоваться"),
            ("t", "Перевести (ответь на сообщение)"),
            ("lang", "Мои языки, например /lang ru en"),
            ("langs", "Список языков"),
            ("help", "Справка"),
        ]
    )
    logger.info("Bot @%s is ready", username)


async def post_shutdown(app: Application) -> None:
    tr: Translator | None = app.bot_data.get("translator")
    if tr:
        await tr.close()


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("123456"):
        raise SystemExit(
            "Нет токена. Создай бота в @BotFather, положи токен в файл .env:\n"
            "BOT_TOKEN=123456:ABC...\n"
        )
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    webhook_base = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if webhook_base:
        builder = builder.updater(None)
    app = builder.build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("langs", cmd_langs))
    app.add_handler(CommandHandler(["t", "tr", "translate"], cmd_t))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(InlineQueryHandler(on_inline))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND,
            on_private_text,
        )
    )
    app.add_error_handler(on_error)

    webhook_base = (os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if webhook_base:
        port = int(os.getenv("PORT", "8080"))
        logger.info("Starting webhook on port %s → %s/telegram", port, webhook_base)
        run_webhook(app, webhook_base, port)
        return

    logger.info("Starting polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def run_webhook(application: Application, base_url: str, port: int) -> None:
    from aiohttp import web

    async def keep_awake(url: str) -> None:
        await asyncio.sleep(20)
        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                try:
                    response = await client.get(f"{url}/health")
                    logger.info("keepalive ping %s", response.status_code)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("keepalive ping failed: %s", exc)
                await asyncio.sleep(8 * 60)

    async def telegram_webhook(request: web.Request) -> web.Response:
        try:
            data = await request.json()
            update = Update.de_json(data, application.bot)
            if update:
                await application.update_queue.put(update)
        except Exception:
            logger.exception("webhook update failed")
        return web.Response(text="ok")

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def diag(_: web.Request) -> web.Response:
        tr: Translator | None = application.bot_data.get("translator")
        if not tr:
            return web.json_response({"ok": False, "error": "translator not ready"}, status=503)
        try:
            result = await tr.translate("привет", "en")
            return web.json_response(
                {"ok": True, "provider": result.provider, "text": result.text}
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("diag translate failed")
            return web.json_response({"ok": False, "error": str(exc)}, status=500)

    async def on_startup(_: web.Application) -> None:
        await application.initialize()
        if application.post_init:
            await application.post_init(application)
        await application.start()
        await application.bot.set_webhook(
            url=f"{base_url}/telegram",
            allowed_updates=["message", "edited_message", "callback_query", "inline_query"],
            drop_pending_updates=False,
        )
        logger.info("Webhook set to %s/telegram", base_url)
        asyncio.create_task(keep_awake(base_url), name="keep_awake")

    async def on_cleanup(_: web.Application) -> None:
        if application.post_shutdown:
            await application.post_shutdown(application)
        await application.stop()
        await application.shutdown()

    web_app = web.Application()
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_app.router.add_get("/diag", diag)
    web_app.on_startup.append(on_startup)
    web_app.on_cleanup.append(on_cleanup)
    web.run_app(web_app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()
