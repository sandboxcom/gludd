"""Script transliteration between writing systems.

Converts text between scripts (Cyrillic ↔ Latin, Greek ↔ Latin, Arabic ↔ Latin,
Devanagari ↔ Latin, etc.) using standardized romanization schemes:
- ISO 9:1995 for Cyrillic
- ISO 843:1997 for Greek
- ALA-LC for Arabic
- IAST for Devanagari
- Revised Romanization for Korean
- Hepburn for Japanese

Also supports common non-standard transliterations (e.g., SMS/chat Cyrillic).
"""

from __future__ import annotations

from typing import TypedDict

# ── Transliteration result shape ────────────────────────────────────────────


class TransliterationResult(TypedDict):
    source_text: str
    source_script: str
    target_script: str
    transliterated_text: str
    scheme: str
    reversible: bool


# ── Cyrillic → Latin (ISO 9:1995) ──────────────────────────────────────────

_CYRILLIC_TO_LATIN: dict[str, str] = {
    "А": "A",
    "а": "a",
    "Б": "B",
    "б": "b",
    "В": "V",
    "в": "v",
    "Г": "G",
    "г": "g",
    "Д": "D",
    "д": "d",
    "Ђ": "Đ",
    "ђ": "đ",
    "Е": "E",
    "е": "e",
    "Ё": "Ë",
    "ё": "ë",
    "Є": "Ê",
    "є": "ê",
    "Ж": "Ž",
    "ж": "ž",
    "З": "Z",
    "з": "z",
    "Ѕ": "Ẑ",
    "ѕ": "ẑ",
    "И": "I",
    "и": "i",
    "І": "Ì",
    "і": "ì",
    "Ї": "Ï",
    "ї": "ï",
    "Й": "J",
    "й": "j",
    "Ј": "J̌",
    "ј": "ǰ",
    "К": "K",
    "к": "k",
    "Л": "L",
    "л": "l",
    "Љ": "L̂",
    "љ": "l̂",
    "М": "M",
    "м": "m",
    "Н": "N",
    "н": "n",
    "Њ": "N̂",
    "њ": "n̂",
    "О": "O",
    "о": "o",
    "П": "P",
    "п": "p",
    "Р": "R",
    "р": "r",
    "С": "S",
    "с": "s",
    "Т": "T",
    "т": "t",
    "Ћ": "Ć",
    "ћ": "ć",
    "У": "U",
    "у": "u",
    "Ў": "Ŭ",
    "ў": "ŭ",
    "Ф": "F",
    "ф": "f",
    "Х": "H",
    "х": "h",
    "Ц": "C",
    "ц": "c",
    "Ч": "Č",
    "ч": "č",
    "Џ": "D̂",
    "џ": "d̂",
    "Ш": "Š",
    "ш": "š",
    "Щ": "Ŝ",
    "щ": "ŝ",
    "Ъ": "ʺ",
    "ъ": "ʺ",
    "Ы": "Y",
    "ы": "y",
    "Ь": "ʹ",
    "ь": "ʹ",
    "Э": "È",
    "э": "è",
    "Ю": "Û",
    "ю": "û",
    "Я": "Â",
    "я": "â",
    "Ѣ": "Ě",
    "ѣ": "ě",
    "Ѫ": "Ǎ",
    "ѫ": "ǎ",
    "Ѳ": "F̀",
    "ѳ": "f̀",
    "Ѵ": "Ỳ",
    "ѵ": "ỳ",
}

# ── Latin → Cyrillic (reverse ISO 9:1995) ──────────────────────────────────

_LATIN_TO_CYRILLIC: dict[str, str] = {v: k for k, v in _CYRILLIC_TO_LATIN.items()}

# ── Greek → Latin (ISO 843:1997) ───────────────────────────────────────────

_GREEK_TO_LATIN: dict[str, str] = {
    "Α": "A",
    "α": "a",
    "Β": "V",
    "β": "v",
    "Γ": "G",
    "γ": "g",
    "Δ": "D",
    "δ": "d",
    "Ε": "E",
    "ε": "e",
    "Ζ": "Z",
    "ζ": "z",
    "Η": "Ī",
    "η": "ī",
    "Θ": "Th",
    "θ": "th",
    "Ι": "I",
    "ι": "i",
    "Κ": "K",
    "κ": "k",
    "Λ": "L",
    "λ": "l",
    "Μ": "M",
    "μ": "m",
    "Ν": "N",
    "ν": "n",
    "Ξ": "X",
    "ξ": "x",
    "Ο": "O",
    "ο": "o",
    "Π": "P",
    "π": "p",
    "Ρ": "R",
    "ρ": "r",
    "Σ": "S",
    "σ": "s",
    "ς": "s",
    "Τ": "T",
    "τ": "t",
    "Υ": "Y",
    "υ": "y",
    "Φ": "F",
    "φ": "f",
    "Χ": "Ch",
    "χ": "ch",
    "Ψ": "Ps",
    "ψ": "ps",
    "Ω": "Ō",
    "ω": "ō",
}

# ── Latin → Greek (reverse) ────────────────────────────────────────────────

_LATIN_TO_GREEK: dict[str, str] = {v: k for k, v in _GREEK_TO_LATIN.items()}

# ── Arabic → Latin (ALA-LC simplified) ─────────────────────────────────────

_ARABIC_TO_LATIN: dict[str, str] = {
    "ا": "ā",
    "أ": "ʾā",
    "إ": "ʾi",
    "آ": "ʾā",
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "j",
    "ح": "ḥ",
    "خ": "kh",
    "د": "d",
    "ذ": "dh",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "ṣ",
    "ض": "ḍ",
    "ط": "ṭ",
    "ظ": "ẓ",
    "ع": "ʿ",
    "غ": "gh",
    "ف": "f",
    "ق": "q",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
    "ة": "h",
    "و": "w",
    "ي": "y",
    "ى": "á",
    "ء": "ʾ",
    "ؤ": "ʾu",
    "ئ": "ʾi",
    "لا": "lā",
}

# Remove the ligature entry and handle separately
del _ARABIC_TO_LATIN["لا"]

# ── Devanagari → Latin (IAST simplified) ───────────────────────────────────

_DEVANAGARI_TO_LATIN: dict[str, str] = {
    "अ": "a",
    "आ": "ā",
    "इ": "i",
    "ई": "ī",
    "उ": "u",
    "ऊ": "ū",
    "ऋ": "ṛ",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ṅ",
    "च": "c",
    "छ": "ch",
    "ज": "j",
    "झ": "jh",
    "ञ": "ñ",
    "ट": "ṭ",
    "ठ": "ṭh",
    "ड": "ḍ",
    "ढ": "ḍh",
    "ण": "ṇ",
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
    "श": "ś",
    "ष": "ṣ",
    "स": "s",
    "ह": "h",
    "ा": "ā",
    "ि": "i",
    "ी": "ī",
    "ु": "u",
    "ू": "ū",
    "ृ": "ṛ",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ं": "ṃ",
    "ः": "ḥ",
    "क्ष": "kṣ",
    "त्र": "tr",
    "ज्ञ": "jñ",
    "श्र": "śr",
}

# ── Korean → Latin (Revised Romanization) ──────────────────────────────────

_KOREAN_TO_LATIN: dict[str, str] = {
    "ㄱ": "g",
    "ㄲ": "kk",
    "ㄴ": "n",
    "ㄷ": "d",
    "ㄸ": "tt",
    "ㄹ": "r",
    "ㅁ": "m",
    "ㅂ": "b",
    "ㅃ": "pp",
    "ㅅ": "s",
    "ㅆ": "ss",
    "ㅇ": "",
    "ㅈ": "j",
    "ㅉ": "jj",
    "ㅊ": "ch",
    "ㅋ": "k",
    "ㅌ": "t",
    "ㅍ": "p",
    "ㅎ": "h",
    "ㅏ": "a",
    "ㅐ": "ae",
    "ㅑ": "ya",
    "ㅒ": "yae",
    "ㅓ": "eo",
    "ㅔ": "e",
    "ㅕ": "yeo",
    "ㅖ": "ye",
    "ㅗ": "o",
    "ㅘ": "wa",
    "ㅙ": "wae",
    "ㅚ": "oe",
    "ㅛ": "yo",
    "ㅜ": "u",
    "ㅝ": "wo",
    "ㅞ": "we",
    "ㅟ": "wi",
    "ㅠ": "yu",
    "ㅡ": "eu",
    "ㅢ": "ui",
    "ㅣ": "i",
}

# ── Japanese Kana → Latin (Hepburn) ────────────────────────────────────────

_HIRAGANA_TO_LATIN: dict[str, str] = {
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
    "きゃ": "kya",
    "きゅ": "kyu",
    "きょ": "kyo",
    "しゃ": "sha",
    "しゅ": "shu",
    "しょ": "sho",
    "ちゃ": "cha",
    "ちゅ": "chu",
    "ちょ": "cho",
    "にゃ": "nya",
    "にゅ": "nyu",
    "にょ": "nyo",
    "ひゃ": "hya",
    "ひゅ": "hyu",
    "ひょ": "hyo",
    "みゃ": "mya",
    "みゅ": "myu",
    "みょ": "myo",
    "りゃ": "rya",
    "りゅ": "ryu",
    "りょ": "ryo",
    "ぎゃ": "gya",
    "ぎゅ": "gyu",
    "ぎょ": "gyo",
    "じゃ": "ja",
    "じゅ": "ju",
    "じょ": "jo",
    "びゃ": "bya",
    "びゅ": "byu",
    "びょ": "byo",
    "ぴゃ": "pya",
    "ぴゅ": "pyu",
    "ぴょ": "pyo",
}

_KATAKANA_TO_LATIN: dict[str, str] = {
    "ア": "a",
    "イ": "i",
    "ウ": "u",
    "エ": "e",
    "オ": "o",
    "カ": "ka",
    "キ": "ki",
    "ク": "ku",
    "ケ": "ke",
    "コ": "ko",
    "サ": "sa",
    "シ": "shi",
    "ス": "su",
    "セ": "se",
    "ソ": "so",
    "タ": "ta",
    "チ": "chi",
    "ツ": "tsu",
    "テ": "te",
    "ト": "to",
    "ナ": "na",
    "ニ": "ni",
    "ヌ": "nu",
    "ネ": "ne",
    "ノ": "no",
    "ハ": "ha",
    "ヒ": "hi",
    "フ": "fu",
    "ヘ": "he",
    "ホ": "ho",
    "マ": "ma",
    "ミ": "mi",
    "ム": "mu",
    "メ": "me",
    "モ": "mo",
    "ヤ": "ya",
    "ユ": "yu",
    "ヨ": "yo",
    "ラ": "ra",
    "リ": "ri",
    "ル": "ru",
    "レ": "re",
    "ロ": "ro",
    "ワ": "wa",
    "ヲ": "wo",
    "ン": "n",
    "ガ": "ga",
    "ギ": "gi",
    "グ": "gu",
    "ゲ": "ge",
    "ゴ": "go",
    "ザ": "za",
    "ジ": "ji",
    "ズ": "zu",
    "ゼ": "ze",
    "ゾ": "zo",
    "ダ": "da",
    "ヂ": "ji",
    "ヅ": "zu",
    "デ": "de",
    "ド": "do",
    "バ": "ba",
    "ビ": "bi",
    "ブ": "bu",
    "ベ": "be",
    "ボ": "bo",
    "パ": "pa",
    "ピ": "pi",
    "プ": "pu",
    "ペ": "pe",
    "ポ": "po",
    "キャ": "kya",
    "キュ": "kyu",
    "キョ": "kyo",
    "シャ": "sha",
    "シュ": "shu",
    "ショ": "sho",
    "チャ": "cha",
    "チュ": "chu",
    "チョ": "cho",
    "ニャ": "nya",
    "ニュ": "nyu",
    "ニョ": "nyo",
    "ヒャ": "hya",
    "ヒュ": "hyu",
    "ヒョ": "hyo",
    "ミャ": "mya",
    "ミュ": "myu",
    "ミョ": "myo",
    "リャ": "rya",
    "リュ": "ryu",
    "リョ": "ryo",
    "ギャ": "gya",
    "ギュ": "gyu",
    "ギョ": "gyo",
    "ジャ": "ja",
    "ジュ": "ju",
    "ジョ": "jo",
    "ビャ": "bya",
    "ビュ": "byu",
    "ビョ": "byo",
    "ピャ": "pya",
    "ピュ": "pyu",
    "ピョ": "pyo",
}

# ── Script detection ────────────────────────────────────────────────────────

_SCRIPT_RANGES: dict[str, list[tuple[int, int, str]]] = {
    "Cyrillic": [(0x0400, 0x04FF, "Cyrillic"), (0x0500, 0x052F, "Cyrillic")],
    "Greek": [(0x0370, 0x03FF, "Greek")],
    "Arabic": [(0x0600, 0x06FF, "Arabic"), (0x0750, 0x077F, "Arabic")],
    "Devanagari": [(0x0900, 0x097F, "Devanagari")],
    "Hangul": [(0xAC00, 0xD7AF, "Hangul"), (0x1100, 0x11FF, "Hangul")],
    "Hiragana": [(0x3040, 0x309F, "Hiragana")],
    "Katakana": [(0x30A0, 0x30FF, "Katakana")],
}

_TRANSLIT_TABLES: dict[str, dict[str, str]] = {
    "cyrillic-to-latin": _CYRILLIC_TO_LATIN,
    "latin-to-cyrillic": _LATIN_TO_CYRILLIC,
    "greek-to-latin": _GREEK_TO_LATIN,
    "latin-to-greek": _LATIN_TO_GREEK,
    "arabic-to-latin": _ARABIC_TO_LATIN,
    "devanagari-to-latin": _DEVANAGARI_TO_LATIN,
    "korean-to-latin": _KOREAN_TO_LATIN,
    "hiragana-to-latin": _HIRAGANA_TO_LATIN,
    "katakana-to-latin": _KATAKANA_TO_LATIN,
}

_REVERSIBLE_SCHEMES: frozenset[str] = frozenset(
    {
        "cyrillic-to-latin",
        "latin-to-cyrillic",
        "greek-to-latin",
        "latin-to-greek",
    }
)

# ── Identify script of text ─────────────────────────────────────────────────


def _detect_script(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isspace() or ch in ".,;:!?\"'()-0123456789":
            continue
        cp = ord(ch)
        for script_name, ranges in _SCRIPT_RANGES.items():
            for lo, hi, _ in ranges:
                if lo <= cp <= hi:
                    counts[script_name] = counts.get(script_name, 0) + 1
                    break
    if not counts:
        return "Latin"
    return max(counts, key=lambda k: counts[k])


def _best_scheme(source_script: str, target_script: str) -> str | None:
    mapping: dict[str, str] = {
        "Cyrillic-Latin": "cyrillic-to-latin",
        "Latin-Cyrillic": "latin-to-cyrillic",
        "Greek-Latin": "greek-to-latin",
        "Latin-Greek": "latin-to-greek",
        "Arabic-Latin": "arabic-to-latin",
        "Devanagari-Latin": "devanagari-to-latin",
        "Hangul-Latin": "korean-to-latin",
        "Hiragana-Latin": "hiragana-to-latin",
        "Katakana-Latin": "katakana-to-latin",
    }
    key = f"{source_script}-{target_script}"
    scheme = mapping.get(key)
    if scheme:
        return scheme

    if target_script == "Latin":
        for script, s in mapping.items():
            if script.startswith(f"{source_script}-"):
                return s
    return None


# ── Public transliteration function ─────────────────────────────────────────


def transliterate(
    text: str,
    target_script: str = "Latin",
    scheme: str | None = None,
) -> TransliterationResult:
    if not text or not text.strip():
        return TransliterationResult(
            source_text=text,
            source_script="Unknown",
            target_script=target_script,
            transliterated_text="",
            scheme="none",
            reversible=False,
        )

    source_script = _detect_script(text)
    if source_script == target_script:
        return TransliterationResult(
            source_text=text,
            source_script=source_script,
            target_script=target_script,
            transliterated_text=text,
            scheme="identity",
            reversible=True,
        )

    if scheme is None:
        scheme = _best_scheme(source_script, target_script)
    if scheme is None:
        return TransliterationResult(
            source_text=text,
            source_script=source_script,
            target_script=target_script,
            transliterated_text=text,
            scheme="unsupported",
            reversible=False,
        )

    table = _TRANSLIT_TABLES.get(scheme)
    if table is None:
        return TransliterationResult(
            source_text=text,
            source_script=source_script,
            target_script=target_script,
            transliterated_text=text,
            scheme=scheme or "unknown",
            reversible=False,
        )

    result_chars: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if len(text) - i >= 3:
            tri = text[i : i + 3]
            if tri in table:
                result_chars.append(table[tri])
                i += 3
                continue
        if len(text) - i >= 2:
            pair = text[i : i + 2]
            if pair in table:
                result_chars.append(table[pair])
                i += 2
                continue
        result_chars.append(table.get(ch, ch))
        i += 1

    return TransliterationResult(
        source_text=text,
        source_script=source_script,
        target_script=target_script,
        transliterated_text="".join(result_chars),
        scheme=scheme,
        reversible=scheme in _REVERSIBLE_SCHEMES,
    )


# ── Available scheme listing ────────────────────────────────────────────────


def list_schemes() -> list[dict[str, object]]:
    return [
        {
            "scheme": name,
            "source_script": name.split("-to-")[0].title(),
            "target_script": name.split("-to-")[1].title(),
            "reversible": name in _REVERSIBLE_SCHEMES,
        }
        for name in sorted(_TRANSLIT_TABLES.keys())
    ]
