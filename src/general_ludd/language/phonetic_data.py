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
