from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

LANGS: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
    "uk": "Українська",
    "de": "Deutsch",
    "es": "Español",
    "fr": "Français",
    "it": "Italiano",
    "pl": "Polski",
    "tr": "Türkçe",
    "zh-CN": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
    "pt": "Português",
    "nl": "Nederlands",
    "cs": "Čeština",
    "sv": "Svenska",
    "fi": "Suomi",
    "el": "Ελληνικά",
    "he": "עברית",
    "hi": "हिन्दी",
    "id": "Indonesia",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "kk": "Қазақша",
    "be": "Беларуская",
}

LANG_ALIASES = {
    "zh": "zh-CN",
    "cn": "zh-CN",
    "ua": "uk",
    "jp": "ja",
    "kr": "ko",
    "uae": "ar",
}

CYRILLIC_RE = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґӘәҒғҚқҢңӨөҰұҮүҺһ]")
LATIN_RE = re.compile(r"[A-Za-z]")


@dataclass
class Translation:
    text: str
    source: str
    target: str
    provider: str


def normalize_lang(code: str | None) -> str | None:
    if not code:
        return None
    raw = code.strip().lower().replace("_", "-")
    if raw in LANG_ALIASES:
        return LANG_ALIASES[raw]
    if raw in LANGS:
        return raw
    if raw.startswith("zh"):
        return "zh-CN"
    return None


def looks_cyrillic(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    cyr = sum(1 for c in letters if CYRILLIC_RE.match(c))
    return cyr / len(letters) >= 0.3


def guess_source(text: str, native: str = "ru") -> str:
    if looks_cyrillic(text):
        return "ru" if native == "uk" and "ї" not in text.lower() and "є" not in text.lower() else (
            "uk" if any(c in text.lower() for c in "їєґ") else "ru"
        )
    if LATIN_RE.search(text):
        return "en"
    return "auto"


def smart_target(text: str, native: str = "ru", foreign: str = "en") -> str:
    source = guess_source(text, native)
    if source == native or (native == "ru" and looks_cyrillic(text)):
        return foreign
    return native


class Translator:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
            follow_redirects=True,
        )
        self._cache: dict[str, Translation] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def translate(self, text: str, target: str, source: str = "auto") -> Translation:
        text = text.strip()
        if not text:
            raise ValueError("Пустой текст")
        target_norm = normalize_lang(target) or target
        source_norm = normalize_lang(source) or source or "auto"
        key = hashlib.sha256(f"{source_norm}|{target_norm}|{text}".encode("utf-8")).hexdigest()
        cached = self._cache.get(key)
        if cached:
            return cached

        for provider in (self._google, self._google_chrome, self._mymemory):
            try:
                result = await provider(text, target_norm, source_norm)
                if self._usable(text, result, source_norm, target_norm):
                    self._cache[key] = result
                    if len(self._cache) > 500:
                        oldest = next(iter(self._cache))
                        self._cache.pop(oldest, None)
                    return result
            except Exception as exc:  # noqa: BLE001
                logger.warning("Translate via %s failed: %s", provider.__name__, exc)

        raise RuntimeError("Не удалось перевести. Попробуй ещё раз через пару секунд.")

    @staticmethod
    def _usable(original: str, result: Translation, source: str, target: str) -> bool:
        translated = (result.text or "").strip()
        if not translated:
            return False
        if "MYMEMORY WARNING" in translated.upper():
            return False
        if translated.lower() == original.strip().lower() and source != target:
            return False
        return True

    async def _google(self, text: str, target: str, source: str) -> Translation:
        response = await self._client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": source,
                "tl": target,
                "dt": "t",
                "dj": "1",
                "q": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        sentences = data.get("sentences") or []
        translated = "".join(part.get("trans", "") for part in sentences).strip()
        if not translated:
            raise RuntimeError("Google вернул пустой перевод")
        detected = data.get("src") or source
        return Translation(text=translated, source=detected, target=target, provider="google")

    async def _google_chrome(self, text: str, target: str, source: str) -> Translation:
        response = await self._client.get(
            "https://clients5.google.com/translate_a/t",
            params={
                "client": "dict-chrome-ex",
                "sl": source,
                "tl": target,
                "q": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        translated = ""
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, str):
                translated = first
            elif isinstance(first, list) and first:
                translated = str(first[0])
        if not translated:
            raise RuntimeError("Chrome Translate вернул пустой перевод")
        return Translation(text=translated.strip(), source=source, target=target, provider="google")

    async def _mymemory(self, text: str, target: str, source: str) -> Translation:
        pair_source = source if source != "auto" else guess_source(text)
        if pair_source == "auto":
            pair_source = "en"
        if pair_source == target:
            pair_source = "en" if target != "en" else "ru"
        response = await self._client.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": f"{pair_source}|{target}"},
        )
        response.raise_for_status()
        data = response.json()
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        translated = translated.strip()
        if not translated or translated.lower() == text.lower():
            matches = data.get("matches") or []
            for match in matches:
                candidate = (match.get("translation") or "").strip()
                if candidate:
                    translated = candidate
                    break
        if not translated:
            raise RuntimeError("MyMemory вернул пустой перевод")
        return Translation(text=translated, source=pair_source, target=target, provider="mymemory")
