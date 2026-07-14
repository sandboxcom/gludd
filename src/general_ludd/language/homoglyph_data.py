"""Confusable character mappings, invisible character detection.

Covers:
- UTS #39 Unicode Security Mechanisms confusables
- Homoglyph categories: Latin/Cyrillic/Greek/Armenian lookalikes
- Invisible character codepoints (zero-width, bidi control, soft hyphen)
- Mixed-script detection utilities
- Skeleton generation heuristics
- Attack vector categories (domain spoofing, code injection, filename confusion)
"""

from __future__ import annotations

from typing import Literal, TypedDict


class HomoglyphGroup(TypedDict):
    skeleton: str
    characters: list[tuple[int, str]]
    categories: list[str]


InvisibleCategory = Literal[
    "zero-width-space",
    "zero-width-joiner",
    "zero-width-non-joiner",
    "soft-hyphen",
    "word-joiner",
    "bidi-control",
    "format-character",
    "deprecated-format",
    "interlinear-annotation",
    "variation-selector",
]


class InvisibleChar(TypedDict):
    codepoint: int
    name: str
    short_name: str
    category: InvisibleCategory
    risk: str
    cve_reference: str


HOMOGLYPH_GROUPS: list[HomoglyphGroup] = [
    {
        "skeleton": "A",
        "characters": [
            (0x0041, "LATIN CAPITAL LETTER A"),
            (0x0391, "GREEK CAPITAL LETTER ALPHA"),
            (0x0410, "CYRILLIC CAPITAL LETTER A"),
            (0x0531, "ARMENIAN CAPITAL LETTER AYB"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic", "Armenian"],
    },
    {
        "skeleton": "B",
        "characters": [
            (0x0042, "LATIN CAPITAL LETTER B"),
            (0x0392, "GREEK CAPITAL LETTER BETA"),
            (0x0412, "CYRILLIC CAPITAL LETTER VE"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "C",
        "characters": [
            (0x0043, "LATIN CAPITAL LETTER C"),
            (0x0421, "CYRILLIC CAPITAL LETTER ES"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "E",
        "characters": [
            (0x0045, "LATIN CAPITAL LETTER E"),
            (0x0395, "GREEK CAPITAL LETTER EPSILON"),
            (0x0415, "CYRILLIC CAPITAL LETTER IE"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "H",
        "characters": [
            (0x0048, "LATIN CAPITAL LETTER H"),
            (0x0397, "GREEK CAPITAL LETTER ETA"),
            (0x041D, "CYRILLIC CAPITAL LETTER EN"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "I",
        "characters": [
            (0x0049, "LATIN CAPITAL LETTER I"),
            (0x0399, "GREEK CAPITAL LETTER IOTA"),
            (0x0406, "CYRILLIC CAPITAL LETTER BYELORUSSIAN-UKRAINIAN I"),
            (0x007C, "VERTICAL LINE"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "K",
        "characters": [
            (0x004B, "LATIN CAPITAL LETTER K"),
            (0x039A, "GREEK CAPITAL LETTER KAPPA"),
            (0x041A, "CYRILLIC CAPITAL LETTER KA"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "M",
        "characters": [
            (0x004D, "LATIN CAPITAL LETTER M"),
            (0x039C, "GREEK CAPITAL LETTER MU"),
            (0x041C, "CYRILLIC CAPITAL LETTER EM"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "O",
        "characters": [
            (0x004F, "LATIN CAPITAL LETTER O"),
            (0x039F, "GREEK CAPITAL LETTER OMICRON"),
            (0x041E, "CYRILLIC CAPITAL LETTER O"),
            (0x0030, "DIGIT ZERO"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic", "Digit"],
    },
    {
        "skeleton": "P",
        "characters": [
            (0x0050, "LATIN CAPITAL LETTER P"),
            (0x03A1, "GREEK CAPITAL LETTER RHO"),
            (0x0420, "CYRILLIC CAPITAL LETTER ER"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "T",
        "characters": [
            (0x0054, "LATIN CAPITAL LETTER T"),
            (0x03A4, "GREEK CAPITAL LETTER TAU"),
            (0x0422, "CYRILLIC CAPITAL LETTER TE"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "X",
        "characters": [
            (0x0058, "LATIN CAPITAL LETTER X"),
            (0x039E, "GREEK CAPITAL LETTER CHI"),
            (0x0425, "CYRILLIC CAPITAL LETTER HA"),
        ],
        "categories": ["Latin", "Greek", "Cyrillic"],
    },
    {
        "skeleton": "Z",
        "characters": [
            (0x005A, "LATIN CAPITAL LETTER Z"),
            (0x0396, "GREEK CAPITAL LETTER ZETA"),
        ],
        "categories": ["Latin", "Greek"],
    },
    {
        "skeleton": "a",
        "characters": [
            (0x0061, "LATIN SMALL LETTER A"),
            (0x0430, "CYRILLIC SMALL LETTER A"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "c",
        "characters": [
            (0x0063, "LATIN SMALL LETTER C"),
            (0x0441, "CYRILLIC SMALL LETTER ES"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "e",
        "characters": [
            (0x0065, "LATIN SMALL LETTER E"),
            (0x0435, "CYRILLIC SMALL LETTER IE"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "o",
        "characters": [
            (0x006F, "LATIN SMALL LETTER O"),
            (0x043E, "CYRILLIC SMALL LETTER O"),
            (0x03BF, "GREEK SMALL LETTER OMICRON"),
        ],
        "categories": ["Latin", "Cyrillic", "Greek"],
    },
    {
        "skeleton": "p",
        "characters": [
            (0x0070, "LATIN SMALL LETTER P"),
            (0x0440, "CYRILLIC SMALL LETTER ER"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "x",
        "characters": [
            (0x0078, "LATIN SMALL LETTER X"),
            (0x0445, "CYRILLIC SMALL LETTER HA"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "y",
        "characters": [
            (0x0079, "LATIN SMALL LETTER Y"),
            (0x0443, "CYRILLIC SMALL LETTER U"),
        ],
        "categories": ["Latin", "Cyrillic"],
    },
    {
        "skeleton": "l",
        "characters": [
            (0x006C, "LATIN SMALL LETTER L"),
            (0x0031, "DIGIT ONE"),
            (0x007C, "VERTICAL LINE"),
        ],
        "categories": ["Latin", "Digit", "Symbol"],
    },
    {
        "skeleton": "0",
        "characters": [
            (0x0030, "DIGIT ZERO"),
            (0x004F, "LATIN CAPITAL LETTER O"),
            (0x041E, "CYRILLIC CAPITAL LETTER O"),
        ],
        "categories": ["Digit", "Latin", "Cyrillic"],
    },
]

INVISIBLE_CHARACTERS: list[InvisibleChar] = [
    {
        "codepoint": 0x200B, "name": "ZERO WIDTH SPACE",
        "short_name": "ZWSP", "category": "zero-width-space",
        "risk": "Can be inserted in identifiers, URLs, and filenames to create"
                " visually identical but distinct strings",
        "cve_reference": "",
    },
    {
        "codepoint": 0x200C, "name": "ZERO WIDTH NON-JOINER",
        "short_name": "ZWNJ", "category": "zero-width-non-joiner",
        "risk": "Used legitimately in scripts like Persian/Arabic; can be"
                " abused to split identifiers in source code",
        "cve_reference": "",
    },
    {
        "codepoint": 0x200D, "name": "ZERO WIDTH JOINER",
        "short_name": "ZWJ", "category": "zero-width-joiner",
        "risk": "Used legitimately for emoji sequences and Indic scripts; can"
                " create invisible differences in strings",
        "cve_reference": "",
    },
    {
        "codepoint": 0x00AD, "name": "SOFT HYPHEN",
        "short_name": "SHY", "category": "soft-hyphen",
        "risk": "Invisible line-break opportunity; can split identifiers"
                " that appear continuous",
        "cve_reference": "",
    },
    {
        "codepoint": 0x2060, "name": "WORD JOINER",
        "short_name": "WJ", "category": "word-joiner",
        "risk": "Zero-width no-break; prevents line breaks invisibly; can"
                " create hidden concatenation in source",
        "cve_reference": "",
    },
    {
        "codepoint": 0x202A, "name": "LEFT-TO-RIGHT EMBEDDING",
        "short_name": "LRE", "category": "bidi-control",
        "risk": "Bidi override can reverse visual order of text, hiding"
                " executable code or misleading URIs",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x202B, "name": "RIGHT-TO-LEFT EMBEDDING",
        "short_name": "RLE", "category": "bidi-control",
        "risk": "Same as LRE but for RTL context; primary vector for"
                " Trojan Source attacks",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x202C, "name": "POP DIRECTIONAL FORMATTING",
        "short_name": "PDF", "category": "bidi-control",
        "risk": "Terminates bidi override; used to bracket malicious"
                " text in Trojan Source attacks",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x202D, "name": "LEFT-TO-RIGHT OVERRIDE",
        "short_name": "LRO", "category": "bidi-control",
        "risk": "Forces LTR direction; can hide RTL characters that"
                " reverse code semantics visually",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x202E, "name": "RIGHT-TO-LEFT OVERRIDE",
        "short_name": "RLO", "category": "bidi-control",
        "risk": "Primary Trojan Source attack vector: reverses visual"
                " order of characters in source code",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x2066, "name": "LEFT-TO-RIGHT ISOLATE",
        "short_name": "LRI", "category": "bidi-control",
        "risk": "Isolates text segment for LTR; less dangerous than"
                " override but still a bidi control",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x2067, "name": "RIGHT-TO-LEFT ISOLATE",
        "short_name": "RLI", "category": "bidi-control",
        "risk": "Isolates text segment for RTL; can hide comments"
                " or code in a mixed-direction source file",
        "cve_reference": "CVE-2021-42574",
    },
    {
        "codepoint": 0x2068, "name": "FIRST STRONG ISOLATE",
        "short_name": "FSI", "category": "bidi-control",
        "risk": "First-strong directional isolate; contextual bidi"
                " that can be exploited in mixed-script contexts",
        "cve_reference": "",
    },
    {
        "codepoint": 0x2069, "name": "POP DIRECTIONAL ISOLATE",
        "short_name": "PDI", "category": "bidi-control",
        "risk": "Terminates directional isolate; used to bracket"
                " malicious bidi sequences",
        "cve_reference": "",
    },
    {
        "codepoint": 0xFEFF, "name": "ZERO WIDTH NO-BREAK SPACE / BOM",
        "short_name": "BOM/ZWNBSP", "category": "format-character",
        "risk": "When used as ZWNBSP (not BOM), creates invisible"
                " no-break space in text; can break parsing",
        "cve_reference": "",
    },
    {
        "codepoint": 0x200E, "name": "LEFT-TO-RIGHT MARK",
        "short_name": "LRM", "category": "bidi-control",
        "risk": "Invisible directional marker; can manipulate"
                " bidirectional text rendering",
        "cve_reference": "",
    },
    {
        "codepoint": 0x200F, "name": "RIGHT-TO-LEFT MARK",
        "short_name": "RLM", "category": "bidi-control",
        "risk": "Invisible directional marker; can manipulate"
                " bidirectional text rendering",
        "cve_reference": "",
    },
    {
        "codepoint": 0x061C, "name": "ARABIC LETTER MARK",
        "short_name": "ALM", "category": "bidi-control",
        "risk": "Arabic-script directional marker; can be used"
                " for bidi spoofing in Arabic text",
        "cve_reference": "",
    },
    {
        "codepoint": 0x180E, "name": "MONGOLIAN VOWEL SEPARATOR",
        "short_name": "MVS", "category": "zero-width-space",
        "risk": "Zero-width separator in Mongolian; can be"
                " abused as invisible space",
        "cve_reference": "",
    },
    {
        "codepoint": 0x034F, "name": "COMBINING GRAPHEME JOINER",
        "short_name": "CGJ", "category": "format-character",
        "risk": "Invisible combining mark; affects grapheme"
                " cluster boundaries and collation",
        "cve_reference": "",
    },
]

ATTACK_VECTORS: dict[str, str] = {
    "domain_spoofing": (
        "Replacing ASCII characters with visually identical Unicode"
        " characters from other scripts in domain names. Example:"
        " apple.com with Cyrillic 'a' (U+0430)"
    ),
    "code_injection": (
        "Using bidi control characters (CVE-2021-42574) to reverse"
        " the visual order of characters in source code, making"
        " malicious logic appear benign during code review"
    ),
    "filename_confusion": (
        "Using homoglyphs or zero-width characters in filenames"
        " to create files that appear identical but have different"
        " content or extensions"
    ),
    "string_comparison_bypass": (
        "Using zero-width characters or homoglyphs to bypass"
        " string comparison / blocklist checks while appearing"
        " legitimate to human readers"
    ),
    "comment_out_out-of-context": (
        "Using bidi overrides to make code look commented out"
        " when it is actually executed, or to hide code inside"
        " what appears to be a comment"
    ),
    "package_typosquatting": (
        "Using visually identical characters in package names"
        " to impersonate legitimate packages in registries"
    ),
}


def _codepoint_in_group(cp: int, groups: list[HomoglyphGroup]) -> str:
    for group in groups:
        for entry_cp, _name in group["characters"]:
            if entry_cp == cp:
                return group["skeleton"]
    return ""


def _invisible_codepoints() -> set[int]:
    return {c["codepoint"] for c in INVISIBLE_CHARACTERS}


_INVISIBLE_SET: set[int] = _invisible_codepoints()
