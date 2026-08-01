"""Encoding tables, BOM data, code page mappings.

Covers:
- BOM byte sequences per encoding
- ISO 8859-1 through 8859-16 aliases and coverage
- Windows-1250 through 1258 code page info
- CJK encodings: Shift-JIS, EUC-JP, EUC-KR, GB2312, GB18030, Big5
- Cyrillic: KOI8-R, KOI8-U
- IBM code pages (437, 850, 852, 855, 857, 860, 861, 862, 863, 864, 865, 866, 869)
- Encoding detection confidence thresholds
- Mojibake pattern signatures
- Encoding categories (single-byte, multi-byte, variable-width, stateful)
"""

from __future__ import annotations

from typing import TypedDict

EncodingCategory = str

BOM_SIGNATURES: dict[str, bytes] = {
    "UTF-8":          b"\xef\xbb\xbf",
    "UTF-16-BE":      b"\xfe\xff",
    "UTF-16-LE":      b"\xff\xfe",
    "UTF-32-BE":      b"\x00\x00\xfe\xff",
    "UTF-32-LE":      b"\xff\xfe\x00\x00",
    "UTF-7":          b"\x2b\x2f\x76",
    "UTF-1":          b"\xf7\x64\x4c",
    "SCSU":           b"\x0e\xfe\xff",
    "GB-18030":       b"\x84\x31\x95\x33",
}


BOM_BY_SEQUENCE: dict[bytes, str] = {v: k for k, v in BOM_SIGNATURES.items()}


BOM_SIZE: dict[str, int] = {
    "UTF-8": 3,
    "UTF-16-BE": 2,
    "UTF-16-LE": 2,
    "UTF-32-BE": 4,
    "UTF-32-LE": 4,
}


BOM_REQUIRED_BY_RFC: set[str] = {
    "UTF-16",
    "UTF-16BE",
    "UTF-16LE",
}

BOM_OPTIONAL_BY_RFC: set[str] = {
    "UTF-8",
}


class EncodingInfo(TypedDict):
    name: str
    aliases: list[str]
    category: EncodingCategory
    max_bytes_per_char: int
    is_ascii_compatible: bool
    languages: list[str]


UTF_ENCODINGS: list[EncodingInfo] = [
    {"name": "UTF-8", "aliases": ["utf8", "utf_8", "unicode-1-1-utf-8"],
     "category": "variable-width", "max_bytes_per_char": 4, "is_ascii_compatible": True,
     "languages": ["Universal"]},
    {"name": "UTF-16-BE", "aliases": ["utf-16be", "utf16be"],
     "category": "variable-width", "max_bytes_per_char": 4, "is_ascii_compatible": False,
     "languages": ["Universal"]},
    {"name": "UTF-16-LE", "aliases": ["utf-16le", "utf16le"],
     "category": "variable-width", "max_bytes_per_char": 4, "is_ascii_compatible": False,
     "languages": ["Universal"]},
    {"name": "UTF-32-BE", "aliases": ["utf-32be", "utf32be"],
     "category": "fixed-width", "max_bytes_per_char": 4, "is_ascii_compatible": False,
     "languages": ["Universal"]},
    {"name": "UTF-32-LE", "aliases": ["utf-32le", "utf32le"],
     "category": "fixed-width", "max_bytes_per_char": 4, "is_ascii_compatible": False,
     "languages": ["Universal"]},
    {"name": "UTF-7", "aliases": ["utf7"],
     "category": "stateful", "max_bytes_per_char": 5, "is_ascii_compatible": True,
     "languages": ["Universal"]},
]


SINGLE_BYTE_ENCODINGS: list[EncodingInfo] = [
    {"name": "ISO-8859-1", "aliases": ["latin1", "l1", "IBM819", "CP819", "csISOLatin1"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Western European", "Afrikaans", "Albanian", "Basque", "Catalan",
                   "Danish", "Dutch", "English", "Faroese", "Finnish", "French",
                   "Galician", "German", "Icelandic", "Irish", "Italian", "Norwegian",
                   "Portuguese", "Spanish", "Swedish"]},
    {"name": "ISO-8859-2", "aliases": ["latin2", "l2", "csISOLatin2"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Central/Eastern European", "Bosnian", "Croatian", "Czech",
                   "Hungarian", "Polish", "Romanian", "Serbian (Latin)", "Slovak", "Slovene"]},
    {"name": "ISO-8859-3", "aliases": ["latin3", "l3", "csISOLatin3"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["South European", "Esperanto", "Galician", "Maltese", "Turkish"]},
    {"name": "ISO-8859-4", "aliases": ["latin4", "l4", "csISOLatin4"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["North European", "Estonian", "Latvian", "Lithuanian", "Greenlandic", "Sami"]},
    {"name": "ISO-8859-5", "aliases": ["cyrillic", "csISOLatinCyrillic"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Cyrillic", "Russian", "Belarusian", "Bulgarian", "Macedonian", "Serbian"]},
    {"name": "ISO-8859-6", "aliases": ["arabic", "csISOLatinArabic"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Arabic"]},
    {"name": "ISO-8859-7", "aliases": ["greek", "greek8", "csISOLatinGreek"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Greek"]},
    {"name": "ISO-8859-8", "aliases": ["hebrew", "csISOLatinHebrew"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Hebrew"]},
    {"name": "ISO-8859-9", "aliases": ["latin5", "l5", "csISOLatin5"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Turkish"]},
    {"name": "ISO-8859-10", "aliases": ["latin6", "l6", "csISOLatin6"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Nordic", "Scandinavian"]},
    {"name": "ISO-8859-11", "aliases": ["thai", "csISOLatinThai"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Thai"]},
    {"name": "ISO-8859-13", "aliases": ["latin7", "l7"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Baltic Rim", "Estonian", "Latvian", "Lithuanian"]},
    {"name": "ISO-8859-14", "aliases": ["latin8", "l8", "iso-celtic"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Celtic", "Breton", "Cornish", "Irish", "Manx", "Scottish Gaelic", "Welsh"]},
    {"name": "ISO-8859-15", "aliases": ["latin9", "l9"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Western European", "Euro sign", "OE ligature", "S with caron"]},
    {"name": "ISO-8859-16", "aliases": ["latin10", "l10"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["South-Eastern European", "Albanian", "Croatian", "Hungarian",
                   "Italian", "Polish", "Romanian", "Slovene"]},
]


WINDOWS_CODE_PAGES: list[EncodingInfo] = [
    {"name": "windows-1250", "aliases": ["cp1250", "win1250"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Central European", "Czech", "Hungarian", "Polish", "Slovak", "Slovene"]},
    {"name": "windows-1251", "aliases": ["cp1251", "win1251"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Cyrillic", "Russian", "Bulgarian", "Serbian", "Ukrainian"]},
    {"name": "windows-1252", "aliases": ["cp1252", "win1252", "ANSI"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Western European", "English", "French", "German", "Spanish"]},
    {"name": "windows-1253", "aliases": ["cp1253", "win1253"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Greek"]},
    {"name": "windows-1254", "aliases": ["cp1254", "win1254"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Turkish"]},
    {"name": "windows-1255", "aliases": ["cp1255", "win1255"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Hebrew"]},
    {"name": "windows-1256", "aliases": ["cp1256", "win1256"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Arabic"]},
    {"name": "windows-1257", "aliases": ["cp1257", "win1257"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Baltic", "Estonian", "Latvian", "Lithuanian"]},
    {"name": "windows-1258", "aliases": ["cp1258", "win1258"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Vietnamese"]},
]


CJK_ENCODINGS: list[EncodingInfo] = [
    {"name": "Shift_JIS", "aliases": ["shift-jis", "sjis", "csshiftjis", "x-sjis", "ms_kanji"],
     "category": "multi-byte", "max_bytes_per_char": 2, "is_ascii_compatible": True,
     "languages": ["Japanese"]},
    {"name": "EUC-JP", "aliases": ["euc_jp", "eucjp", "x-euc-jp"],
     "category": "multi-byte", "max_bytes_per_char": 3, "is_ascii_compatible": True,
     "languages": ["Japanese"]},
    {"name": "EUC-KR", "aliases": ["euc_kr", "euckr", "ksc5601", "ks_x_1001"],
     "category": "multi-byte", "max_bytes_per_char": 2, "is_ascii_compatible": True,
     "languages": ["Korean"]},
    {"name": "GB2312", "aliases": ["gb2312-80", "gb2312-1980", "euc-cn", "euccn"],
     "category": "multi-byte", "max_bytes_per_char": 2, "is_ascii_compatible": True,
     "languages": ["Chinese (Simplified)"]},
    {"name": "GB18030", "aliases": ["gb18030-2000", "gb18030-2005"],
     "category": "multi-byte", "max_bytes_per_char": 4, "is_ascii_compatible": True,
     "languages": ["Chinese (Simplified + Traditional + minority)"]},
    {"name": "Big5", "aliases": ["big5-tw", "csbig5", "big5-hkscs"],
     "category": "multi-byte", "max_bytes_per_char": 2, "is_ascii_compatible": True,
     "languages": ["Chinese (Traditional)"]},
]


CYRILLIC_ENCODINGS: list[EncodingInfo] = [
    {"name": "KOI8-R", "aliases": ["koi8_r", "koi8-r", "csKOI8R"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Russian", "Bulgarian"]},
    {"name": "KOI8-U", "aliases": ["koi8_u", "koi8-u", "koi8-ukrainian"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Ukrainian"]},
]


IBM_CODE_PAGES: list[EncodingInfo] = [
    {"name": "IBM437", "aliases": ["cp437", "437", "ibm437", "cspc8codepage437"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["English", "DOS-US", "CP437"]},
    {"name": "IBM850", "aliases": ["cp850", "850", "ibm850", "cspc850multilingual"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Western European", "Multilingual (Latin-1)"]},
    {"name": "IBM852", "aliases": ["cp852", "852", "ibm852", "cspcp852"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Central European (Latin-2)"]},
    {"name": "IBM855", "aliases": ["cp855", "855", "ibm855", "csIBM855"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Cyrillic"]},
    {"name": "IBM857", "aliases": ["cp857", "857", "ibm857", "csIBM857"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Turkish"]},
    {"name": "IBM860", "aliases": ["cp860", "860", "ibm860", "csIBM860"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Portuguese"]},
    {"name": "IBM861", "aliases": ["cp861", "861", "ibm861", "csIBM861"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Icelandic"]},
    {"name": "IBM862", "aliases": ["cp862", "862", "ibm862", "cspc862latinhebrew"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Hebrew"]},
    {"name": "IBM863", "aliases": ["cp863", "863", "ibm863", "csIBM863"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Canadian French"]},
    {"name": "IBM864", "aliases": ["cp864", "864", "ibm864", "csIBM864"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Arabic"]},
    {"name": "IBM865", "aliases": ["cp865", "865", "ibm865", "csIBM865"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Nordic", "Danish", "Norwegian"]},
    {"name": "IBM866", "aliases": ["cp866", "866", "ibm866", "csIBM866"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Cyrillic (Russian)"]},
    {"name": "IBM869", "aliases": ["cp869", "869", "ibm869", "csIBM869"],
     "category": "single-byte", "max_bytes_per_char": 1, "is_ascii_compatible": True,
     "languages": ["Greek"]},
]


CHARDET_CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "entry": 0.20,
    "usable": 0.50,
    "reliable": 0.80,
    "trusted": 0.95,
}


MOJIBAKE_SIGNATURES: dict[str, list[str]] = {
    "UTF-8 viewed as ISO-8859-1": [
        "\u00c3\u00a9", "\u00c3\u00a1", "\u00c3\u00b3",
        "\u00c3\u00ba", "\u00c3\u00b1", "\u00c2\u00a9", "\u00c2\u00bf",
    ],
    "ISO-8859-1 viewed as UTF-8": [
        "\ufffd", "\ufffd\ufffd",
    ],
    "UTF-8 viewed as Windows-1252": [
        "\u00c3\u00a9", "\u00c3\u00a1", "\u00c3\u00b3",
        "\u00c3\u00ba", "\u00c3\u00b1", "\u00c2\u00a9", "\u00c2\u00bf",
    ],
    "Shift_JIS viewed as ISO-8859-1": [
        "\u0082", "\u0083", "\u008a", "\u008c", "\u008e",
    ],
    "EUC-KR viewed as ISO-8859-1": [
        "\u00a1\u00fe", "\u00a2", "\u00a4", "\u00a7",
    ],
    "GB2312 viewed as Windows-1252": [
        "\u0081\u0084", "\u00d6\u00d0", "\u00b9\u00fa",
    ],
}


ALL_ENCODINGS: list[EncodingInfo] = (
    UTF_ENCODINGS + SINGLE_BYTE_ENCODINGS + WINDOWS_CODE_PAGES +
    CJK_ENCODINGS + CYRILLIC_ENCODINGS + IBM_CODE_PAGES
)
