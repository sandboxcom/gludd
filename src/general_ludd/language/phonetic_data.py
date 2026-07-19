"""IPA/X-SAMPA/ARPABET tables, phonetic algorithms, CMU Pronouncing Dictionary.

Covers:
- IPA vowel chart (cardinal vowels, central vowels, diphthongs)
- IPA consonant chart (plosives, fricatives, nasals, liquids, glides)
- IPA suprasegmentals (stress, length, tone, intonation)
- X-SAMPA to IPA mapping
- ARPABET phoneme set (CMU standard 39-phoneme inventory)
- Soundex algorithm rules
- Metaphone primary/secondary code rules
- Double Metaphone encoding
- CMU Pronouncing Dictionary subset
- Daitch-Mokotoff Soundex
"""

from __future__ import annotations

from typing import TypedDict


class PhonemeEntry(TypedDict):
    ipa: str
    xsampa: str
    arpabet: str
    description: str
    examples: str


IPA_VOWELS: list[PhonemeEntry] = [
    {
        "ipa": "i", "xsampa": "i", "arpabet": "IY",
        "description": "close front unrounded",
        "examples": "fleece, see",
    },
    {
        "ipa": "y", "xsampa": "y", "arpabet": "IW",
        "description": "close front rounded",
        "examples": "French tu, German uber",
    },
    {
        "ipa": "\u0268", "xsampa": "1", "arpabet": "IH",
        "description": "close central unrounded",
        "examples": "Russian ty",
    },
    {
        "ipa": "\u0289", "xsampa": "}", "arpabet": "UH",
        "description": "close central rounded",
        "examples": "Swedish hus",
    },
    {
        "ipa": "\u026f", "xsampa": "M", "arpabet": "UW",
        "description": "close back unrounded",
        "examples": "Korean eu",
    },
    {
        "ipa": "u", "xsampa": "u", "arpabet": "UW",
        "description": "close back rounded",
        "examples": "goose, through",
    },
    {
        "ipa": "\u026a", "xsampa": "I", "arpabet": "IH",
        "description": "near-close near-front unrounded",
        "examples": "kit, bit",
    },
    {
        "ipa": "\u028f", "xsampa": "Y", "arpabet": "UH",
        "description": "near-close near-front rounded",
        "examples": "German fullen",
    },
    {
        "ipa": "\u028a", "xsampa": "U", "arpabet": "UH",
        "description": "near-close near-back rounded",
        "examples": "foot, good",
    },
    {
        "ipa": "e", "xsampa": "e", "arpabet": "EY",
        "description": "close-mid front unrounded",
        "examples": "face, bait (GA)",
    },
    {
        "ipa": "\u00f8", "xsampa": "2", "arpabet": "ER",
        "description": "close-mid front rounded",
        "examples": "French peu",
    },
    {
        "ipa": "\u0258", "xsampa": "@\\", "arpabet": "AH",
        "description": "close-mid central unrounded",
        "examples": "Vietnamese mo",
    },
    {
        "ipa": "\u0275", "xsampa": "8", "arpabet": "OW",
        "description": "close-mid central rounded",
        "examples": "Swedish bus",
    },
    {
        "ipa": "\u0264", "xsampa": "7", "arpabet": "OW",
        "description": "close-mid back unrounded",
        "examples": "Vietnamese to",
    },
    {
        "ipa": "o", "xsampa": "o", "arpabet": "OW",
        "description": "close-mid back rounded",
        "examples": "goat, no (GA)",
    },
    {
        "ipa": "\u0259", "xsampa": "@", "arpabet": "AH",
        "description": "mid central (schwa)",
        "examples": "comma, about",
    },
    {
        "ipa": "\u025b", "xsampa": "E", "arpabet": "EH",
        "description": "open-mid front unrounded",
        "examples": "dress, bed",
    },
    {
        "ipa": "\u0153", "xsampa": "9", "arpabet": "ER",
        "description": "open-mid front rounded",
        "examples": "French neuf",
    },
    {
        "ipa": "\u025c", "xsampa": "3", "arpabet": "ER",
        "description": "open-mid central unrounded",
        "examples": "nurse (RP)",
    },
    {
        "ipa": "\u025e", "xsampa": "3\\", "arpabet": "AO",
        "description": "open-mid central rounded",
        "examples": "Irish English",
    },
    {
        "ipa": "\u028c", "xsampa": "V", "arpabet": "AH",
        "description": "open-mid back unrounded",
        "examples": "strut, cup",
    },
    {
        "ipa": "\u0254", "xsampa": "O", "arpabet": "AO",
        "description": "open-mid back rounded",
        "examples": "thought, caught (GA)",
    },
    {
        "ipa": "\u00e6", "xsampa": "\\{", "arpabet": "AE",
        "description": "near-open front unrounded",
        "examples": "trap, cat",
    },
    {
        "ipa": "\u0250", "xsampa": "6", "arpabet": "AA",
        "description": "near-open central",
        "examples": "German besser",
    },
    {
        "ipa": "a", "xsampa": "a", "arpabet": "AA",
        "description": "open front unrounded",
        "examples": "French patte",
    },
    {
        "ipa": "\u0251", "xsampa": "A", "arpabet": "AA",
        "description": "open back unrounded",
        "examples": "palm, father",
    },
    {
        "ipa": "\u0252", "xsampa": "Q", "arpabet": "AO",
        "description": "open back rounded",
        "examples": "lot, hot (RP)",
    },
]


IPA_CONSONANTS: list[PhonemeEntry] = [
    {
        "ipa": "p", "xsampa": "p", "arpabet": "P",
        "description": "voiceless bilabial plosive",
        "examples": "pat, spin",
    },
    {
        "ipa": "b", "xsampa": "b", "arpabet": "B",
        "description": "voiced bilabial plosive",
        "examples": "bat, web",
    },
    {
        "ipa": "t", "xsampa": "t", "arpabet": "T",
        "description": "voiceless alveolar plosive",
        "examples": "tap, stop",
    },
    {
        "ipa": "d", "xsampa": "d", "arpabet": "D",
        "description": "voiced alveolar plosive",
        "examples": "dog, bed",
    },
    {
        "ipa": "k", "xsampa": "k", "arpabet": "K",
        "description": "voiceless velar plosive",
        "examples": "cat, skill",
    },
    {
        "ipa": "g", "xsampa": "g", "arpabet": "G",
        "description": "voiced velar plosive",
        "examples": "go, bag",
    },
    {
        "ipa": "m", "xsampa": "m", "arpabet": "M",
        "description": "bilabial nasal",
        "examples": "man, ham",
    },
    {
        "ipa": "n", "xsampa": "n", "arpabet": "N",
        "description": "alveolar nasal",
        "examples": "no, tin",
    },
    {
        "ipa": "\u014b", "xsampa": "N", "arpabet": "NG",
        "description": "velar nasal",
        "examples": "sing, long",
    },
    {
        "ipa": "f", "xsampa": "f", "arpabet": "F",
        "description": "voiceless labiodental fricative",
        "examples": "fan, leaf",
    },
    {
        "ipa": "v", "xsampa": "v", "arpabet": "V",
        "description": "voiced labiodental fricative",
        "examples": "van, leave",
    },
    {
        "ipa": "\u03b8", "xsampa": "T", "arpabet": "TH",
        "description": "voiceless dental fricative",
        "examples": "thin, path",
    },
    {
        "ipa": "\u00f0", "xsampa": "D", "arpabet": "DH",
        "description": "voiced dental fricative",
        "examples": "this, bathe",
    },
    {
        "ipa": "s", "xsampa": "s", "arpabet": "S",
        "description": "voiceless alveolar fricative",
        "examples": "see, pass",
    },
    {
        "ipa": "z", "xsampa": "z", "arpabet": "Z",
        "description": "voiced alveolar fricative",
        "examples": "zoo, rose",
    },
    {
        "ipa": "\u0283", "xsampa": "S", "arpabet": "SH",
        "description": "voiceless postalveolar fricative",
        "examples": "she, ash",
    },
    {
        "ipa": "\u0292", "xsampa": "Z", "arpabet": "ZH",
        "description": "voiced postalveolar fricative",
        "examples": "measure, beige",
    },
    {
        "ipa": "h", "xsampa": "h", "arpabet": "HH",
        "description": "voiceless glottal fricative",
        "examples": "hat, ahead",
    },
    {
        "ipa": "r", "xsampa": "r", "arpabet": "R",
        "description": "alveolar trill",
        "examples": "Spanish perro",
    },
    {
        "ipa": "\u0279", "xsampa": "r\\", "arpabet": "R",
        "description": "alveolar approximant",
        "examples": "red, far (GA)",
    },
    {
        "ipa": "l", "xsampa": "l", "arpabet": "L",
        "description": "alveolar lateral approximant",
        "examples": "leg, bell",
    },
    {
        "ipa": "j", "xsampa": "j", "arpabet": "Y",
        "description": "palatal approximant",
        "examples": "yes, onion",
    },
    {
        "ipa": "w", "xsampa": "w", "arpabet": "W",
        "description": "labio-velar approximant",
        "examples": "wet, queen",
    },
    {
        "ipa": "\u02a7", "xsampa": "tS", "arpabet": "CH",
        "description": "voiceless postalveolar affricate",
        "examples": "chain, catch",
    },
    {
        "ipa": "\u02a4", "xsampa": "dZ", "arpabet": "JH",
        "description": "voiced postalveolar affricate",
        "examples": "judge, age",
    },
    {
        "ipa": "\u0294", "xsampa": "?", "arpabet": "Q",
        "description": "glottal stop",
        "examples": "uh-oh, button (Cockney)",
    },
]


ARPABET_TO_IPA: dict[str, str] = {
    "AA": "\u0251", "AE": "\u00e6", "AH": "\u028c", "AO": "\u0254",
    "AW": "a\u028a", "AY": "a\u026a", "B": "b", "CH": "\u02a7",
    "D": "d", "DH": "\u00f0", "EH": "\u025b", "ER": "\u025d",
    "EY": "e\u026a", "F": "f", "G": "g", "HH": "h",
    "IH": "\u026a", "IY": "i", "JH": "\u02a4", "K": "k",
    "L": "l", "M": "m", "N": "n", "NG": "\u014b",
    "OW": "o\u028a", "OY": "\u0254\u026a", "P": "p", "R": "\u0279",
    "S": "s", "SH": "\u0283", "T": "t", "TH": "\u03b8",
    "UH": "\u028a", "UW": "u", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "\u0292",
}


IPA_TO_ARPABET: dict[str, str] = {v: k for k, v in ARPABET_TO_IPA.items()}


ARPABET_STRESS: dict[str, str] = {
    "0": "no stress",
    "1": "primary stress",
    "2": "secondary stress",
}


SOUNDEX_MAPPING: dict[str, str] = {
    "b": "1", "f": "1", "p": "1", "v": "1",
    "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
    "d": "3", "t": "3",
    "l": "4",
    "m": "5", "n": "5",
    "r": "6",
}


SOUNDEX_VOWELS: set[str] = {"a", "e", "i", "o", "u", "y"}
SOUNDEX_IGNORE: set[str] = {"h", "w"}


METAPHONE_VOWELS: set[str] = {"a", "e", "i", "o", "u"}

METAPHONE_EXCEPTIONS: dict[str, str] = {
    "gn": "n",
    "kn": "n",
    "pn": "n",
    "wr": "r",
    "wh": "w",
}

DOUBLE_METAPHONE: dict[str, list[str]] = {
    "kn": ["n", "n"], "gn": ["n", "n"], "pn": ["n", "n"],
    "ae": ["k", "k"], "wr": ["r", "r"], "wh": ["w", ""],
    "x": ["ks", "ks"], "sch": ["sk", "x"],
}

# CMU Pronouncing Dictionary subset (ARPABET)
CMU_DICT_SUBSET: dict[str, list[str]] = {
    "HELLO": ["HH AH0 L OW1"],
    "WORLD": ["W ER1 L D"],
    "DATA": ["D EY1 T AH0", "D AE1 T AH0"],
    "UNICODE": ["Y UW1 N IH0 K OW2 D"],
    "LANGUAGE": ["L AE1 NG G W AH0 JH"],
    "FONT": ["F AA1 N T"],
    "PHONETIC": ["F AH0 N EH1 T IH0 K"],
    "ENCODING": ["EH0 N K OW1 D IH0 NG"],
    "CHARACTER": ["K EH1 R IH0 K T ER0"],
    "STRING": ["S T R IH1 NG"],
    "BYTE": ["B AY1 T"],
    "TEXT": ["T EH1 K S T"],
    "LOCALE": ["L OW0 K AE1 L"],
    "SCRIPT": ["S K R IH1 P T"],
    "SPEECH": ["S P IY1 CH"],
    "SYNTHESIS": ["S IH1 N TH AH0 S AH0 S"],
}


def compute_soundex(word: str) -> str:
    """Compute the 4-character Soundex code for an English word.

    Standard NARA Soundex algorithm:
    1. Keep the first letter (uppercase).
    2. Map remaining letters to digits via SOUNDEX_MAPPING.
    3. Drop vowels (a/e/i/o/u/y) and h/w entirely.
    4. Collapse adjacent same-digit codes.
    5. Pad with zeros to 4 chars; truncate to 4.

    Returns "" for empty input.
    """
    if not word:
        return ""

    cleaned = "".join(c for c in word.upper() if c.isalpha())
    if not cleaned:
        return ""

    code = cleaned[0]
    prev_digit = SOUNDEX_MAPPING.get(code[0].lower(), "")

    for ch in cleaned[1:]:
        lower = ch.lower()
        if lower in SOUNDEX_IGNORE:
            continue
        if lower in SOUNDEX_VOWELS:
            prev_digit = ""
            continue
        digit = SOUNDEX_MAPPING.get(lower, "")
        if digit and digit != prev_digit:
            code += digit
        prev_digit = digit

    return (code + "000")[:4]


def compute_metaphone(word: str) -> str:
    """Compute the primary Metaphone code.

    Simplified Metaphone (Phil Lawrence, 1990):
    - Initial silent letters dropped (gn/kn/pn/wr)
    - Digraphs mapped via DOUBLE_METAPHONE primary
    - Vowels preserved only at position 0
    - Stops at 4 characters (canonical length)

    Returns "" for empty input.
    """
    if not word:
        return ""

    w = word.upper()
    for prefix, replacement in (("GN", "N"), ("KN", "N"),
                                 ("PN", "N"), ("WR", "R"),
                                 ("WH", "W"), ("AE", "E"),
                                 ("PS", "S")):
        if w.startswith(prefix):
            w = replacement + w[len(prefix):]
            break

    out: list[str] = []
    i = 0
    while i < len(w) and len(out) < 4:
        ch = w[i]
        if not ch.isalpha():
            i += 1
            continue

        if ((i == 0 and ch in METAPHONE_VOWELS) or ch.upper() in "AEIOU") and i == 0:
            out.append(ch)
            i += 1
            continue

        pair = w[i:i + 2]
        triple = w[i:i + 3]

        if triple == "SCH":
            out.append("SK")
            i += 3
            continue
        if pair in DOUBLE_METAPHONE:
            primary = DOUBLE_METAPHONE[pair][0]
            if primary:
                out.append(primary)
            i += 2
            continue
        if ch == "X":
            out.append("KS")
            i += 1
            continue

        if ch not in "AEIOU":
            out.append(ch)
        i += 1

    return "".join(out)[:4]


def compute_double_metaphone(
    word: str,
) -> tuple[str, str]:
    """Compute Double Metaphone (primary, alternate) codes.

    Returns ("", "") for empty input.
    The alternate encodes alternate pronunciations (e.g. Italian vs German).
    """
    if not word:
        return "", ""

    primary = compute_metaphone(word)
    w = word.upper()

    for prefix, replacement in (("GN", "N"), ("KN", "N"),
                                 ("PN", "N"), ("WR", "R"),
                                 ("WH", "W")):
        if w.startswith(prefix):
            w = replacement + w[len(prefix):]
            break

    if w.startswith("AE"):
        alternate_base = "E" + w[2:]
    elif w.startswith("SCH"):
        alternate_base = "X" + w[3:]
    elif w.startswith("X"):
        alternate_base = "Z" + w[1:]
    else:
        alternate_base = w

    alternate_out: list[str] = []
    for ch in alternate_base[:4]:
        if (ch.isalpha() and ch.upper() not in "AEIOU") or (ch.isalpha() and not alternate_out):
            alternate_out.append(ch)

    alternate = "".join(alternate_out)[:4]
    return primary, alternate


def transcribe_to_arpabet(text: str) -> str:
    """Transcribe text to ARPABET phonemes using the CMU dictionary subset.

    For words not in the dictionary, falls back to the word itself (uppercase).
    Multiple words are joined by space; each word is joined by space internally.
    Returns "" for empty input.
    """
    if not text:
        return ""

    words = [w for w in text.upper().split() if w]
    if not words:
        return ""

    out: list[str] = []
    for word in words:
        clean = "".join(c for c in word if c.isalpha())
        if not clean:
            continue
        if clean in CMU_DICT_SUBSET:
            out.append(CMU_DICT_SUBSET[clean][0])
        else:
            out.append(clean)

    return " ".join(out)


def transcribe_to_ipa(text: str) -> str:
    """Transcribe text to IPA using the CMU dictionary subset.

    For dictionary words: maps each ARPABET phoneme to its IPA equivalent.
    For unknown words: falls back to lowercase form.
    Returns "" for empty input.
    """
    if not text:
        return ""

    words = [w for w in text.upper().split() if w]
    if not words:
        return ""

    out: list[str] = []
    for word in words:
        clean = "".join(c for c in word if c.isalpha())
        if not clean:
            continue
        if clean in CMU_DICT_SUBSET:
            arpabet = CMU_DICT_SUBSET[clean][0].split()
            ipa_phonemes: list[str] = []
            for phoneme in arpabet:
                base = phoneme.rstrip("012")
                ipa = ARPABET_TO_IPA.get(base, phoneme)
                ipa_phonemes.append(ipa)
            out.append("".join(ipa_phonemes))
        else:
            out.append(clean.lower())

    return " ".join(out)
