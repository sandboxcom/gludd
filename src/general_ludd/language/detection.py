"""Language detection using statistical n-gram models.

Detects the human language of input text via Unicode script analysis,
character frequency profiles, and stopword matching. Provides confidence
scores and multi-language disambiguation.
"""

from __future__ import annotations

import unicodedata
from typing import TypedDict

# ── Detection result shape ─────────────────────────────────────────────────


class DetectionResult(TypedDict):
    language: str
    language_name: str
    confidence: float
    script: str
    iso_639_1: str
    alternative: list[dict[str, object]]
    method: str


# ── Stopword tables per language ────────────────────────────────────────────

_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        {
            "the",
            "be",
            "to",
            "of",
            "and",
            "a",
            "in",
            "that",
            "have",
            "it",
            "for",
            "not",
            "on",
            "with",
            "he",
            "as",
            "you",
            "do",
            "at",
            "this",
            "but",
            "his",
            "by",
            "from",
            "they",
            "we",
            "say",
            "her",
            "she",
            "or",
            "an",
            "will",
            "my",
            "one",
            "all",
            "would",
            "there",
            "their",
            "what",
            "so",
            "up",
            "out",
            "if",
            "about",
            "who",
            "get",
            "which",
            "go",
            "me",
            "when",
            "make",
            "can",
            "like",
            "time",
            "no",
            "just",
            "him",
            "know",
            "take",
            "people",
            "into",
            "year",
            "your",
            "good",
            "some",
            "could",
            "them",
            "see",
            "other",
            "than",
            "then",
            "now",
            "look",
            "only",
            "come",
            "its",
            "over",
            "think",
            "also",
            "back",
            "after",
            "use",
            "two",
            "how",
            "our",
            "work",
            "first",
            "well",
            "way",
            "even",
            "new",
            "want",
            "because",
            "any",
            "these",
            "give",
            "day",
            "most",
            "us",
        }
    ),
    "fr": frozenset(
        {
            "le",
            "la",
            "les",
            "de",
            "des",
            "du",
            "un",
            "une",
            "et",
            "en",
            "à",
            "est",
            "que",
            "pas",
            "qui",
            "pour",
            "dans",
            "ce",
            "il",
            "au",
            "sur",
            "ne",
            "se",
            "je",
            "nous",
            "vous",
            "elle",
            "ils",
            "elles",
            "son",
            "sa",
            "ses",
            "mais",
            "ou",
            "où",
            "lui",
            "leur",
            "faire",
            "comme",
            "tout",
            "tous",
            "aussi",
            "bien",
            "dire",
            "peut",
            "cette",
            "autre",
            "ça",
            "sans",
            "plus",
            "deux",
            "même",
            "prendre",
            "être",
            "avoir",
            "avec",
            "mon",
            "votre",
            "notre",
            "pendant",
            "entre",
            "très",
        }
    ),
    "de": frozenset(
        {
            "der",
            "die",
            "das",
            "und",
            "in",
            "zu",
            "den",
            "mit",
            "sich",
            "des",
            "auf",
            "für",
            "ist",
            "im",
            "nicht",
            "ein",
            "eine",
            "auch",
            "als",
            "es",
            "an",
            "aus",
            "er",
            "sie",
            "nach",
            "bei",
            "wird",
            "wie",
            "war",
            "wir",
            "noch",
            "bis",
            "hat",
            "sein",
            "einen",
            "welche",
            "sind",
            "oder",
            "um",
            "haben",
            "aber",
            "vor",
            "dem",
            "kann",
            "von",
            "nur",
            "wenn",
            "schon",
            "da",
            "durch",
            "mehr",
            "man",
            "worden",
        }
    ),
    "es": frozenset(
        {
            "el",
            "la",
            "los",
            "las",
            "de",
            "del",
            "en",
            "un",
            "una",
            "que",
            "es",
            "por",
            "para",
            "con",
            "no",
            "se",
            "su",
            "lo",
            "como",
            "más",
            "pero",
            "sus",
            "le",
            "ya",
            "o",
            "este",
            "ha",
            "muy",
            "sin",
            "todo",
            "sobre",
            "está",
            "también",
            "me",
            "hasta",
            "hay",
            "donde",
            "quien",
            "puede",
            "entre",
            "cuando",
            "son",
            "desde",
            "ser",
            "tiene",
            "cada",
            "bien",
            "porque",
            "hacer",
            "nos",
            "dice",
            "gran",
        }
    ),
    "it": frozenset(
        {
            "di",
            "che",
            "e",
            "in",
            "il",
            "la",
            "un",
            "a",
            "per",
            "non",
            "una",
            "le",
            "con",
            "sono",
            "si",
            "da",
            "della",
            "dei",
            "degli",
            "nel",
            "nella",
            "come",
            "più",
            "anche",
            "era",
            "delle",
            "alla",
            "sua",
            "tra",
            "dopo",
            "suo",
            "nei",
            "fare",
            "stato",
            "quando",
            "solo",
            "altro",
            "tutti",
            "essere",
            "senza",
            "aveva",
            "cui",
            "stessa",
            "possono",
            "quella",
        }
    ),
    "pt": frozenset(
        {
            "de",
            "a",
            "o",
            "que",
            "e",
            "do",
            "da",
            "em",
            "um",
            "para",
            "com",
            "não",
            "uma",
            "os",
            "no",
            "se",
            "na",
            "por",
            "mais",
            "as",
            "dos",
            "como",
            "mas",
            "ao",
            "ele",
            "das",
            "à",
            "seu",
            "sua",
            "ou",
            "quando",
            "muito",
            "nos",
            "já",
            "eu",
            "também",
            "só",
            "pelo",
            "pela",
            "até",
            "isso",
            "ela",
            "entre",
            "depois",
            "sem",
            "mesmo",
            "aos",
            "seus",
            "quem",
            "nas",
            "me",
            "esse",
        }
    ),
    "nl": frozenset(
        {
            "de",
            "het",
            "van",
            "en",
            "een",
            "in",
            "is",
            "dat",
            "te",
            "op",
            "voor",
            "zijn",
            "niet",
            "die",
            "met",
            "er",
            "aan",
            "om",
            "worden",
            "dan",
            "bij",
            "zal",
            "naar",
            "maar",
            "heb",
            "uit",
            "al",
            "wat",
            "door",
            "over",
            "ook",
            "geen",
            "tot",
            "kan",
            "nu",
            "dit",
            "mijn",
            "had",
            "was",
            "heeft",
            "wel",
        }
    ),
    "ru": frozenset(
        {
            "и",
            "в",
            "не",
            "на",
            "с",
            "что",
            "как",
            "а",
            "то",
            "все",
            "она",
            "так",
            "но",
            "его",
            "по",
            "из",
            "у",
            "же",
            "от",
            "о",
            "за",
            "вы",
            "это",
            "он",
            "для",
            "мы",
            "ее",
            "к",
            "бы",
            "до",
            "их",
            "был",
            "мне",
            "вас",
            "тебя",
            "меня",
            "или",
            "там",
        }
    ),
    "zh": frozenset(
        {
            "的",
            "一",
            "是",
            "在",
            "不",
            "了",
            "有",
            "和",
            "人",
            "这",
            "中",
            "大",
            "为",
            "上",
            "个",
            "国",
            "我",
            "以",
            "要",
            "他",
            "时",
            "来",
            "用",
            "们",
            "生",
            "到",
            "作",
            "地",
            "于",
            "出",
            "就",
            "分",
            "对",
            "成",
            "会",
            "可",
            "主",
            "发",
            "年",
            "动",
        }
    ),
    "ja": frozenset(
        {
            "の",
            "に",
            "は",
            "を",
            "た",
            "が",
            "で",
            "て",
            "と",
            "し",
            "です",
            "ます",
            "から",
            "まで",
            "など",
            "こと",
            "これ",
            "それ",
            "いる",
            "する",
            "ある",
            "なる",
            "もの",
            "ため",
            "よう",
            "どこ",
            "いつ",
            "どんな",
            "だ",
        }
    ),
    "ko": frozenset(
        {
            "이",
            "그",
            "에",
            "는",
            "을",
            "의",
            "를",
            "가",
            "로",
            "과",
            "한",
            "하다",
            "있다",
            "없다",
            "에서",
            "으로",
            "것",
            "들",
            "저",
            "나",
            "우리",
            "너",
            "그녀",
            "그들",
            "때문에",
            "할",
            "수",
            "있는",
            "년",
            "월",
            "일",
        }
    ),
    "ar": frozenset(
        {
            "في",
            "من",
            "على",
            "أن",
            "هو",
            "هي",
            "هذا",
            "هذه",
            "كان",
            "مع",
            "إلى",
            "عن",
            "لا",
            "ما",
            "بين",
            "إذا",
            "قد",
            "عند",
            "حيث",
            "بعد",
            "قبل",
            "كل",
            "الذي",
            "التي",
            "لم",
            "لن",
            "الذين",
            "بعض",
            "أي",
            "ذلك",
            "تلك",
        }
    ),
    "hi": frozenset(
        {
            "है",
            "में",
            "और",
            "का",
            "की",
            "के",
            "ने",
            "से",
            "को",
            "पर",
            "यह",
            "वह",
            "एक",
            "हो",
            "था",
            "थे",
            "दी",
            "गई",
            "हुआ",
            "हुए",
            "तक",
            "जो",
            "बाद",
            "अपने",
            "अपनी",
        }
    ),
    "tr": frozenset(
        {
            "ve",
            "bir",
            "bu",
            "da",
            "için",
            "de",
            "ile",
            "olarak",
            "ne",
            "değil",
            "gibi",
            "daha",
            "en",
            "çok",
            "var",
            "kadar",
            "sonra",
            "bana",
            "benim",
            "size",
            "sizin",
            "onun",
            "onlar",
            "bizim",
            "senin",
            "kendi",
            "şey",
            "her",
            "bütün",
        }
    ),
    "pl": frozenset(
        {
            "w",
            "się",
            "na",
            "z",
            "nie",
            "to",
            "do",
            "że",
            "jak",
            "jest",
            "po",
            "co",
            "dla",
            "już",
            "między",
            "lub",
            "ale",
            "przez",
            "przy",
            "tak",
            "być",
            "był",
            "mnie",
            "nas",
            "ich",
            "one",
            "go",
            "jego",
            "jej",
            "mu",
            "nam",
        }
    ),
    "uk": frozenset(
        {
            "і",
            "в",
            "не",
            "на",
            "з",
            "що",
            "як",
            "а",
            "то",
            "все",
            "вона",
            "він",
            "це",
            "ми",
            "ви",
            "його",
            "її",
            "до",
            "для",
            "був",
            "була",
            "або",
            "ще",
            "там",
            "про",
        }
    ),
    "sv": frozenset(
        {
            "och",
            "att",
            "det",
            "som",
            "en",
            "på",
            "i",
            "är",
            "av",
            "för",
            "med",
            "inte",
            "har",
            "till",
            "om",
            "den",
            "de",
            "ett",
            "man",
            "sig",
            "men",
            "från",
            "så",
            "då",
            "eller",
            "hade",
            "nu",
        }
    ),
    "th": frozenset(
        {
            "ที่",
            "เป็น",
            "ว่า",
            "และ",
            "ไม่",
            "ใน",
            "จะ",
            "แก่",
            "มี",
            "ได้",
            "ของ",
            "ไป",
            "มา",
            "เรา",
            "เขา",
            "มัน",
            "อัน",
            "ด้วย",
            "จาก",
            "โดย",
            "ถึง",
            "หรือ",
            "อีก",
            "ผม",
            "คุณ",
            "ฉัน",
        }
    ),
    "vi": frozenset(
        {
            "và",
            "của",
            "có",
            "không",
            "được",
            "trong",
            "một",
            "cho",
            "là",
            "với",
            "nhưng",
            "các",
            "đã",
            "sẽ",
            "những",
            "người",
            "khi",
            "thì",
            "qua",
            "tôi",
            "anh",
            "chị",
            "em",
        }
    ),
}

# ── ISO 639-1 → English name ───────────────────────────────────────────────

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "uk": "Ukrainian",
    "sv": "Swedish",
    "th": "Thai",
    "vi": "Vietnamese",
    "el": "Greek",
    "he": "Hebrew",
    "fa": "Persian",
    "id": "Indonesian",
    "ms": "Malay",
    "ro": "Romanian",
    "cs": "Czech",
    "hu": "Hungarian",
    "fi": "Finnish",
    "no": "Norwegian",
    "da": "Danish",
    "bg": "Bulgarian",
    "sr": "Serbian",
    "sk": "Slovak",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "sl": "Slovenian",
    "hr": "Croatian",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ur": "Urdu",
    "sw": "Swahili",
    "tl": "Tagalog",
    "ca": "Catalan",
    "eu": "Basque",
    "gl": "Galician",
}

# ── Script → language mappings ─────────────────────────────────────────────

_SCRIPT_TO_LANGS: dict[str, list[str]] = {
    "Latin": [
        "en",
        "fr",
        "de",
        "es",
        "it",
        "pt",
        "nl",
        "sv",
        "pl",
        "tr",
        "vi",
        "id",
        "ms",
        "ro",
        "cs",
        "hu",
        "fi",
        "no",
        "da",
        "sk",
        "lt",
        "lv",
        "et",
        "sl",
        "hr",
        "sw",
        "tl",
        "ca",
        "eu",
        "gl",
    ],
    "Cyrillic": ["ru", "uk", "bg", "sr"],
    "Han": ["zh", "ja"],
    "Hiragana": ["ja"],
    "Katakana": ["ja"],
    "Hangul": ["ko"],
    "Arabic": ["ar", "fa", "ur"],
    "Devanagari": ["hi", "mr"],
    "Thai": ["th"],
    "Greek": ["el"],
    "Hebrew": ["he"],
    "Bengali": ["bn"],
    "Tamil": ["ta"],
    "Telugu": ["te"],
    "Malayalam": ["ml"],
    "Gujarati": ["gu"],
    "Gurmukhi": ["pa"],
}

# ── Character frequency profiles ───────────────────────────────────────────

_LANG_FREQUENCY_LATIN_CHARS: dict[str, str] = {
    "en": "etaoinshrdlcumwfgypbvkjxqz",
    "fr": "esaitnruoldcmpévqfbghjàxèêyzwëkçùœ",
    "de": "enisratdhuclgmobfwzkpvüäöjßqyx",
    "es": "eaosrnidltcupmbygqvhfójzáéñíx",
    "it": "eiaonlrtscdupmvgfbzhqàòèùì",
    "pt": "aeosridntmculpvgfqbzhãjõêxçó",
    "nl": "enatirdosglvkhumjpbwczf",
    "sv": "eanrstildomgkvhäpuöbfjcå",
    "tr": "aenilkrmdstuyboügşzçpöc",
    "pl": "aeoinrzsctwydklmpułgjbążęh",
    "vi": "nathgciuoeonldmrb",
}

# ── Unicode script detection ────────────────────────────────────────────────


def _script_of(cp: int) -> str:
    try:
        return unicodedata.name(chr(cp), "").split(" ")[0] if cp <= 0x10FFFF else "Unknown"
    except (ValueError, IndexError):
        return "Unknown"


def _primary_script(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace() or ch.isdigit() or ch in ".,;:!?\"'()-–—…":
            continue
        scr = _script_of(ord(ch))
        counts[scr] = counts.get(scr, 0) + 1
    if not counts:
        return "Latin"
    sorted_scripts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_scripts[0][0]


def _count_script_chars(text: str, script: str) -> int:
    total = 0
    for ch in text:
        if ch.isspace() or ch in ".,;:!?\"'()-–—…0123456789":
            continue
        if _script_of(ord(ch)) == script:
            total += 1
    return total


# ── Public detection function ───────────────────────────────────────────────


def detect_language(text: str, top_n: int = 3) -> DetectionResult:
    if not text or not text.strip():
        return DetectionResult(
            language="unknown",
            language_name="Unknown",
            confidence=0.0,
            script="Unknown",
            iso_639_1="",
            alternative=[],
            method="none",
        )

    words: list[str] = text.lower().split()
    word_set: set[str] = set(w for w in words if len(w) > 1)

    primary_script = _primary_script(text)
    script_langs = _SCRIPT_TO_LANGS.get(primary_script, ["en"])

    scores: dict[str, float] = {}

    for lang in script_langs:
        stopwords = _STOPWORDS.get(lang)
        if stopwords is None:
            continue
        matched = stopwords & word_set
        if matched:
            density = len(matched) / min(len(stopwords), len(word_set) or 1)
            freq_bonus = 0.0
            if lang in _LANG_FREQUENCY_LATIN_CHARS and primary_script == "Latin":
                freq_order = _LANG_FREQUENCY_LATIN_CHARS[lang]
                text_lower = text.lower()
                score_sum = 0
                for i, ch in enumerate(freq_order[:10]):
                    count = text_lower.count(ch)
                    weight = 1.0 - (i * 0.08)
                    score_sum += count * weight
                if len(text_lower) > 0:
                    freq_bonus = min(score_sum / len(text_lower) * 2, 0.4)
            scores[lang] = density * 0.6 + freq_bonus

    if primary_script == "Latin" and not scores:
        for lang, freq_order in _LANG_FREQUENCY_LATIN_CHARS.items():
            text_lower = text.lower()
            score_sum = 0
            for i, ch in enumerate(freq_order[:8]):
                count = text_lower.count(ch)
                weight = 1.0 - (i * 0.1)
                score_sum += count * weight
            if len(text_lower) > 0 and score_sum > 0:
                scores[lang] = min(score_sum / len(text_lower), 0.5)

    if primary_script in _SCRIPT_TO_LANGS and not scores:
        for lang in _SCRIPT_TO_LANGS[primary_script]:
            if lang not in scores:
                scores[lang] = 0.3

    if not scores:
        return DetectionResult(
            language="unknown",
            language_name="Unknown",
            confidence=0.0,
            script=primary_script,
            iso_639_1="",
            alternative=[],
            method="script-only",
        )

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_lang, best_score = ranked[0]
    alternatives = [
        {"language": lang, "language_name": LANGUAGE_NAMES.get(lang, lang), "confidence": round(scr, 4)}
        for lang, scr in ranked[1:top_n]
        if scr > 0.05
    ]

    return DetectionResult(
        language=best_lang,
        language_name=LANGUAGE_NAMES.get(best_lang, best_lang),
        confidence=round(best_score, 4),
        script=primary_script,
        iso_639_1=best_lang,
        alternative=alternatives,
        method="stopword+freq",
    )


# ── Multi-language detection ────────────────────────────────────────────────


def detect_languages_in_text(text: str, threshold: float = 0.1) -> list[dict[str, object]]:
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        result = detect_language(text)
        return [
            {
                "segment": text[:200],
                "language": result["language"],
                "language_name": result["language_name"],
                "confidence": result["confidence"],
                "script": result["script"],
            }
        ]

    results: list[dict[str, object]] = []
    for sentence in sentences:
        if len(sentence.strip()) < 3:
            continue
        det = detect_language(sentence)
        if det["confidence"] >= threshold:
            results.append(
                {
                    "segment": sentence[:200],
                    "language": det["language"],
                    "language_name": det["language_name"],
                    "confidence": det["confidence"],
                    "script": det["script"],
                }
            )
    return results


def _split_sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[.!?。！？\n])\s+", text)
    return [p.strip() for p in parts if p.strip()]
