"""Language expert core: detection, translation, and transliteration.

:class:`LanguageDetector` — detect language from text (mock backend).
:class:`Translator` — translate between languages (mock backend).
:class:`Transliterator` — script conversion (mock backend).
"""

from __future__ import annotations

import re

from general_ludd.language.contracts import (
    LanguageDetectionResult,
    TranslationResult,
    TransliterationResult,
)

# ── Unicode script ranges ────────────────────────────────────────────────

_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0400, 0x04FF, "Cyrillic"),
    (0x0500, 0x052F, "Cyrillic"),
    (0x0600, 0x06FF, "Arabic"),
    (0x0750, 0x077F, "Arabic"),
    (0x0900, 0x097F, "Devanagari"),
    (0x3000, 0x303F, "Japanese"),
    (0x3040, 0x309F, "Japanese"),
    (0x30A0, 0x30FF, "Japanese"),
    (0x4E00, 0x9FFF, "Chinese"),
    (0xAC00, 0xD7AF, "Korean"),
]

_LANG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "en": ("the", "is", "are", "and", "you", "have", "that", "for"),
    "es": ("el", "la", "los", "las", "que", "de", "en", "con", "por", "qué", "es", "una", "un", "hola"),
    "fr": ("le", "la", "les", "des", "est", "que", "pas", "une", "dans", "vous"),
    "de": (
        "der",
        "die",
        "das",
        "ist",
        "sind",
        "und",
        "nicht",
        "mit",
        "sich",
        "ein",
        "eine",
        "ihnen",
        "es",
        "guten",
        "tag",
        "wie",
        "geht",
    ),
    "pt": ("o", "a", "os", "as", "que", "de", "da", "do", "em", "não", "para"),
    "it": ("il", "la", "i", "le", "di", "che", "non", "una", "per", "con", "sono"),
    "ru": ("что", "это", "как", "все", "его", "она", "они", "есть", "быть"),
    "ar": ("في", "من", "على", "كان", "هذا", "أن", "التي", "مع"),
    "zh": ("的", "是", "在", "了", "有", "和", "人", "这", "中", "大"),
    "ja": ("です", "ます", "した", "ない", "いる", "ある", "こと"),
    "ko": ("입니다", "있는", "하는", "그리고", "그것", "사람"),
}

_SCRIPT_TO_LANG: dict[str, str] = {
    "Cyrillic": "ru",
    "Chinese": "zh",
    "Japanese": "ja",
    "Korean": "ko",
    "Arabic": "ar",
    "Devanagari": "hi",
}

# ── Transliteration maps ─────────────────────────────────────────────────

_CYR_TO_LAT: dict[str, str] = {
    "А": "A",
    "Б": "B",
    "В": "V",
    "Г": "G",
    "Д": "D",
    "Е": "E",
    "Ё": "Yo",
    "Ж": "Zh",
    "З": "Z",
    "И": "I",
    "Й": "Y",
    "К": "K",
    "Л": "L",
    "М": "M",
    "Н": "N",
    "О": "O",
    "П": "P",
    "Р": "R",
    "С": "S",
    "Т": "T",
    "У": "U",
    "Ф": "F",
    "Х": "Kh",
    "Ц": "Ts",
    "Ч": "Ch",
    "Ш": "Sh",
    "Щ": "Shch",
    "Ъ": "",
    "Ы": "Y",
    "Ь": "",
    "Э": "E",
    "Ю": "Yu",
    "Я": "Ya",
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

_AR_TO_LAT: dict[str, str] = {
    "ا": "a",
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "j",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "dh",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "ع": "'",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "و": "w",
    "ي": "y",
    "ة": "a",
    "أ": "a",
    "إ": "i",
    "آ": "aa",
    "ى": "a",
    "ئ": "'",
    "ـ": "",
}

_DEV_TO_LAT: dict[str, str] = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ii",
    "उ": "u",
    "ऊ": "uu",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "ा": "aa",
    "ि": "i",
    "ी": "ii",
    "ु": "u",
    "ू": "uu",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ं": "n",
    "ः": "h",
    "नमस्ते": "namaste",
}

_JA_TO_LAT: dict[str, str] = {
    "あ": "a",
    "い": "i",
    "う": "u",
    "え": "e",
    "お": "o",
    "か": "ka",
    "き": "ki",
    "く": "ku",
    "け": "ke",
    "こ": "ko",
    "さ": "sa",
    "し": "shi",
    "す": "su",
    "せ": "se",
    "そ": "so",
    "た": "ta",
    "ち": "chi",
    "つ": "tsu",
    "て": "te",
    "と": "to",
    "な": "na",
    "に": "ni",
    "ぬ": "nu",
    "ね": "ne",
    "の": "no",
    "は": "ha",
    "ひ": "hi",
    "ふ": "fu",
    "へ": "he",
    "ほ": "ho",
    "ま": "ma",
    "み": "mi",
    "む": "mu",
    "め": "me",
    "も": "mo",
    "や": "ya",
    "ゆ": "yu",
    "よ": "yo",
    "ら": "ra",
    "り": "ri",
    "る": "ru",
    "れ": "re",
    "ろ": "ro",
    "わ": "wa",
    "を": "wo",
    "ん": "n",
    "が": "ga",
    "ぎ": "gi",
    "ぐ": "gu",
    "げ": "ge",
    "ご": "go",
    "ざ": "za",
    "じ": "ji",
    "ず": "zu",
    "ぜ": "ze",
    "ぞ": "zo",
    "だ": "da",
    "ぢ": "ji",
    "づ": "zu",
    "で": "de",
    "ど": "do",
    "ば": "ba",
    "び": "bi",
    "ぶ": "bu",
    "べ": "be",
    "ぼ": "bo",
    "ぱ": "pa",
    "ぴ": "pi",
    "ぷ": "pu",
    "ぺ": "pe",
    "ぽ": "po",
    "っ": "",
}

_VALID_TARGET_SCRIPTS: set[str] = {"Latin", "Cyrillic", "Arabic", "Devanagari", "Chinese", "Japanese", "Korean"}

_SCRIPT_TRANSLIT_MAPS: dict[str, dict[str, str]] = {
    "Cyrillic": _CYR_TO_LAT,
    "Arabic": _AR_TO_LAT,
    "Devanagari": _DEV_TO_LAT,
    "Japanese": _JA_TO_LAT,
}


# ── LanguageDetector ─────────────────────────────────────────────────────


class LanguageDetector:
    """Detects language from text using a mock keyword-based backend."""

    def detect(self, text: str) -> LanguageDetectionResult:
        _require_nonempty(text)
        script = _detect_script(text)
        lang_code = _classify(text, script)
        return LanguageDetectionResult(
            language_code=lang_code,
            confidence=_confidence_for(text, lang_code),
            script=script if script != "Latin" else None,
            region=_region_for(lang_code),
            detection_method="mock-keyword",
        )

    def detect_batch(self, texts: list[str]) -> list[LanguageDetectionResult]:
        if not texts:
            raise ValueError("detect_batch requires at least one text")
        return [self.detect(t) for t in texts]


# ── Translator ───────────────────────────────────────────────────────────


class Translator:
    """Translates text between languages using a mock backend."""

    def __init__(self, backend: str = "mock") -> None:
        self.backend = backend

    def translate(self, text: str, source_lang: str, target_lang: str) -> TranslationResult:
        _require_nonempty(text)
        if source_lang == target_lang:
            raise ValueError("Cannot translate to the same language")
        translated = f"[{target_lang.upper()}] {text}"
        return TranslationResult(
            translated_text=translated,
            source_lang=source_lang,
            target_lang=target_lang,
            confidence=0.85,
        )

    def translate_batch(self, texts: list[str], source_lang: str, target_lang: str) -> list[TranslationResult]:
        if not texts:
            raise ValueError("translate_batch requires at least one text")
        return [self.translate(t, source_lang, target_lang) for t in texts]


# ── Transliterator ───────────────────────────────────────────────────────


class Transliterator:
    """Converts text between writing scripts using character mapping."""

    def __init__(self, backend: str = "mock") -> None:
        self.backend = backend

    def detect_script(self, text: str) -> str:
        if not text:
            return "Unknown"
        return _detect_script(text)

    def transliterate(self, text: str, target_script: str, scheme: str | None = None) -> TransliterationResult:
        _require_nonempty(text)
        if target_script not in _VALID_TARGET_SCRIPTS:
            raise ValueError(f"Unknown target script: {target_script}")
        source = _detect_script(text)
        if source == target_script:
            raise ValueError("Cannot transliterate to the same script")
        out = _apply_translit(text, source, target_script)
        return TransliterationResult(
            transliterated_text=out,
            source_script=source,
            target_script=target_script,
            scheme=scheme,
        )

    def transliterate_batch(
        self, texts: list[str], target_script: str, scheme: str | None = None
    ) -> list[TransliterationResult]:
        if not texts:
            raise ValueError("transliterate_batch requires at least one text")
        return [self.transliterate(t, target_script, scheme) for t in texts]


# ── helpers ──────────────────────────────────────────────────────────────


def _require_nonempty(text: str) -> None:
    if not text.strip():
        raise ValueError("Text must not be empty")


def _detect_script(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        for lo, hi, name in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                return name
    return "Latin"


def _classify(text: str, script: str) -> str:
    lower = text.lower()
    best_lang = "en"
    best_score = 0
    for lang, keywords in _LANG_KEYWORDS.items():
        score = 0
        for kw in keywords:
            kw_lower = kw.lower()
            if re.search(r"\b" + re.escape(kw_lower) + r"\b", lower):
                score += 1
        if score > best_score:
            best_score = score
            best_lang = lang
    if best_score == 0 and script in _SCRIPT_TO_LANG:
        best_lang = _SCRIPT_TO_LANG[script]
    return best_lang


def _confidence_for(text: str, lang: str) -> float:
    keywords = _LANG_KEYWORDS.get(lang, ())
    if not keywords:
        return 0.7
    lower = text.lower()
    matches = 0
    for kw in keywords:
        kw_lower = kw.lower()
        if re.search(r"\b" + re.escape(kw_lower) + r"\b", lower):
            matches += 1
    return min(1.0, max(0.55, matches / max(len(keywords), 1) + 0.4))


def _region_for(lang: str) -> str | None:
    return {
        "en": "US",
        "fr": "FR",
        "es": "ES",
        "de": "DE",
        "pt": "BR",
        "it": "IT",
        "ru": "RU",
        "ar": "SA",
        "zh": "CN",
        "ja": "JP",
        "ko": "KR",
        "hi": "IN",
    }.get(lang)


def _lang_name(code: str) -> str:
    return {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "pt": "Portuguese",
        "it": "Italian",
        "ru": "Russian",
        "ar": "Arabic",
        "zh": "Chinese",
        "ja": "Japanese",
        "ko": "Korean",
        "hi": "Hindi",
    }.get(code, code.upper())


def _apply_translit(text: str, source: str, target: str) -> str:
    if source == target:
        return text
    if source in _SCRIPT_TRANSLIT_MAPS and target == "Latin":
        tmap = _SCRIPT_TRANSLIT_MAPS[source]
        return "".join(tmap.get(ch, ch) for ch in text)
    if source == "Latin" and target == "Cyrillic":
        return _latin_to_cyrillic(text)
    return f"[{target}] {text}"


def _latin_to_cyrillic(text: str) -> str:
    reverse: dict[str, str] = {}
    for cyr, lat in _CYR_TO_LAT.items():
        if lat and lat not in reverse:
            reverse[lat] = cyr
            reverse[lat.lower()] = cyr.lower()
    out_chars: list[str] = []
    i = 0
    while i < len(text):
        matched = False
        for seg_len in (3, 2, 1):
            if i + seg_len <= len(text):
                seg = text[i : i + seg_len]
                if seg in reverse:
                    out_chars.append(reverse[seg])
                    i += seg_len
                    matched = True
                    break
        if not matched:
            out_chars.append(text[i])
            i += 1
    return "".join(out_chars)
