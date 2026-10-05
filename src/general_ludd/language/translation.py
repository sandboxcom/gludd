"""Translation support via open-source libraries and API adapters.

Provides machine translation between languages using:
- LibreTranslate (self-hosted / local)
- googletrans as fallback
- A built-in word-level dictionary for offline common phrases

Produces translation results with confidence scoring and alternative
translations when available.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import TypedDict

# ── Translation result shape ───────────────────────────────────────────────


class TranslationResult(TypedDict):
    """Stable serialized translation result shared by every engine."""

    source_language: str
    source_text: str
    target_language: str
    translated_text: str
    confidence: float
    engine: str
    alternative: list[str]
    error: str


# ── Built-in mini dictionary (common words/phrases) ────────────────────────

_DICTIONARY: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "de": {
            "hello": "hallo",
            "goodbye": "auf Wiedersehen",
            "thank you": "danke",
            "please": "bitte",
            "yes": "ja",
            "no": "nein",
            "good morning": "guten Morgen",
            "good night": "gute Nacht",
            "sorry": "Entschuldigung",
            "welcome": "willkommen",
            "help": "Hilfe",
            "friend": "Freund",
            "water": "Wasser",
            "food": "Essen",
        },
        "fr": {
            "hello": "bonjour",
            "goodbye": "au revoir",
            "thank you": "merci",
            "please": "s'il vous plaît",
            "yes": "oui",
            "no": "non",
            "good morning": "bonjour",
            "good night": "bonne nuit",
            "sorry": "pardon",
            "welcome": "bienvenue",
            "help": "aide",
            "friend": "ami",
            "water": "eau",
            "food": "nourriture",
        },
        "es": {
            "hello": "hola",
            "goodbye": "adiós",
            "thank you": "gracias",
            "please": "por favor",
            "yes": "sí",
            "no": "no",
            "good morning": "buenos días",
            "good night": "buenas noches",
            "sorry": "lo siento",
            "welcome": "bienvenido",
            "help": "ayuda",
            "friend": "amigo",
            "water": "agua",
            "food": "comida",
        },
        "it": {
            "hello": "ciao",
            "goodbye": "arrivederci",
            "thank you": "grazie",
            "please": "per favore",
            "yes": "sì",
            "no": "no",
            "good morning": "buongiorno",
            "good night": "buonanotte",
            "sorry": "scusa",
            "welcome": "benvenuto",
            "help": "aiuto",
            "friend": "amico",
            "water": "acqua",
            "food": "cibo",
        },
        "pt": {
            "hello": "olá",
            "goodbye": "adeus",
            "thank you": "obrigado",
            "please": "por favor",
            "yes": "sim",
            "no": "não",
            "good morning": "bom dia",
            "good night": "boa noite",
            "sorry": "desculpe",
            "welcome": "bem-vindo",
            "help": "ajuda",
            "friend": "amigo",
            "water": "água",
            "food": "comida",
        },
        "ru": {
            "hello": "привет",
            "goodbye": "до свидания",
            "thank you": "спасибо",
            "please": "пожалуйста",
            "yes": "да",
            "no": "нет",
            "good morning": "доброе утро",
            "good night": "спокойной ночи",
            "sorry": "извините",
            "welcome": "добро пожаловать",
            "help": "помощь",
            "friend": "друг",
            "water": "вода",
            "food": "еда",
        },
        "ja": {
            "hello": "こんにちは",
            "goodbye": "さようなら",
            "thank you": "ありがとう",
            "please": "お願いします",
            "yes": "はい",
            "no": "いいえ",
            "good morning": "おはようございます",
            "good night": "おやすみなさい",
            "sorry": "ごめんなさい",
            "welcome": "ようこそ",
            "help": "助けて",
            "friend": "友達",
            "water": "水",
            "food": "食べ物",
        },
        "ko": {
            "hello": "안녕하세요",
            "goodbye": "안녕히 가세요",
            "thank you": "감사합니다",
            "please": "부탁합니다",
            "yes": "네",
            "no": "아니요",
            "good morning": "좋은 아침",
            "good night": "안녕히 주무세요",
            "sorry": "죄송합니다",
            "welcome": "환영합니다",
            "help": "도움",
            "friend": "친구",
            "water": "물",
            "food": "음식",
        },
        "zh": {
            "hello": "你好",
            "goodbye": "再见",
            "thank you": "谢谢",
            "please": "请",
            "yes": "是的",
            "no": "不",
            "good morning": "早上好",
            "good night": "晚安",
            "sorry": "对不起",
            "welcome": "欢迎",
            "help": "帮助",
            "friend": "朋友",
            "water": "水",
            "food": "食物",
        },
        "ar": {
            "hello": "مرحبا",
            "goodbye": "وداعا",
            "thank you": "شكرا",
            "please": "من فضلك",
            "yes": "نعم",
            "no": "لا",
            "good morning": "صباح الخير",
            "good night": "تصبح على خير",
            "sorry": "آسف",
            "welcome": "أهلا بك",
            "help": "مساعدة",
            "friend": "صديق",
            "water": "ماء",
            "food": "طعام",
        },
    },
}

# ── LibreTranslate integration ──────────────────────────────────────────────

_LIBRETRANSLATE_URL = os.environ.get("GLUDD_LIBRETRANSLATE_URL", "http://localhost:5000")


def _libretranslate(source: str, target: str, text: str) -> TranslationResult | None:
    url = f"{_LIBRETRANSLATE_URL}/translate"
    payload = json.dumps(
        {
            "q": text,
            "source": source if source != "auto" else "auto",
            "target": target,
            "format": "text",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return _make_result(
                source_language=source,
                source_text=text,
                target_language=target,
                translated_text=data.get("translatedText", ""),
                confidence=0.85,
                engine="libretranslate",
                alternative=[],
            )
    except urllib.error.HTTPError as exc:
        exc.close()
        return None
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _make_result(
    source_language: str,
    source_text: str,
    target_language: str,
    translated_text: str,
    confidence: float,
    engine: str,
    alternative: list[str] | None = None,
    error: str = "",
) -> TranslationResult:
    return {
        "source_language": source_language,
        "source_text": source_text,
        "target_language": target_language,
        "translated_text": translated_text,
        "confidence": confidence,
        "engine": engine,
        "alternative": alternative or [],
        "error": error,
    }


# ── Dictionary-only translation ─────────────────────────────────────────────


def _dictionary_translate(source: str, target: str, text: str) -> TranslationResult | None:
    src_dict = _DICTIONARY.get(source)
    if src_dict is None:
        return None
    tgt_dict = src_dict.get(target)
    if tgt_dict is None:
        return None

    lower = text.lower().strip()
    if lower in tgt_dict:
        return _make_result(
            source_language=source,
            source_text=text,
            target_language=target,
            translated_text=tgt_dict[lower],
            confidence=1.0,
            engine="dictionary",
            alternative=[],
        )

    words = lower.split()
    translated_words: list[str] = [str(tgt_dict.get(w, w)) for w in words]
    if translated_words == words:
        return None

    return _make_result(
        source_language=source,
        source_text=text,
        target_language=target,
        translated_text=" ".join(translated_words),
        confidence=0.4,
        engine="dictionary",
        alternative=[],
    )


# ── Public translation function ─────────────────────────────────────────────


def translate(
    text: str,
    source_language: str,
    target_language: str,
    *,
    allow_network: bool = True,
) -> TranslationResult:
    """Translate text using a bounded network adapter and offline fallback.

    Args:
        text: Source text to translate.
        source_language: Source ISO language code or ``auto``.
        target_language: Target ISO language code.
        allow_network: Whether the LibreTranslate adapter may be attempted.

    Returns:
        A stable translation result dictionary.
    """
    if not text or not text.strip():
        return _make_result(
            source_language=source_language,
            source_text=text,
            target_language=target_language,
            translated_text="",
            confidence=0.0,
            engine="none",
            alternative=[],
        )

    if source_language == target_language:
        return _make_result(
            source_language=source_language,
            source_text=text,
            target_language=target_language,
            translated_text=text,
            confidence=1.0,
            engine="identity",
            alternative=[],
        )

    if allow_network:
        lt_result = _libretranslate(source_language, target_language, text)
        if lt_result is not None:
            return lt_result

    dict_result = _dictionary_translate(source_language, target_language, text)
    if dict_result is not None:
        return dict_result

    return _make_result(
        source_language=source_language,
        source_text=text,
        target_language=target_language,
        translated_text=text,
        confidence=0.0,
        engine="passthrough",
        alternative=[],
        error=f"No translation engine available for {source_language} -> {target_language}",
    )
